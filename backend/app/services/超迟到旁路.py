from __future__ import annotations

import json

from app.services import 时间

架构 = "乙-可升格暂存"
架构甲 = "甲-永久隔离"
允许升格 = True


class 升格拒绝(Exception):
    """升格被拒：调用方回滚事务，旁路匣与热度窗都保持原样（失败闭）。"""

    def __init__(self, 原因: str, 状态码: int = 409):
        super().__init__(原因)
        self.原因 = 原因
        self.状态码 = 状态码


def 站规(conn) -> dict:
    return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 站规")}


def 窗参数(conn) -> tuple[int, int]:
    站 = 站规(conn)
    return int(站.get("窗长秒", "60")), int(站.get("允许迟到秒", "30"))


def 判界(事件时: str, 房间键: str, 窗长秒: int, 允许迟到秒: int, 水位: str):
    """返回 (窗键, 窗起, 窗止, 旁路界, 是否已过旁路界)。"""
    窗起 = 时间.对齐窗起(事件时, 窗长秒)
    窗止 = 时间.加秒(窗起, 窗长秒)
    旁路界 = 时间.加秒(窗止, 允许迟到秒)
    已过 = 时间.解析(水位) >= 时间.解析(旁路界)
    return 时间.窗键(房间键, 窗起), 窗起, 窗止, 旁路界, 已过


def 已有旁路(conn, 事件编号: str):
    return conn.execute(
        "SELECT * FROM 旁路匣 WHERE 事件编号=? ORDER BY 编号 LIMIT 1", (事件编号,)
    ).fetchone()


