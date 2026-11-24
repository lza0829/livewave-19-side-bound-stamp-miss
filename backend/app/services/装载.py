import json

from app.db import 连接
from app.modules import 旁路界戳
from app.seed import 此刻
from app.services import 时间, 热度窗, 超迟到旁路


class 入流闸失败(Exception):
    """入流闸在事务内自检失败：整批回滚，宁可失败也不落半截。"""


def _自检(conn, 涉及窗键: set[str] | None = None) -> list[str]:
    问题 = []

    脏旁路 = conn.execute(
        "SELECT COUNT(*) FROM 互动事件 WHERE 命运='已旁路' AND 窗键<>''"
    ).fetchone()[0]
    if 脏旁路:
        问题.append(f"旁路事件仍挂窗键:{脏旁路}")

    半截 = conn.execute(
        """SELECT COUNT(*) FROM 旁路匣 b JOIN 互动事件 e ON e.事件编号=b.事件编号
           WHERE b.升格状态='待升格' AND (e.命运<>'已旁路' OR e.窗键<>'')"""
    ).fetchone()[0]
    if 半截:
        问题.append(f"待升格旁路行对应事件已入窗:{半截}")

    孤立 = conn.execute(
        """SELECT COUNT(*) FROM 互动事件 e
           WHERE e.命运='已旁路'
             AND NOT EXISTS (SELECT 1 FROM 旁路匣 b WHERE b.事件编号=e.事件编号)"""
    ).fetchone()[0]
    if 孤立:
        问题.append(f"已旁路事件未落匣:{孤立}")

    空戳 = conn.execute(
        """SELECT COUNT(*) FROM 旁路匣 b
           WHERE b.升格状态 IN ('待升格','已升格') AND b.事件编号<>''
             AND NOT EXISTS (SELECT 1 FROM 旁路界戳 s WHERE s.事件编号=b.事件编号)"""
    ).fetchone()[0]
    if 空戳:
        问题.append(f"旁路成功戳空:{空戳}")

    戳双记 = conn.execute(
        """SELECT COUNT(*) FROM 旁路界戳 s JOIN 互动事件 e ON e.事件编号=s.事件编号
           WHERE s.戳态 IN ('旁路','待解旁路') AND e.命运='已入窗'"""
    ).fetchone()[0]
    if 戳双记:
        问题.append(f"同事件既在匣又改累计:{戳双记}")

    已升格缺窗 = conn.execute(
        """SELECT COUNT(*) FROM 旁路匣 b JOIN 互动事件 e ON e.事件编号=b.事件编号
           WHERE b.升格状态='已升格' AND (e.命运<>'已入窗' OR e.窗键<>b.窗键)"""
    ).fetchone()[0]
    if 已升格缺窗:
        问题.append(f"已升格旁路行与事件不一致:{已升格缺窗}")

    窗们 = conn.execute("SELECT * FROM 热度窗 WHERE 状态='已关闭'").fetchall()
    for 窗 in 窗们:
        if 涉及窗键 is not None and 窗["窗键"] not in 涉及窗键:
            continue
        合计 = conn.execute(
            """SELECT COALESCE(SUM(点赞增量),0) AS 赞, COALESCE(SUM(送礼额),0) AS 送, COUNT(*) AS n
               FROM 互动事件 WHERE 窗键=? AND 命运='已入窗'""",
            (窗["窗键"],),
        ).fetchone()
        if 合计["赞"] != 窗["点赞合计"] or 合计["送"] != 窗["送礼合计"] or 合计["n"] != 窗["事件笔数"]:
            问题.append(
                f"窗{窗['窗键']}半截: 表{窗['点赞合计']}/{窗['送礼合计']}/{窗['事件笔数']} "
                f"vs 事件{合计['赞']}/{合计['送']}/{合计['n']}"
            )
    return 问题


def _落未知房(conn, ev: dict, 报告: dict) -> None:
    处理时 = ev.get("处理时") or 此刻()
    编号 = 超迟到旁路.落匣(
        conn, ev, ev.get("房间键") or "", "", "", "", "", "", 0, 处理时,
        原因="未知直播间", 升格状态="不可升格", 备注="房间不存在，仅隔离留存",
    )
    报告["旁路"] += 1
    报告["明细"].append(
        {"事件编号": ev.get("事件编号"), "命运": "旁路-未知房", "旁路编号": 编号}
    )


def _落非法(conn, ev: dict, 报告: dict, 原因: str) -> None:
    """缺字段/时刻非法的入流一律进旁路匣隔离，禁止静默丢弃，绝不改窗。"""
    处理时 = ev.get("处理时") or 此刻()
    编号 = 超迟到旁路.落匣(
        conn, ev, str(ev.get("房间键") or ""), "", "", "", "", "", 0, 处理时,
        原因="非法事件", 升格状态="不可升格", 备注=原因,
    )
    报告["旁路"] += 1
    报告["明细"].append(
        {"事件编号": ev.get("事件编号"), "命运": "旁路-非法", "原因": 原因, "旁路编号": 编号}
    )


