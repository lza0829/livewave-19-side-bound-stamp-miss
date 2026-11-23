import json
from datetime import datetime, timezone

from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 旁路界戳 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  事件编号 TEXT NOT NULL,
  窗键 TEXT NOT NULL DEFAULT '',
  水位快照 TEXT NOT NULL DEFAULT '',
  旁路界 TEXT NOT NULL DEFAULT '',
  戳号 TEXT NOT NULL DEFAULT '',
  时刻 TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS 站规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 直播间 (
  房间键 TEXT PRIMARY KEY,
  标题 TEXT NOT NULL,
  主播 TEXT NOT NULL,
  开播事件时 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '直播中'
);
CREATE TABLE IF NOT EXISTS 互动事件 (
  事件编号 TEXT PRIMARY KEY,
  房间键 TEXT NOT NULL REFERENCES 直播间(房间键),
  事件类型 TEXT NOT NULL,
  事件时 TEXT NOT NULL,
  处理时 TEXT NOT NULL,
  观众键 TEXT NOT NULL DEFAULT '',
  点赞增量 INTEGER NOT NULL DEFAULT 0,
  送礼额 INTEGER NOT NULL DEFAULT 0,
  命运 TEXT NOT NULL DEFAULT '已入流',
  窗键 TEXT NOT NULL DEFAULT '',
  批次编号 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 水位线 (
  房间键 TEXT PRIMARY KEY REFERENCES 直播间(房间键),
  事件时水位 TEXT NOT NULL,
  允许迟到秒 INTEGER NOT NULL DEFAULT 30,
  更新处理时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 热度窗 (
  窗键 TEXT PRIMARY KEY,
  房间键 TEXT NOT NULL REFERENCES 直播间(房间键),
  窗起事件时 TEXT NOT NULL,
  窗止事件时 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '开启',
  点赞合计 INTEGER NOT NULL DEFAULT 0,
  送礼合计 INTEGER NOT NULL DEFAULT 0,
  事件笔数 INTEGER NOT NULL DEFAULT 0,
  关闭处理时 TEXT NOT NULL DEFAULT '',
  版本 INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS 撤回单 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  窗键 TEXT NOT NULL,
  房间键 TEXT NOT NULL,
  原因 TEXT NOT NULL,
  旧点赞 INTEGER NOT NULL,
  新点赞 INTEGER NOT NULL,
  旧送礼 INTEGER NOT NULL,
  新送礼 INTEGER NOT NULL,
  处理时 TEXT NOT NULL,
  事件编号 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 旁路匣 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  原因 TEXT NOT NULL,
  载荷 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  事件编号 TEXT NOT NULL DEFAULT '',
  房间键 TEXT NOT NULL DEFAULT '',
  窗键 TEXT NOT NULL DEFAULT '',
  事件时 TEXT NOT NULL DEFAULT '',
  水位 TEXT NOT NULL DEFAULT '',
  窗止 TEXT NOT NULL DEFAULT '',
  旁路界 TEXT NOT NULL DEFAULT '',
  允许迟到秒 INTEGER NOT NULL DEFAULT 0,
  升格状态 TEXT NOT NULL DEFAULT '待升格',
  升格处理时 TEXT NOT NULL DEFAULT '',
  升格窗键 TEXT NOT NULL DEFAULT '',
  升格改历史窗 INTEGER NOT NULL DEFAULT 0,
  改窗 INTEGER NOT NULL DEFAULT 0,
  备注 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 窗贯验 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  事件数 INTEGER NOT NULL,
  已关闭窗数 INTEGER NOT NULL,
  撤回数 INTEGER NOT NULL,
  旁路数 INTEGER NOT NULL,
  是否通过 INTEGER NOT NULL,
  说明 TEXT NOT NULL
);
把旧版只有「载荷」的旁路行补成可查询/可升格的结构化行。

    旧载荷形如 {"事件编号","事件时","窗止","旁路界","水位"}，没有窗键。
    从互动事件行反推原窗键；推不出就标记不可升格，绝不静默改窗。
    """
    from app.services import 时间

    站 = {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 站规")}
    窗长 = int(站.get("窗长秒", "60"))
    允许迟到 = int(站.get("允许迟到秒", "30"))
    处理数 = 0

    rows = conn.execute(
        "SELECT 编号, 载荷 FROM 旁路匣 WHERE 事件编号='' AND 载荷 LIKE '%事件编号%'"
    ).fetchall()
    for 行 in rows:
        try:
            载 = json.loads(行["载荷"])
        except Exception:
            continue
        事件编号 = str(载.get("事件编号") or "")
        if not 事件编号:
            continue
        已有 = conn.execute(
            "SELECT 1 FROM 旁路匣 WHERE 事件编号=? AND 编号<>?", (事件编号, 行["编号"])
        ).fetchone()
        事件 = conn.execute(
            "SELECT * FROM 互动事件 WHERE 事件编号=?", (事件编号,)
        ).fetchone()

        事件时 = str(载.get("事件时") or (事件["事件时"] if 事件 else ""))
        房间键 = str(事件["房间键"] if 事件 else (载.get("房间键") or ""))
        窗起 = 时间.对齐窗起(事件时, 窗长) if 事件时 else ""
        窗键 = 时间.窗键(房间键, 窗起) if 事件时 and 房间键 else ""
        窗止 = str(载.get("窗止") or (时间.加秒(窗起, 窗长) if 窗起 else ""))
        旁路界 = str(载.get("旁路界") or (时间.加秒(窗止, 允许迟到) if 窗止 else ""))
        水位 = str(载.get("水位") or "")
        迟到 = 允许迟到
        if 旁路界 and 窗止:
            try:
                迟到 = int((时间.解析(旁路界) - 时间.解析(窗止)).total_seconds())
            except Exception:
                迟到 = 允许迟到

        if 已有:
            conn.execute(
                "UPDATE 旁路匣 SET 房间键=?, 窗键=?, 事件时=?, 水位=?, 窗止=?, 旁路界=?, 允许迟到秒=?, 升格状态='不可升格', 备注=? WHERE 编号=?",
                (房间键, 窗键, 事件时, 水位, 窗止, 旁路界, 迟到, "重复历史载荷，未回填事件编号", 行["编号"]),
            )
        elif 事件 and 事件["命运"] == "已旁路" and not 事件["窗键"]:
            conn.execute(
                "UPDATE 旁路匣 SET 事件编号=?, 房间键=?, 窗键=?, 事件时=?, 水位=?, 窗止=?, 旁路界=?, 允许迟到秒=?, 升格状态='待升格', 备注=? WHERE 编号=?",
                (事件编号, 房间键, 窗键, 事件时, 水位, 窗止, 旁路界, 迟到, "历史载荷回填", 行["编号"]),
            )
        else:
            conn.execute(
                "UPDATE 旁路匣 SET 房间键=?, 窗键=?, 事件时=?, 水位=?, 窗止=?, 旁路界=?, 允许迟到秒=?, 升格状态='不可升格', 备注=? WHERE 编号=?",
                (房间键, 窗键, 事件时, 水位, 窗止, 旁路界, 迟到, "历史载荷无对应旁路事件，禁止升格", 行["编号"]),
            )
        处理数 += 1
    return 处理数


def 初始化库() -> None:
    conn = 连接()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(建表)

        conn.execute("BEGIN IMMEDIATE")
        try:
            _补齐列(conn)
            for k, v in 默认站规.items():
                conn.execute("INSERT OR IGNORE INTO 站规(键, 值) VALUES (?, ?)", (k, v))

            if conn.execute("SELECT COUNT(*) AS c FROM 直播间").fetchone()["c"] == 0:
                conn.execute(
                    "INSERT INTO 直播间(房间键, 标题, 主播, 开播事件时, 状态) VALUES (?,?,?,?,?)",
                    ("房A", "晚间热聊", "主播小浪", "2026-11-22T12:00:00Z", "直播中"),
                )
                conn.execute(
                    "INSERT INTO 水位线(房间键, 事件时水位, 允许迟到秒, 更新处理时) VALUES (?,?,?,?)",
                    ("房A", "2026-11-22T12:00:00Z", 30, 此刻()),
                )
            _回填历史旁路(conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
