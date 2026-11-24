"""旁路界戳接管迟到写：硬约束与两口径可分性。

夹具主线：
1. 铺底 → 旁路后直接迟到写（同一事件编号）：乙口径下累计不变、戳态=待解旁路、待办可查；
2. 持解旁路工单才可改正式累计；无工单/工单被邻事件占用都拒绝；
3. 旁路后未打迟到写 → 戳态仍为「旁路」、无待办，且升格仍可调用；
4. 甲口径下同一请求整票拒绝，戳态仍为旁路、无待办、累计不变（与乙可区分）；
5. 并发：落匣侧与迟到写侧交错，不得「累计改且匣同时成功」。
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import seed
from app.main import app
from app.modules import 旁路界戳
from app.services import 超迟到旁路, 贯验

关闭窗 = "房A|2026-11-22T12:00:00Z"


@pytest.fixture()
def 客户端(tmp_path, monkeypatch):
    monkeypatch.setenv("数据目录", str(tmp_path))
    monkeypatch.setenv("夹具目录", "/workspace/fixtures")
    seed.初始化库()
    with TestClient(app) as c:
        yield c


def 推(c, 名):
    r = c.post("/api/装载/夹具", params={"名": 名})
    assert r.status_code == 200, r.text
    return r.json()


def 窗(c, 窗键=关闭窗):
    return next((w for w in c.get("/api/热度窗").json() if w["窗键"] == 窗键), None)


def 核(c, 事件编号=None):
    params = {"事件编号": 事件编号} if 事件编号 else {}
    r = c.get("/api/核证/旁路界戳/核验", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def 铺底(c):
    """准时 + 迟到 + 超迟旁路三步，得到一条待升格旁路线 X1（事件时 12:00:20）。"""
    推(c, "准时热度样例")
    推(c, "迟到修正样例")
    推(c, "超迟到旁路样例")


def test_旁路成功必须打戳且核证通过(客户端):
    铺底(客户端)
    行 = 客户端.get("/api/旁路匣/只读/X1").json()
    assert 行["升格状态"] == "待升格"
    assert 行["戳号"] != "" and 行["戳态"] == "旁路"
    assert 行["界戳"]["窗键"] == 关闭窗
    assert 行["界戳"]["水位快照"] == 行["水位"]
    assert 行["界戳"]["旁路界"] == 行["旁路界"]

    果 = 核(客户端)
    assert 果["核证"]["是否通过"] is True
    assert 果["核证"]["旁路成功戳空"] == 0
    assert 果["核证"]["同事件既在匣又改累计"] == 0
    assert 果["核证"]["戳号被邻事件顶替"] == 0


def test_乙_迟到写不改累计_戳态推进且待办可查(客户端):
    铺底(客户端)
    前 = 窗(客户端)
    报 = 推(客户端, "旁路后迟到写样例")  # 同一事件编号 X1 再次入流

    assert 报["迟到写拒绝"] == 1
    assert 报["待解旁路"] == 1
    明细 = next(d for d in 报["明细"] if d["事件编号"] == "X1")
    assert 明细["命运"] == "待解旁路"
    assert 明细["改累计"] is False
    assert 明细["戳态"] == "待解旁路"
    assert 明细["落待办"] is True
    assert 明细["工单号"]

    后 = 窗(客户端)
    assert (后["点赞合计"], 后["事件笔数"], 后["版本"]) == (
        前["点赞合计"], 前["事件笔数"], 前["版本"]
    )

    行 = 客户端.get("/api/旁路匣/只读/X1").json()
    assert 行["戳态"] == "待解旁路"
    assert 行["升格是否可调用"] is False, "待解旁路不得绕过工单直接升格"
    assert 行["解旁路是否可调用"] is True
    assert 行["待办状态"] == "待解"

    待办 = 客户端.get("/api/旁路匣/待办", params={"事件编号": "X1"}).json()
    assert len(待办) == 1 and 待办[0]["状态"] == "待解"
    assert 待办[0]["工单号"] == 明细["工单号"]

    assert 客户端.post("/api/贯验").json()["是否通过"] is True
    assert 核(客户端, "X1")["核证"]["是否通过"] is True


def test_乙_持解旁路工单方可改正式累计(客户端):
    铺底(客户端)
    推(客户端, "旁路后迟到写样例")
    前 = 窗(客户端)
    待办 = 客户端.get("/api/旁路匣/待办", params={"事件编号": "X1"}).json()[0]
    工单号 = 待办["工单号"]

    # 未能工单/工单不符 → 拒绝且累计不变。
    assert 客户端.post("/api/旁路匣/解旁路", json={"事件编号": "X1", "工单号": ""}).status_code == 409
    assert 客户端.post(
        "/api/旁路匣/解旁路", json={"事件编号": "X1", "工单号": "工单:邻事件"}
    ).status_code == 409
    assert 窗(客户端)["点赞合计"] == 前["点赞合计"]

    r = 客户端.post("/api/旁路匣/解旁路", json={"事件编号": "X1", "工单号": 工单号})
    assert r.status_code == 200, r.text
    果 = r.json()
    assert 果["解旁路"] is True and 果["戳态"] == "已解"
    assert 果["新点赞"] == 前["点赞合计"] + 9

    后 = 窗(客户端)
    assert 后["点赞合计"] == 前["点赞合计"] + 9
    assert 后["版本"] == 前["版本"] + 1

    行 = 客户端.get("/api/旁路匣/只读/X1").json()
    assert 行["升格状态"] == "已升格" and 行["戳态"] == "已解"
    待办后 = 客户端.get("/api/旁路匣/待办", params={"事件编号": "X1"}).json()
    assert 待办后[0]["状态"] == "已解"

    # 重复解/重复升格都拒绝，且累计不再变化。
    assert 客户端.post("/api/旁路匣/解旁路", json={"事件编号": "X1", "工单号": 工单号}).status_code == 409
    assert 窗(客户端)["点赞合计"] == 后["点赞合计"]
    assert 客户端.post("/api/贯验").json()["是否通过"] is True


def test_旁路后未打迟到写_戳态仍为旁路且无待办(客户端):
    铺底(客户端)
    行 = 客户端.get("/api/旁路匣/只读/X1").json()
    assert 行["戳态"] == "旁路"
    assert 行["待办状态"] == ""
    assert 客户端.get("/api/旁路匣/待办", params={"事件编号": "X1"}).json() == []

    # 未推进戳态 → 乙下升格仍可调用。
    assert 行["升格是否可调用"] is True
    assert 客户端.post("/api/旁路匣/升格", json={"事件编号": "X1"}).status_code == 200


def test_甲与乙对迟到写结论可分(客户端, monkeypatch):
    """同一夹具、同一请求，只翻转全局架构开关：甲拒绝且无待办，乙推进且落待办。"""
    铺底(客户端)
    前 = 窗(客户端)

    monkeypatch.setattr(超迟到旁路, "架构", 超迟到旁路.架构甲)
    monkeypatch.setattr(超迟到旁路, "允许升格", False)

    r = 客户端.post("/api/装载/夹具", params={"名": "旁路后迟到写样例"})
    assert r.status_code == 409, "甲：整批回滚，迟到写整票拒绝"
    后 = 窗(客户端)
    assert (后["点赞合计"], 后["事件笔数"], 后["版本"]) == (
        前["点赞合计"], 前["事件笔数"], 前["版本"]
    )
    行 = 客户端.get("/api/旁路匣/只读/X1").json()
    assert 行["戳态"] == "旁路", "甲：戳态不得推进"
    assert 行["待办状态"] == "", "甲：不得落待办"
    assert 客户端.get("/api/旁路匣/待办", params={"事件编号": "X1"}).json() == []
    assert 客户端.get("/api/旁路匣/口径").json()["界戳架构"] == 旁路界戳.架构甲
    assert 客户端.post("/api/旁路匣/解旁路", json={
        "事件编号": "X1", "工单号": "x"
    }).status_code == 409, "甲：无待办可解"


def test_界戳核证只读且不改状态(客户端):
    铺底(客户端)
    前 = 窗(客户端)
    a = 核(客户端)
    b = 核(客户端)
    assert a["核证"]["是否通过"] is True and b["核证"]["是否通过"] is True
    assert 窗(客户端)["版本"] == 前["版本"]
    行 = 客户端.get("/api/旁路匣/只读/X1").json()
    assert 行["戳态"] == "旁路", "核证接口不得推进戳态"


def test_戳号不得被邻事件顶替(客户端):
    铺底(客户端)
    from app.db import 连接

    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        原 = 旁路界戳.取戳(conn, "X1")
        # 借 X1 的戳号给邻事件打戳：模块层必须拒绝（不得顶替）。
        with pytest.raises(旁路界戳.界戳违规):
            旁路界戳.打戳(
                conn,
                事件编号="邻事件",
                窗键=原["窗键"],
                水位快照=原["水位快照"],
                旁路界=原["旁路界"],
                处理时=原["时刻"],
                戳号=原["戳号"],
            )
        conn.execute("ROLLBACK")
    finally:
        conn.close()

    # 空戳号 / 空事件编号也不允许。
    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(旁路界戳.界戳违规):
            旁路界戳.打戳(conn, 事件编号="", 窗键="w", 水位快照="a", 旁路界="b",
                          处理时="t")
        conn.execute("ROLLBACK")
    finally:
        conn.close()


@pytest.mark.parametrize("抢锁等待毫秒", ["0", "8000"])
def test_落匣侧与迟到写侧并发不得双成(客户端, monkeypatch, 抢锁等待毫秒):
    """落匣（新事件旁路）与迟到写（已有戳事件）并发：不得累计改且匣同时成功。

    两侧都在 BEGIN IMMEDIATE 内串行化；无论谁先谁后，X1 所属窗合计都不动。
    抢锁失败的一侧整笔回滚（失败闭），不留半截：不得「匣成 + 累计漂」。
    """
    from app.db import 连接
    from app.services import 装载

    铺底(客户端)
    monkeypatch.setenv("数据库忙等毫秒", 抢锁等待毫秒)

    前 = 窗(客户端)
    结果 = {}

    def 跑(键, 夹):
        try:
            结果[键] = ("ok", 装载.装夹具(夹))
        except Exception as e:
            结果[键] = ("err", repr(e))

    守锁 = 连接()
    守锁.execute("BEGIN IMMEDIATE")
    线程 = [
        threading.Thread(target=跑, args=("迟到写", "旁路后迟到写样例")),
        threading.Thread(target=跑, args=("落匣", "原窗超迟事件样例")),
    ]
    for t in 线程:
        t.start()
    time.sleep(0.2)
    守锁.execute("COMMIT")
    守锁.close()
    for t in 线程:
        t.join(timeout=30)

    for 键, (状态, 值) in 结果.items():
        if 状态 == "err":
            assert ("locked" in 值.lower() or "busy" in 值.lower()
                    or "并发" in 值 or "半截" in 值), 值
        elif 键 == "迟到写":
            assert 值["迟到写拒绝"] == 1 and 值["入窗"] == 0 and 值["撤回"] == 0, 值
        else:
            assert 值["旁路"] == 1 and 值["撤回"] == 0 and 值["入窗"] == 0, 值

    后 = 窗(客户端)
    assert (后["点赞合计"], 后["事件笔数"]) == (前["点赞合计"], 前["事件笔数"]), \
        "并发两侧都不得改动原窗累计"

    X1 = 客户端.get("/api/旁路匣/只读/X1").json()
    assert X1["升格状态"] == "待升格", "X1 绝不入窗"
    assert X1["戳态"] in ("旁路", "待解旁路")

    conn = 连接()
    try:
        事件 = conn.execute(
            "SELECT 命运,窗键 FROM 互动事件 WHERE 事件编号='X1'"
        ).fetchone()
        assert 事件["命运"] == "已旁路" and 事件["窗键"] == ""
        # 落匣侧事件若成功则必须有戳；失败则不得留事件（防半截）。
        if 结果.get("落匣", ("err", ""))[0] == "ok":
            assert conn.execute(
                "SELECT COUNT(*) FROM 旁路界戳 WHERE 事件编号='X2'"
            ).fetchone()[0] == 1
        else:
            assert conn.execute(
                "SELECT COUNT(*) FROM 互动事件 WHERE 事件编号='X2'"
            ).fetchone()[0] == 0
    finally:
        conn.close()
    assert 贯验.跑贯验()["是否通过"] is True


def test_戳落失败整票回滚不留匣不留累计(客户端, monkeypatch):
    """旁路落匣成功但打戳失败：整票回滚，匣无残行、累计不变（禁止戳空）。

    这是基线最危险的交错点：「匣成、戳空、累计漂」。打戳失败必须把整批回滚。
    """
    from app.db import 连接
    from app.services import 装载

    铺底(客户端)
    前 = 窗(客户端)
    已装 = 客户端.get("/api/批次").json()[0]["编号"]

    def 坏的打戳(*a, **k):
        raise 旁路界戳.界戳违规("注入：打戳失败")

    原打戳 = 旁路界戳.打戳
    monkeypatch.setattr(旁路界戳, "打戳", 坏的打戳)
    with pytest.raises(旁路界戳.界戳违规):
        装载.装夹具("原窗超迟事件样例")

    后 = 窗(客户端)
    assert (后["点赞合计"], 后["事件笔数"], 后["版本"]) == (
        前["点赞合计"], 前["事件笔数"], 前["版本"]
    ), "打戳失败不得改累计"

    conn = 连接()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM 互动事件 WHERE 事件编号='X2'"
        ).fetchone()[0] == 0, "整票回滚后不得留事件"
        assert conn.execute(
            "SELECT COUNT(*) FROM 旁路匣 WHERE 事件编号='X2'"
        ).fetchone()[0] == 0, "整票回滚后不得留旁路行"
        assert conn.execute(
            "SELECT COUNT(*) FROM 旁路界戳 WHERE 事件编号='X2'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM 装载批次 WHERE 编号=?", (已装,)
        ).fetchone()[0] >= 1
    finally:
        conn.close()

    # 恢复后重灌必须成功，且戳、匣、事件三者齐备。
    monkeypatch.setattr(旁路界戳, "打戳", 原打戳)
    客户端.post("/api/装载/夹具", params={"名": "原窗超迟事件样例"})
    行 = 客户端.get("/api/旁路匣/只读/X2").json()
    assert 行["戳号"] and 行["戳态"] == "旁路"
    assert 核(客户端)["核证"]["是否通过"] is True
    assert 客户端.post("/api/贯验").json()["是否通过"] is True