def _消化已旁路迟到写(conn, 事件编号: str, 处理时: str, 报告: dict) -> None:
    """迟到写闸：命中已有旁路界戳的事件，按全局架构消化，绝不改正式累计。

    - 甲：整票拒绝（抛界戳违规 → 整批回滚，戳态/待办/累计都不变）；
    - 乙：戳态推进为「待解旁路」并落待办，累计不变；
    - 已解（已入窗）后重放：幂等跳过，既不拒绝也不改累计。
    """
    果 = 旁路界戳.消化迟到写(conn, 事件编号, 处理时)
    if 果["戳态"] == "已解" or 果["命运"] == "幂等跳过":
        报告["明细"].append({"事件编号": 事件编号, "命运": "幂等跳过"})
        return
    报告["迟到写拒绝"] += 1  # 该请求未改正式累计
    if 果["戳态"] == "待解旁路":
        报告["待解旁路"] += 1
    报告["明细"].append(
        {
            "事件编号": 事件编号,
            "命运": 果["命运"],
            "戳态": 果["戳态"],
            "改累计": 果["改累计"],
            "落待办": 果["落待办"],
            "工单号": 果.get("工单号", ""),
        }
    )


def 装一批(体: dict) -> dict:
    """入流一批互动事件：推进水位线，尝试关窗，处理迟到/旁路/撤回。

    口径（类 Flink）：
    - 水位 = 已见最大事件时，只进不退
    - 窗 [起,止) 在 水位>=止 时关闭发射
    - 关闭后至 水位>=止+允许迟到 仍可迟到修正并发撤回
    - 水位>=止+允许迟到 → 超迟到旁路：只写旁路匣，绝不改窗（本工程为乙-可升格暂存）
    - 旁路成功后同票打旁路界戳；迟到写命中已打戳事件 → 按架构消化，绝不改累计
    - 整批在 BEGIN IMMEDIATE 内完成；自检失败整批回滚（失败闭）
    """
    批次编号 = 体.get("批次编号") or f"批-{此刻().replace(':', '').replace('-', '')}"
    事件列表 = 体.get("事件") or []
    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        报告 = {
            "批次编号": 批次编号,
            "入流": 0,
            "入窗": 0,
            "旁路": 0,
            "撤回": 0,
            "新关窗": 0,
            "迟到写拒绝": 0,
            "待解旁路": 0,
            "明细": [],
        }
        涉及窗键: set[str] = set()
        窗长, 默许迟到 = 超迟到旁路.窗参数(conn)

        for ev in 事件列表:
            if not isinstance(ev, dict) or not ev.get("事件编号") or not ev.get("事件时"):
                _落非法(conn, ev if isinstance(ev, dict) else {}, 报告, "缺事件编号/事件时")
                continue

            房间键 = ev.get("房间键") or ""
            房 = conn.execute("SELECT * FROM 直播间 WHERE 房间键=?", (房间键,)).fetchone()
            if not 房:
                _落未知房(conn, ev, 报告)
                continue

            水位 = conn.execute("SELECT * FROM 水位线 WHERE 房间键=?", (房间键,)).fetchone()
            if not 水位:
                conn.execute(
                    "INSERT INTO 水位线(房间键, 事件时水位, 允许迟到秒, 更新处理时) VALUES (?,?,?,?)",
                    (房间键, 房["开播事件时"], 默许迟到, 此刻()),
                )
                水位 = conn.execute("SELECT * FROM 水位线 WHERE 房间键=?", (房间键,)).fetchone()

            允许迟到 = int(水位["允许迟到秒"])
            事件时 = ev["事件时"]
            处理时 = ev.get("处理时") or 此刻()
            事件编号 = ev["事件编号"]
            点赞 = int(ev.get("点赞增量") or 0)
            送礼 = int(ev.get("送礼额") or 0)
            类型 = ev.get("事件类型") or "点赞"

            try:
                时间.解析(事件时)
                时间.解析(处理时)
            except (ValueError, TypeError):
                _落非法(conn, ev, 报告, "事件时/处理时格式非法")
                continue

            # 迟到写闸必须先于幂等跳过与入窗：有界戳的事件只能按架构消化。
            if 旁路界戳.有戳(conn, 事件编号):
                _消化已旁路迟到写(conn, 事件编号, 处理时, 报告)
                continue

            if conn.execute("SELECT 1 FROM 互动事件 WHERE 事件编号=?", (事件编号,)).fetchone():
                报告["明细"].append({"事件编号": 事件编号, "命运": "幂等跳过"})
                continue

            当前水位 = 水位["事件时水位"]
            if 时间.解析(事件时) > 时间.解析(当前水位):
                当前水位 = 事件时
                conn.execute(
                    "UPDATE 水位线 SET 事件时水位=?, 更新处理时=? WHERE 房间键=?",
                    (当前水位, 处理时, 房间键),
                )
            报告["新关窗"] += 热度窗.尝试关窗(
                conn, 房间键, 当前水位, 允许迟到, 窗长, 处理时
            )

            wk, 窗起, 窗止, 旁路界, 已过 = 超迟到旁路.判界(
                事件时, 房间键, 窗长, 允许迟到, 当前水位
            )
            涉及窗键.add(wk)

            if 已过:
                conn.execute(
                    """INSERT INTO 互动事件(事件编号,房间键,事件类型,事件时,处理时,观众键,点赞增量,送礼额,命运,窗键,批次编号)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        事件编号, 房间键, 类型, 事件时, 处理时,
                        ev.get("观众键") or "", 点赞, 送礼, "已旁路", "", 批次编号,
                    ),
                )
                编号 = 超迟到旁路.落匣(
                    conn, ev, 房间键, wk, 窗起, 窗止, 旁路界, 当前水位,
                    允许迟到, 处理时,
                )
                # 旁路成功必须同票打戳：戳要素缺失即抛错，整批回滚（禁止戳空）。
                旁路界戳.打戳(
                    conn,
                    事件编号=事件编号,
                    窗键=wk,
                    水位快照=当前水位,
                    旁路界=旁路界,
                    处理时=处理时,
                    允许迟到秒=允许迟到,
                )
                报告["旁路"] += 1
                报告["明细"].append(
                    {"事件编号": 事件编号, "命运": "已旁路", "窗键": wk, "旁路编号": 编号,
                     "已打戳": True}
                )
                continue

            窗 = conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (wk,)).fetchone()
            if not 窗:
                conn.execute(
                    """INSERT INTO 热度窗(窗键,房间键,窗起事件时,窗止事件时,状态,点赞合计,送礼合计,事件笔数,版本)
                       VALUES (?,?,?,?, '开启', 0, 0, 0, 0)""",
                    (wk, 房间键, 窗起, 窗止),
                )
                窗 = conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (wk,)).fetchone()

            if 窗["状态"] != "已关闭" and 时间.解析(当前水位) >= 时间.解析(窗止):
                报告["新关窗"] += 热度窗.尝试关窗(
                    conn, 房间键, 当前水位, 允许迟到, 窗长, 处理时
                )
                窗 = conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (wk,)).fetchone()

            if 窗["状态"] == "已关闭":
                旧赞, 旧送 = 窗["点赞合计"], 窗["送礼合计"]
                新赞, 新送 = 旧赞 + 点赞, 旧送 + 送礼
                新版本 = int(窗["版本"]) + 1
                conn.execute(
                    """UPDATE 热度窗 SET 点赞合计=?, 送礼合计=?, 事件笔数=事件笔数+1, 版本=? WHERE 窗键=?""",
                    (新赞, 新送, 新版本, wk),
                )
                conn.execute(
                    """INSERT INTO 撤回单(窗键,房间键,原因,旧点赞,新点赞,旧送礼,新送礼,处理时,事件编号)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (wk, 房间键, "迟到修正", 旧赞, 新赞, 旧送, 新送, 处理时, 事件编号),
                )
                命运 = "迟到修正撤回"
                报告["撤回"] += 1
            else:
                conn.execute(
                    """UPDATE 热度窗 SET 点赞合计=点赞合计+?, 送礼合计=送礼合计+?, 事件笔数=事件笔数+1 WHERE 窗键=?""",
                    (点赞, 送礼, wk),
                )
                命运 = "已入窗"

            conn.execute(
                """INSERT INTO 互动事件(事件编号,房间键,事件类型,事件时,处理时,观众键,点赞增量,送礼额,命运,窗键,批次编号)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    事件编号, 房间键, 类型, 事件时, 处理时,
                    ev.get("观众键") or "", 点赞, 送礼, "已入窗", wk, 批次编号,
                ),
            )
            报告["入窗"] += 1
            报告["入流"] += 1
            报告["明细"].append({"事件编号": 事件编号, "命运": 命运, "窗键": wk})

        收尾处理时 = next(
            (ev.get("处理时") for ev in reversed(事件列表)
             if isinstance(ev, dict) and ev.get("处理时")),
            此刻(),
        )
        for 房行 in conn.execute("SELECT 房间键, 事件时水位, 允许迟到秒 FROM 水位线"):
            报告["新关窗"] += 热度窗.尝试关窗(
                conn, 房行["房间键"], 房行["事件时水位"], int(房行["允许迟到秒"]), 窗长,
                收尾处理时,
            )

        问题 = _自检(conn, 涉及窗键)
        if 问题:
            报告["自检问题"] = 问题
            raise 入流闸失败("；".join(问题)) from None

        存编号 = 批次编号
        序 = 1
        while conn.execute("SELECT 1 FROM 装载批次 WHERE 编号=?", (存编号,)).fetchone():
            序 += 1
            存编号 = f"{批次编号}#{序}"
        conn.execute(
            "INSERT INTO 装载批次(编号, 时刻, 报告) VALUES (?,?,?)",
            (存编号, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.execute("COMMIT")
        return 报告
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def 装夹具(名: str) -> dict:
    from app.db import 夹具目录

    路径 = 夹具目录() / f"{名}.json"
    if not 路径.exists():
        raise FileNotFoundError(f"夹具不存在:{名}")
    return 装一批(json.loads(路径.read_text(encoding="utf-8")))
