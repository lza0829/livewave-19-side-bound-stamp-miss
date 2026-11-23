import os
import sqlite3
from pathlib import Path


def 数据目录() -> Path:
    return Path(os.environ.get("数据目录", Path(__file__).resolve().parent.parent / "data"))


def 夹具目录() -> Path:
    return Path(os.environ.get("夹具目录", Path(__file__).resolve().parent.parent.parent / "fixtures"))


def 库路径() -> Path:
    return 数据目录() / "播浪.db"


def 忙等毫秒() -> int:
    """写锁等待上限；0 表示并发抢锁立即失败（失败闭），供并发夹具使用。"""
    try:
        return int(os.environ.get("数据库忙等毫秒", "10000"))
    except ValueError:
        return 10000


def 连接() -> sqlite3.Connection:
    """打开数据库连接。

    isolation_level=None 表示由调用方显式管理事务；入流闸用 BEGIN IMMEDIATE
    把「水位推进 + 关窗扫描 + 入窗/旁路决断」封在同一事务里，交错时失败闭。
    路径与忙等参数按调用时读取环境变量，便于测试隔离到临时数据目录。
    """
    目录 = 数据目录()
    目录.mkdir(parents=True, exist_ok=True)
    忙等 = 忙等毫秒()
    c = sqlite3.connect(str(库路径()), timeout=max(忙等 / 1000, 0.0), isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute(f"PRAGMA busy_timeout = {忙等}")
    return c