def 落匣(
    conn,
    事件: dict,
    房间键: str,
    窗键: str,
    窗起: str,
    窗止: str,
    旁路界: str,
    水位: str,
    允许迟到秒: int,
    处理时: str,
    原因: str = "超允许迟到",
    升格状态: str = "待升格",
    备注: str = "",
) -> int:
    """旁路只写匣：不触碰热度窗，不写撤回单。"""
    事件编号 = 事件.get("事件编号") or ""
    载荷 = {
        "事件编号": 事件编号,
        "房间键": 房间键,
        "事件时": 事件.get("事件时") or "",
        "窗键": 窗键,
        "窗起": 窗起,
        "窗止": 窗止,
        "旁路界": 旁路界,
        "水位": 水位,
        "允许迟到秒": 允许迟到秒,
        "原因": 原因,
    }
    游标 = conn.execute(
        """INSERT INTO 旁路匣(原因, 载荷, 时刻, 事件编号, 房间键, 窗键, 事件时, 水位,
                              窗止, 旁路界, 允许迟到秒, 升格状态, 备注)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            原因,
            json.dumps(载荷, ensure_ascii=False),
            处理时,
            事件编号,
            房间键,
            窗键,
            事件.get("事件时") or "",
            水位,
            窗止,
            旁路界,
            允许迟到秒,
            升格状态,
            备注,
        ),
    )
    return int(游标.lastrowid)


def _解析时刻(值: str):
    try:
        return 时间.解析(值)
    except Exception:
        return None


def 校验行(conn, 行) -> tuple[bool, str]:
    """结构 + 不变量校验；valid=False 时绝不允许升格。"""
    窗长, _ = 窗参数(conn)
    if 行["升格状态"] == "不可升格":
        return False, "不可升格（隔离留存）"

    事件编号 = (行["事件编号"] or "").strip()
    if not 事件编号:
        return False, "旁路行未绑定事件编号"

    事件 = conn.execute(
        "SELECT * FROM 互动事件 WHERE 事件编号=?", (事件编号,)
    ).fetchone()
    if not 事件:
        return False, "对应互动事件不存在"

    事件时, 水位, 窗止, 旁路界 = 行["事件时"], 行["水位"], 行["窗止"], 行["旁路界"]
    if not all([事件时, 水位, 窗止, 旁路界]):
        return False, "水位/窗止/旁路界缺项"

    事件时DT, 水位DT = _解析时刻(事件时), _解析时刻(水位)
    窗止DT, 旁路界DT = _解析时刻(窗止), _解析时刻(旁路界)
    if None in (事件时DT, 水位DT, 窗止DT, 旁路界DT):
        return False, "时刻格式非法"

    期望窗起 = 时间.对齐窗起(事件时, 窗长)
    期望窗止 = 时间.加秒(期望窗起, 窗长)
    期望窗键 = 时间.窗键(事件["房间键"], 期望窗起)
    if 窗止 != 期望窗止:
        return False, f"窗止与事件时不对齐: {窗止} vs {期望窗止}"
    if 行["窗键"] and 行["窗键"] != 期望窗键:
        return False, f"窗键与事件时不对齐: {行['窗键']} vs {期望窗键}"
    if 水位DT < 旁路界DT:
        return False, "旁路时水位未越过旁路界"
    if 事件["事件时"] != 事件时:
        return False, "旁路事件时与事件表不一致"

    状态 = 行["升格状态"]
    窗键 = 行["窗键"] or 期望窗键
    窗 = conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (窗键,)).fetchone()

    if 状态 == "待升格":
        if 事件["命运"] != "已旁路" or 事件["窗键"]:
            return False, "待升格事件已挂窗或命运非已旁路（疑似半截）"
        if 窗:
            合计 = conn.execute(
                """SELECT COALESCE(SUM(点赞增量),0) AS 赞, COALESCE(SUM(送礼额),0) AS 送
                   FROM 互动事件 WHERE 窗键=? AND 命运='已入窗'""",
                (窗键,),
            ).fetchone()
            if 合计["赞"] != 窗["点赞合计"] or 合计["送"] != 窗["送礼合计"]:
                return False, "该窗合计与窗内事件不符（疑似旁路事件半截改窗）"
    elif 状态 == "已升格":
        if 事件["命运"] != "已入窗" or 事件["窗键"] != 窗键:
            return False, "已升格事件未挂原窗"
        if not 窗:
            return False, "已升格但原窗不存在"
    else:
        return False, f"未知升格状态:{状态}"
    return True, "ok"


def _读只读(conn, 行) -> dict:
    有效, 原因 = 校验行(conn, 行)
    事件 = conn.execute(
        "SELECT * FROM 互动事件 WHERE 事件编号=?", (行["事件编号"],)
    ).fetchone()
    当前水位 = None
    if 行["房间键"]:
        wm = conn.execute(
            "SELECT 事件时水位 FROM 水位线 WHERE 房间键=?", (行["房间键"],)
        ).fetchone()
        当前水位 = wm["事件时水位"] if wm else None
    可升格 = bool(
        允许升格
        and 有效
        and 行["升格状态"] == "待升格"
        and 事件 is not None
        and 事件["命运"] == "已旁路"
        and not 事件["窗键"]
    )
    return {
        "编号": 行["编号"],
        "事件编号": 行["事件编号"],
        "房间键": 行["房间键"],
        "窗键": 行["窗键"],
        "事件时": 行["事件时"],
        "水位": 行["水位"],
        "当前水位": 当前水位,
        "窗止": 行["窗止"],
        "旁路界": 行["旁路界"],
        "允许迟到秒": 行["允许迟到秒"],
        "升格状态": 行["升格状态"],
        "升格是否可调用": 可升格,
        "是否改窗": bool(行["改窗"]),
        "旁路是否改窗": False,
        "升格是否改历史窗": bool(行["升格改历史窗"]),
        "valid": 有效,
        "valid原因": 原因,
        "架构": 架构,
        "原因": 行["原因"],
        "备注": 行["备注"],
        "升格处理时": 行["升格处理时"],
        "升格窗键": 行["升格窗键"],
    }


def 只读(conn, 事件编号: str | None = None) -> list[dict]:
    if 事件编号:
        rows = conn.execute(
            "SELECT * FROM 旁路匣 WHERE 事件编号=? ORDER BY 编号", (事件编号,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM 旁路匣 ORDER BY 编号 DESC").fetchall()
    return [_读只读(conn, r) for r in rows]


def 只读一条(conn, 事件编号: str) -> dict:
    rows = 只读(conn, 事件编号)
    if not rows:
        raise 升格拒绝(f"旁路匣无此事件:{事件编号}", 404)
    return rows[0]


def 升格(conn, 事件编号: str, 处理时: str) -> dict:
    """显式升格：把旁路事件按升格口径入原窗（或建新窗），并补撤回单。

    调用方负责事务（BEGIN IMMEDIATE + COMMIT/ROLLBACK）。任一步失败即抛
    升格拒绝，事务回滚后窗合计与旁路匣同时保持原样。
    """
    if not 允许升格:
        raise 升格拒绝("当前口径为甲-永久隔离：升格接口拒绝，历史窗永不被该事件改动")

    行 = 已有旁路(conn, 事件编号)
    if not 行:
        raise 升格拒绝(f"旁路匣无此事件:{事件编号}", 404)
    if 行["升格状态"] == "已升格":
        raise 升格拒绝("该事件已升格，重复升格拒绝")
    if 行["升格状态"] == "不可升格":
        raise 升格拒绝(f"该旁路行不可升格:{行['备注'] or 行['原因']}")
    if 行["升格状态"] != "待升格":
        raise 升格拒绝(f"升格状态异常:{行['升格状态']}")

    有效, 原因 = 校验行(conn, 行)
    if not 有效:
        raise 升格拒绝(f"旁路行无效，拒绝升格:{原因}")

    事件 = conn.execute(
        "SELECT * FROM 互动事件 WHERE 事件编号=?", (事件编号,)
    ).fetchone()
    if not 事件 or 事件["命运"] != "已旁路" or 事件["窗键"]:
        raise 升格拒绝("事件已挂窗或命运非已旁路，拒绝升格（防重复入窗）")

    窗长, _ = 窗参数(conn)
    窗键 = 行["窗键"] or 时间.窗键(事件["房间键"], 时间.对齐窗起(事件["事件时"], 窗长))
    窗 = conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (窗键,)).fetchone()

    旧赞 = 窗["点赞合计"] if 窗 else 0
    旧送 = 窗["送礼合计"] if 窗 else 0
    旧笔 = 窗["事件笔数"] if 窗 else 0
    旧版本 = 窗["版本"] if 窗 else 0
    改历史窗 = bool(窗 and 窗["状态"] == "已关闭")

    if not 窗:
        conn.execute(
            """INSERT INTO 热度窗(窗键,房间键,窗起事件时,窗止事件时,状态,点赞合计,送礼合计,事件笔数,关闭处理时,版本)
               VALUES (?,?,?,?, '已关闭', 0, 0, 0, ?, 0)""",
            (窗键, 事件["房间键"], 时间.对齐窗起(事件["事件时"], 窗长),
             行["窗止"], 处理时),
        )
        窗 = conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (窗键,)).fetchone()
    elif 窗["状态"] != "已关闭":
        conn.execute(
            "UPDATE 热度窗 SET 状态='已关闭', 关闭处理时=?, 版本=CASE WHEN 版本=0 THEN 1 ELSE 版本 END WHERE 窗键=?",
            (处理时, 窗键),
        )
        窗 = conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (窗键,)).fetchone()

    新赞 = 旧赞 + int(事件["点赞增量"])
    新送 = 旧送 + int(事件["送礼额"])
    新版本 = int(窗["版本"]) + 1
    改 = conn.execute(
        """UPDATE 热度窗 SET 点赞合计=?, 送礼合计=?, 事件笔数=事件笔数+1, 版本=?
           WHERE 窗键=?""",
        (新赞, 新送, 新版本, 窗键),
    )
    if 改.rowcount != 1:
        raise 升格拒绝("热度窗更新失败，事务回滚")

    改 = conn.execute(
        """UPDATE 互动事件 SET 命运='已入窗', 窗键=?
           WHERE 事件编号=? AND 命运='已旁路' AND 窗键=''""",
        (窗键, 事件编号),
    )
    if 改.rowcount != 1:
        raise 升格拒绝("事件已挂窗或命运变更，拒绝重复入窗，事务回滚")

    改 = conn.execute(
        """UPDATE 旁路匣 SET 升格状态='已升格', 升格处理时=?, 升格窗键=?, 升格改历史窗=?, 改窗=1
           WHERE 编号=? AND 升格状态='待升格'""",
        (处理时, 窗键, 1 if 改历史窗 else 0, 行["编号"]),
    )
    if 改.rowcount != 1:
        raise 升格拒绝("旁路行状态已被并发修改，事务回滚")

    conn.execute(
        """INSERT INTO 撤回单(窗键,房间键,原因,旧点赞,新点赞,旧送礼,新送礼,处理时,事件编号)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (窗键, 事件["房间键"], "旁路升格", 旧赞, 新赞, 旧送, 新送, 处理时, 事件编号),
    )

    return {
        "成功": True,
        "事件编号": 事件编号,
        "窗键": 窗键,
        "旧点赞": 旧赞,
        "新点赞": 新赞,
        "旧送礼": 旧送,
        "新送礼": 新送,
        "旧笔数": 旧笔,
        "新笔数": 旧笔 + 1,
        "旧版本": 旧版本,
        "新版本": 新版本,
        "升格改历史窗": 改历史窗,
        "是否改窗": True,
        "架构": 架构,
        "说明": "升格成功：按升格口径入原窗并补撤回单；此前窗合计从未被该事件改动",
    }


def 口径() -> dict:
    return {
        "架构": 架构,
        "允许升格": 允许升格,
        "旁路是否改窗": False,
        "升格是否可调用": 允许升格,
        "升格后是否改历史窗": 允许升格,
        "对照口径": {
            "甲-永久隔离": {
                "升格是否可调用": False,
                "调用升格接口结论": "拒绝",
                "历史窗是否被该事件改动": False,
            },
            "乙-可升格暂存": {
                "升格是否可调用": True,
                "调用升格接口结论": "成功则改原窗；未升格前窗合计不变",
                "历史窗是否被该事件改动": "升格成功后是",
            },
        },
    }
