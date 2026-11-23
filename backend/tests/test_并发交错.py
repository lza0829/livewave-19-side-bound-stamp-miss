import threading
import time

import pytest

from app import seed
from app.services import 装载, 超迟到旁路


@pytest.fixture()
def 铺好(tmp_path, monkeypatch):
    monkeypatch.setenv("数据目录", str(tmp_path))
    monkeypatch.setenv("夹具目录", "/workspace/fixtures")
    seed.初始化库()
    for 名 in ("准时热度样例", "迟到修正样例", "超迟到旁路样例"):
        装载.装夹具(名)
    return tmp_path


def _窗(conn, 窗键):
    return conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (窗键,)).fetchone()


def _匣(conn, 事件编号):
    return conn.execute("SELECT * FROM 旁路匣 WHERE 事件编号=?", (事件编号,)).fetchone()


关闭窗 = "房A|2026-11-22T12:00:00Z"


def test_同一事件不得既旁路又改窗(铺好):
    """同一事件在同一批里只能走一条路径。重复入流按幂等跳过，不得二次入窗。"""
    from app.db import 连接

    conn = 连接()
    try:
        前 = _窗(conn, 关闭窗)
        前赞, 前笔 = 前["点赞合计"], 前["事件笔数"]
    finally:
        conn.close()

    装载.装夹具("并发旁路侧样例")
    装载.装夹具("并发旁路侧样例")

    conn = 连接()
    try:
        后 = _窗(conn, 关闭窗)
        assert (后["点赞合计"], 后["事件笔数"]) == (前赞, 前笔)
        行 = _匣(conn, "P1")
        assert 行 is not None and 行["升格状态"] == "待升格"
        assert conn.execute(
            "SELECT COUNT(*) FROM 互动事件 WHERE 事件编号='P1'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT 命运 FROM 互动事件 WHERE 事件编号='P1'"
        ).fetchone()[0] == "已旁路"
    finally:
        conn.close()


@pytest.mark.parametrize("抢锁等待毫秒", ["0", "8000"])
def test_并发旁路与迟到修正交错(铺好, monkeypatch, 抢锁等待毫秒):
    """同一房间两笔同时入流：
        · P1 事件时 12:00:40 → 所属窗 12:00 已过旁路界 → 必须旁路，且窗 12:00 合计不变；
        · L2 事件时 12:01:20 → 所属窗 12:01 已关闭但未过旁路界 → 必须迟到修正，窗 12:01 +4 并发撤回单。
    抢到锁的一方（忙等=0 时两方都可能失败）只允许整笔成功或整笔回滚，绝不允许半截。
    """
    旁路名 = "并发旁路侧样例"
    迟到名 = "并发迟到侧样例"
    monkeypatch.setenv("数据库忙等毫秒", 抢锁等待毫秒)

    from app.db import 连接

    守锁 = 连接()
    守锁.execute("BEGIN IMMEDIATE")

    结果 = {}

    def 跑(键, 夹):
        try:
            结果[键] = ("ok", 装载.装夹具(夹))
        except Exception as e:
            结果[键] = ("err", repr(e))

    线程 = [
        threading.Thread(target=跑, args=("旁路", 旁路名)),
        threading.Thread(target=跑, args=("迟到", 迟到名)),
    ]
    for t in 线程:
        t.start()
    time.sleep(0.15)
    守锁.execute("COMMIT")
    守锁.close()
    for t in 线程:
        t.join(timeout=20)

    conn = 连接()
    try:
        旁路窗 = _窗(conn, "房A|2026-11-22T12:00:00Z")
        迟到窗 = _窗(conn, "房A|2026-11-22T12:01:00Z")

        for 键, (状态, 值) in 结果.items():
            if 状态 == "ok":
                if 键 == "旁路":
                    assert 值["旁路"] == 1 and 值["撤回"] == 0 and 值["入窗"] == 0, (键, 值)
                else:
                    assert 值["撤回"] == 1 and 值["旁路"] == 0 and 值["入窗"] == 1, (键, 值)
            else:
                assert (
                    "locked" in 值.lower() or "busy" in 值.lower()
                    or "并发" in 值 or "半截" in 值
                ), 值

        成功键 = {k for k, (s, _) in 结果.items() if s == "ok"}
        事件 = {"旁路": ("P1", "房A|2026-11-22T12:00:00Z"), "迟到": ("L2", "房A|2026-11-22T12:01:00Z")}

        assert 旁路窗["点赞合计"] == 7, "旁路事件绝不得改原窗合计"
        assert 旁路窗["事件笔数"] == 3
        迟到窗后 = _窗(conn, "房A|2026-11-22T12:01:00Z")
        if "迟到" in 成功键:
            assert 迟到窗后["点赞合计"] == 11 and 迟到窗后["事件笔数"] == 3
            assert conn.execute(
                "SELECT COUNT(*) FROM 撤回单 WHERE 事件编号='L2'"
            ).fetchone()[0] == 1
        else:
            assert 迟到窗后["点赞合计"] == 7 and 迟到窗后["事件笔数"] == 2
            assert conn.execute(
                "SELECT COUNT(*) FROM 撤回单 WHERE 事件编号='L2'"
            ).fetchone()[0] == 0

        for 键, (编号, _窗键) in 事件.items():
            行 = _匣(conn, 编号)
            有事件 = conn.execute(
                "SELECT COUNT(*) FROM 互动事件 WHERE 事件编号=?", (编号,)
            ).fetchone()[0]
            if 键 in 成功键:
                e = conn.execute("SELECT * FROM 互动事件 WHERE 事件编号=?", (编号,)).fetchone()
                if 键 == "旁路":
                    assert 行 is not None and 行["升格状态"] == "待升格"
                    assert e["命运"] == "已旁路" and e["窗键"] == ""
                else:
                    assert 行 is None, "迟到修正事件不应落旁路匣"
                    assert e["命运"] == "已入窗" and e["窗键"] == _窗键
            else:
                assert 行 is None, f"失败方不得留下旁路行: {键}"
                assert 有事件 == 0, f"失败方不得留下事件: {键}"

        from app.services import 贯验

        assert 贯验.跑贯验()["是否通过"] is True
    finally:
        conn.close()
