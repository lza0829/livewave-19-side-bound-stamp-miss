import json
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import 夹具目录, 连接
from app.modules import 旁路界戳 as 界
from app.seed import 此刻
from app.services import 装载, 贯验, 目录, 超迟到旁路

路由 = APIRouter(prefix="/api")


def _写闸(函数):
    """入流/升格统一把 SQLite 写锁冲突翻成 409（失败闭），不报半截。"""
    try:
        return 函数()
    except 装载.入流闸失败 as e:
        raise HTTPException(409, {"原因": str(e), "已回滚": True})
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            raise HTTPException(409, {"原因": "并发写冲突，事务未提交（失败闭）", "已回滚": True})
        raise


class 装载请求(BaseModel):
    批次编号: str = ""
    事件: list = Field(default_factory=list)


class 升格请求(BaseModel):
    事件编号: str
    处理时: str = ""


class 解旁路请求(BaseModel):
    事件编号: str
    工单号: str
    处理时: str = ""


@路由.get("/健康")
def 健康():
    return {
        "正常": True,
        "项目": "播浪",
        "骨架": "互动事件入流→事件时间水位线推进→热度窗关闭发射→迟到修正撤回→窗输出与撤回互证",
        "旁路架构": 超迟到旁路.架构,
    }


@路由.get("/总览")
def 取总览():
    return 目录.总览()


@路由.get("/站规")
def 站规():
    conn = 连接()
    try:
        return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 站规")}
    finally:
        conn.close()


@路由.get("/直播间")
def 列直播间():
    conn = 连接()
    try:
        rooms = [dict(r) for r in conn.execute("SELECT * FROM 直播间 ORDER BY 房间键")]
        for r in rooms:
            wm = conn.execute("SELECT * FROM 水位线 WHERE 房间键=?", (r["房间键"],)).fetchone()
            r["水位线"] = dict(wm) if wm else None
        return rooms
    finally:
        conn.close()


@路由.get("/事件")
def 列事件(命运: str = ""):
    conn = 连接()
    try:
        if 命运:
            rows = conn.execute(
                "SELECT * FROM 互动事件 WHERE 命运=? ORDER BY 事件时 DESC LIMIT 200", (命运,)
            )
        else:
            rows = conn.execute("SELECT * FROM 互动事件 ORDER BY 事件时 DESC LIMIT 200")
        return [dict(r) for r in rows]
    finally:
        conn.close()


@路由.get("/水位线")
def 列水位线():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 水位线 ORDER BY 房间键")]
    finally:
        conn.close()


@路由.get("/热度窗")
def 列热度窗(状态: str = ""):
    conn = 连接()
    try:
        if 状态:
            rows = conn.execute(
                "SELECT * FROM 热度窗 WHERE 状态=? ORDER BY 窗起事件时 DESC", (状态,)
            )
        else:
            rows = conn.execute("SELECT * FROM 热度窗 ORDER BY 窗起事件时 DESC")
        return [dict(r) for r in rows]
    finally:
        conn.close()


@路由.get("/撤回单")
def 列撤回():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 撤回单 ORDER BY 编号 DESC LIMIT 100")]
    finally:
        conn.close()


@路由.get("/旁路匣")
def 列旁路():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 旁路匣 ORDER BY 编号 DESC LIMIT 100")]
    finally:
        conn.close()


@路由.get("/旁路匣/口径")
def 旁路口径():
    conn = 连接()
    try:
        return 超迟到旁路.口径()
    finally:
        conn.close()


@路由.get("/旁路匣/只读")
def 旁路只读(事件编号: str = ""):
    """旁路只读接口：事件编号、水位、窗止、旁路界、升格是否可调用、是否改窗、valid。"""
    conn = 连接()
    try:
        return 超迟到旁路.只读(conn, 事件编号 or None)
    finally:
        conn.close()


@路由.get("/旁路匣/只读/{event_id}")
def 旁路只读一条(event_id: str):
    事件编号 = event_id
    conn = 连接()
    try:
        return 超迟到旁路.只读一条(conn, 事件编号)
    except 超迟到旁路.升格拒绝 as e:
        raise HTTPException(e.状态码, e.原因)
    finally:
        conn.close()


@路由.post("/旁路匣/升格")
def 旁路升格(体: 升格请求):
    def 做():
        conn = 连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                结果 = 超迟到旁路.升格(conn, 体.事件编号, 体.处理时 or 此刻())
                conn.execute("COMMIT")
                return 结果
            except 超迟到旁路.升格拒绝 as e:
                conn.execute("ROLLBACK")
                raise HTTPException(e.状态码, {"原因": e.原因})
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    return _写闸(做)


@路由.post("/旁路匣/升格/{event_id}")
def 旁路升格路径(event_id: str):
    return 旁路升格(升格请求(事件编号=event_id))


@路由.post("/旁路匣/解旁路")
def 旁路解旁路(体: 解旁路请求):
    """持解旁路工单解：仅乙可改正式累计；甲口径下无待办，一律拒绝。"""
    def 做():
        conn = 连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                结果 = 超迟到旁路.解旁路(
                    conn, 体.事件编号, 体.工单号, 体.处理时 or 此刻()
                )
                conn.execute("COMMIT")
                return 结果
            except 超迟到旁路.升格拒绝 as e:
                conn.execute("ROLLBACK")
                raise HTTPException(e.状态码, {"原因": e.原因})
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    return _写闸(做)


@路由.get("/旁路匣/待办")
def 列旁路待办(事件编号: str = ""):
    conn = 连接()
    try:
        return 界.待办列表(conn, 事件编号 or None)
    finally:
        conn.close()


@路由.get("/批次")
def 列批次():
    conn = 连接()
    try:
        out = []
        for r in conn.execute("SELECT * FROM 装载批次 ORDER BY 时刻 DESC LIMIT 30"):
            d = dict(r)
            try:
                d["报告"] = json.loads(d["报告"])
            except Exception:
                pass
            out.append(d)
        return out
    finally:
        conn.close()


@路由.post("/装载")
def 做装载(体: 装载请求):
    return _写闸(lambda: 装载.装一批(体.model_dump()))


@路由.post("/装载/夹具")
def 装夹具(名: str = "准时热度样例"):
    try:
        return _写闸(lambda: 装载.装夹具(名))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@路由.post("/贯验")
def 做贯验():
    return 贯验.跑贯验()


@路由.get("/贯验")
def 列贯验():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 窗贯验 ORDER BY 编号 DESC LIMIT 20")]
    finally:
        conn.close()


@路由.get("/空桩")
def 空桩():
    from app import modules

    return modules.列表()

@路由.get("/核证/旁路界戳")
def 取旁路界戳(事件编号: str | None = None):
    conn = 连接()
    try:
        return 界.查界戳(conn, 事件编号)
    finally:
        conn.close()


@路由.get("/核证/旁路界戳/核验")
def 核验旁路界戳(事件编号: str | None = None):
    """旁路界戳核证只读接口：硬约束逐项核验，绝不改状态。"""
    conn = 连接()
    try:
        return 界.核证(conn, 事件编号)
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

