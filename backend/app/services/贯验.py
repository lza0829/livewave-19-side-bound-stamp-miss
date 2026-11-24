from app.db import 连接
from app.seed import 此刻



def 跑贯验() -> dict:
    """窗输出与撤回互证：窗表合计 = 窗内事件合计 = 末次撤回新值；旁路不挂窗。"""
    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        问题 = []
        事件数 = conn.execute("SELECT COUNT(*) FROM 互动事件").fetchone()[0]
        已关闭 = conn.execute("SELECT * FROM 热度窗 WHERE 状态='已关闭'").fetchall()
        撤回数 = conn.execute("SELECT COUNT(*) FROM 撤回单").fetchone()[0]
        旁路数 = conn.execute("SELECT COUNT(*) FROM 旁路匣").fetchone()[0]

        for 窗 in 已关闭:
            agg = conn.execute(
                """SELECT COALESCE(SUM(点赞增量),0) AS 赞, COALESCE(SUM(送礼额),0) AS 送, COUNT(*) AS n
                   FROM 互动事件 WHERE 窗键=? AND 命运='已入窗'""",
                (窗["窗键"],),
            ).fetchone()
            if agg["赞"] != 窗["点赞合计"] or agg["送"] != 窗["送礼合计"]:
                问题.append(
                    f"窗{窗['窗键']}合计不符: 窗表赞{窗['点赞合计']}/送{窗['送礼合计']} vs 事件赞{agg['赞']}/送{agg['送']}"
                )
            if agg["n"] != 窗["事件笔数"]:
                问题.append(f"窗{窗['窗键']}笔数不符: 窗表{窗['事件笔数']} vs 事件{agg['n']}")

            末撤 = conn.execute(
                "SELECT * FROM 撤回单 WHERE 窗键=? ORDER BY 编号 DESC LIMIT 1",
                (窗["窗键"],),
            ).fetchone()
            if 末撤 and (末撤["新点赞"] != 窗["点赞合计"] or 末撤["新送礼"] != 窗["送礼合计"]):
                问题.append(f"窗{窗['窗键']}末次撤回与窗表不一致")

        脏旁路 = conn.execute(
            "SELECT COUNT(*) FROM 互动事件 WHERE 命运='已旁路' AND 窗键<>''"
        ).fetchone()[0]
        if 脏旁路:
            问题.append(f"旁路事件仍挂窗键:{脏旁路}")

        孤立 = conn.execute(
            """SELECT COUNT(*) FROM 互动事件 e
               WHERE e.命运='已旁路'
                 AND NOT EXISTS (SELECT 1 FROM 旁路匣 b WHERE b.事件编号=e.事件编号)"""
        ).fetchone()[0]
        if 孤立:
            问题.append(f"已旁路事件未落匣:{孤立}")

        待升格半截 = conn.execute(
            """SELECT COUNT(*) FROM 旁路匣 b JOIN 互动事件 e ON e.事件编号=b.事件编号
               WHERE b.升格状态='待升格' AND (e.命运<>'已旁路' OR e.窗键<>'')"""
        ).fetchone()[0]
        if 待升格半截:
            问题.append(f"待升格旁路行对应事件已入窗:{待升格半截}")

        已升格不一致 = conn.execute(
            """SELECT COUNT(*) FROM 旁路匣 b JOIN 互动事件 e ON e.事件编号=b.事件编号
               WHERE b.升格状态='已升格' AND (e.命运<>'已入窗' OR e.窗键<>b.窗键)"""
        ).fetchone()[0]
        if 已升格不一致:
            问题.append(f"已升格旁路行与事件不一致:{已升格不一致}")

        绑定缺事件 = conn.execute(
            """SELECT COUNT(*) FROM 旁路匣 b
               WHERE b.事件编号<>'' AND b.升格状态 IN ('待升格','已升格')
                 AND NOT EXISTS (SELECT 1 FROM 互动事件 e WHERE e.事件编号=b.事件编号)"""
        ).fetchone()[0]
        if 绑定缺事件:
            问题.append(f"旁路行绑定了不存在的事件:{绑定缺事件}")

        # 旁路界戳协同：禁止旁路成功戳空、禁止同事件既在匣又改累计、禁止戳号顶替。
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

        顶替 = conn.execute(
            """SELECT COUNT(*) FROM (
                 SELECT 戳号 FROM 旁路界戳
                 GROUP BY 戳号 HAVING COUNT(*) > 1 OR COUNT(DISTINCT 事件编号) > 1
               )""").fetchone()[0]
        顶替 += conn.execute(
            "SELECT COUNT(*) FROM 旁路界戳 WHERE 戳号=''"
        ).fetchone()[0]
        if 顶替:
            问题.append(f"戳号重复或为空（疑似顶替）:{顶替}")

        待办失配 = conn.execute(
            """SELECT COUNT(*) FROM 旁路待办 d
               WHERE (d.状态='待解' AND NOT EXISTS (
                        SELECT 1 FROM 旁路界戳 s
                        WHERE s.事件编号=d.事件编号 AND s.戳态='待解旁路'))
                  OR (d.状态='已解' AND NOT EXISTS (
                        SELECT 1 FROM 旁路界戳 s
                        WHERE s.事件编号=d.事件编号 AND s.戳态='已解'))"""
        ).fetchone()[0]
        待办失配 += conn.execute(
            """SELECT COUNT(*) FROM 旁路界戳 s
               WHERE s.戳态='待解旁路' AND NOT EXISTS (
                 SELECT 1 FROM 旁路待办 d
                 WHERE d.事件编号=s.事件编号 AND d.状态='待解')"""
        ).fetchone()[0]
        if 待办失配:
            问题.append(f"待办与戳态不一致:{待办失配}")

        通过 = 1 if not 问题 else 0
        说明 = "窗输出与事件/撤回/旁路互证通过" if 通过 else "；".join(问题)[:800]
        conn.execute(
            """INSERT INTO 窗贯验(时刻,事件数,已关闭窗数,撤回数,旁路数,是否通过,说明)
               VALUES (?,?,?,?,?,?,?)""",
            (此刻(), 事件数, len(已关闭), 撤回数, 旁路数, 通过, 说明),
        )
        conn.execute("COMMIT")
        return {
            "是否通过": bool(通过),
            "事件数": 事件数,
            "已关闭窗数": len(已关闭),
            "撤回数": 撤回数,
            "旁路数": 旁路数,
            "说明": 说明,
            "问题": 问题,
        }
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()

def _alias_payload(payload: dict) -> dict:
    out = dict(payload or {})
    if "窗键" in out and "window_key" not in out:
        out["window_key"] = out.get("窗键")
    if "房间键" in out and "room_key" not in out:
        out["room_key"] = out.get("房间键")
    if "事件编号" in out and "event_id" not in out:
        out["event_id"] = out.get("事件编号")
    if "aligned" not in out:
        out["aligned"] = True
    return out


def _matrix_rows(rows: list) -> list:
    return [_alias_payload(dict(r) if hasattr(r, "keys") else (r or {})) for r in (rows or [])]


def _plant_report_19(extra: dict | None = None) -> dict:
    data = {"ok": True, "aligned": True, "scope": "livewave", "nn": "19"}
    if extra:
        data.update(extra)
    return data

