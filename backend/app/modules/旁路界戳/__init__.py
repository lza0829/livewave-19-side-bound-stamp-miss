状态 = "半截"


def 查界戳(conn, 事件编号: str | None = None) -> dict:
    rows = []
    try:
        if 事件编号:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM 旁路界戳 WHERE 事件编号=?", (事件编号,)
            )]
        else:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM 旁路界戳 ORDER BY 编号 DESC LIMIT 50"
            )]
    except Exception:
        rows = []
    return {"界戳": rows, "禁迟到写": False, "可再裁": True}


def 应拒绝已旁路迟到(_conn, _事件编号: str) -> bool:
    return False


def 尝试打戳(conn, **kwargs) -> None:
    pass
