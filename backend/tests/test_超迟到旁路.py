import threading

import pytest
from fastapi.testclient import TestClient

from app import seed
from app.main import app
from app.services import 超迟到旁路


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


def 窗(c, 窗键):
    return next((w for w in c.get("/api/热度窗").json() if w["窗键"] == 窗键), None)


关闭窗 = "房A|2026-11-22T12:00:00Z"


def 铺底(c):
    推(c, "准时热度样例")
    推(c, "迟到修正样例")
    推(c, "超迟到旁路样例")


def test_过旁路界事件只进匣不改窗(客户端):
    铺底(客户端)
    前 = 窗(客户端, 关闭窗)
    assert 前["状态"] == "已关闭"
    点赞前, 笔数前, 版本前 = 前["点赞合计"], 前["事件笔数"], 前["版本"]

    报 = 推(客户端, "原窗超迟事件样例")
    assert 报["旁路"] == 1
    assert 报["新关窗"] == 0

    后 = 窗(客户端, 关闭窗)
    assert (后["点赞合计"], 后["事件笔数"], 后["版本"]) == (点赞前, 笔数前, 版本前)

    r = 客户端.get("/api/旁路匣/只读/X2").json()
    assert r["事件编号"] == "X2"
    assert r["窗键"] == 关闭窗
    assert r["窗止"] == "2026-11-22T12:01:00Z"
    assert r["旁路界"] == "2026-11-22T12:01:30Z"
    assert r["水位"] >= r["旁路界"]
    assert r["valid"] is True
    assert r["是否改窗"] is False
    assert r["升格状态"] == "待升格"
    assert r["升格是否可调用"] is True
    assert 客户端.post("/api/贯验").json()["是否通过"] is True


def test_乙口径升格可改原窗(客户端):
    铺底(客户端)
    推(客户端, "原窗超迟事件样例")
    前 = 窗(客户端, 关闭窗)

    r = 客户端.post("/api/旁路匣/升格", json={"事件编号": "X2"})
    assert r.status_code == 200, r.text
    果 = r.json()
    assert 果["成功"] is True and 果["窗键"] == 关闭窗
    assert 果["新点赞"] == 前["点赞合计"] + 9
    assert 果["升格改历史窗"] is True

    后 = 窗(客户端, 关闭窗)
    assert 后["点赞合计"] == 前["点赞合计"] + 9
    assert 后["版本"] == 前["版本"] + 1

    只读 = 客户端.get("/api/旁路匣/只读/X2").json()
    assert 只读["升格状态"] == "已升格"
    assert 只读["升格是否改历史窗"] is True
    assert 只读["升格是否可调用"] is False

    重复 = 客户端.post("/api/旁路匣/升格", json={"事件编号": "X2"})
    assert 重复.status_code == 409
    assert 窗(客户端, 关闭窗)["点赞合计"] == 后["点赞合计"]
    assert 客户端.post("/api/贯验").json()["是否通过"] is True


def test_甲口径升格必须失败且不改窗(客户端, monkeypatch):
    """甲口径不在运行期可选（全局只贯彻乙）；这里只在测试内翻转模块常量，
    证明「调用升格接口能否改历史窗」在两口径下结论确实可分：甲=拒绝且窗不变。
    """
    铺底(客户端)
    推(客户端, "原窗超迟事件样例")
    前 = 窗(客户端, 关闭窗)

    monkeypatch.setattr(超迟到旁路, "架构", 超迟到旁路.架构甲)
    monkeypatch.setattr(超迟到旁路, "允许升格", False)

    r = 客户端.post("/api/旁路匣/升格", json={"事件编号": "X2"})
    assert r.status_code == 409
    assert "甲-永久隔离" in r.json()["detail"]["原因"]

    后 = 窗(客户端, 关闭窗)
    assert (后["点赞合计"], 后["事件笔数"], 后["版本"]) == (
        前["点赞合计"], 前["事件笔数"], 前["版本"],
    )
    只读 = 客户端.get("/api/旁路匣/只读/X2").json()
    assert 只读["升格是否可调用"] is False
    assert 只读["是否改窗"] is False
    assert 只读["架构"] == 超迟到旁路.架构甲
    assert 客户端.get("/api/旁路匣/口径").json()["升格是否可调用"] is False


def test_抬水位越界后原窗事件窗不变(客户端):
    铺底(客户端)
    推(客户端, "抬水位越界样例")
    前 = 窗(客户端, 关闭窗)
    报 = 推(客户端, "原窗超迟事件样例")
    assert 报["旁路"] == 1
    后 = 窗(客户端, 关闭窗)
    assert (后["点赞合计"], 后["事件笔数"]) == (前["点赞合计"], 前["事件笔数"])


def test_禁止静默丢弃(客户端):
    报 = 客户端.post("/api/装载", json={"批次编号": "批-非法", "事件": [
        {"事件编号": "BAD1", "房间键": "房A", "事件时": "不是时间", "点赞增量": 1},
        {"事件编号": "BAD2", "房间键": "不存在", "事件时": "2026-11-22T12:00:10Z"},
        {"事件编号": "BAD3", "房间键": "房A"},
    ]}).json()
    assert 报["旁路"] == 3
    assert all(d["命运"].startswith("旁路-") for d in 报["明细"])
    匣 = 客户端.get("/api/旁路匣").json()
    assert {r["事件编号"] for r in 匣} == {"BAD1", "BAD2", "BAD3"}
    assert all(r["升格状态"] == "不可升格" for r in 匣)


def test_旁路口径对照可区分(客户端):
    口 = 客户端.get("/api/旁路匣/口径").json()
    assert 口["架构"] == "乙-可升格暂存"
    assert 口["旁路是否改窗"] is False
    assert 口["升格是否可调用"] is True
    assert 口["升格后是否改历史窗"] is True
    甲 = 口["对照口径"]["甲-永久隔离"]
    assert 甲["升格是否可调用"] is False
    assert 甲["历史窗是否被该事件改动"] is False
    assert 口["对照口径"]["乙-可升格暂存"]["历史窗是否被该事件改动"] == "升格成功后是"


def test_非法与未知房一律落匣且valid为假(客户端):
    """禁止静默丢弃的加强版：非超迟原因进匣的行也须 valid=False 且升格必拒。"""
    报 = 客户端.post("/api/装载", json={"批次编号": "批-非法2", "事件": [
        {"事件编号": "BAD9", "房间键": "不存在", "事件时": "2026-11-22T12:00:10Z"},
    ]}).json()
    assert 报["旁路"] == 1
    行 = 客户端.get("/api/旁路匣/只读/BAD9").json()
    assert 行["升格状态"] == "不可升格"
    assert 行["valid"] is False
    assert 行["升格是否可调用"] is False
    assert 客户端.post("/api/旁路匣/升格", json={"事件编号": "BAD9"}).status_code == 409


def test_原窗从未建立时升格建新窗(客户端):
    """极端乱序：先抬水位越界，原窗从未建过；升格须按发射口径建新窗，版本从 1 起。"""
    推(客户端, "抬水位越界样例")
    推(客户端, "原窗超迟事件样例")
    assert 窗(客户端, 关闭窗) is None

    r = 客户端.post("/api/旁路匣/升格", json={"事件编号": "X2"})
    assert r.status_code == 200, r.text
    assert r.json()["升格改历史窗"] is False
    新窗 = 窗(客户端, 关闭窗)
    assert 新窗["状态"] == "已关闭"
    assert 新窗["点赞合计"] == 9 and 新窗["事件笔数"] == 1
    assert 新窗["版本"] == 1
    assert 客户端.post("/api/贯验").json()["是否通过"] is True
