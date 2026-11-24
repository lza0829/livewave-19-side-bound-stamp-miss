"""旁路界戳：超迟到旁路成功后必须同票打戳，迟到写按界戳口径消化。

全局只贯彻一种架构（默认乙），两口径对「旁路后直接打迟到写」的结论必须可分：

- 甲 · 界戳一票否决：有戳则迟到写整票拒绝；戳态保持「旁路」，不落待办，
  正式累计与戳态都不得因该请求变化。
- 乙 · 戳态机升级：有戳则迟到写不得改正式累计，但同票把戳态推进为
  「待解旁路」并落一张待办单；持解旁路工单方可解，解后只由乙改正式累计。

硬约束：
- 禁止旁路成功戳空（待升格/已升格旁路行必有戳）。
- 禁止同事件既在匣又改累计（戳态未解时事件不得挂窗）。
- 禁止戳号被邻事件顶替（戳号内含事件编号，且唯一）。
"""

from __future__ import annotations

from app.services import 时间

状态 = "已实现"
架构 = "乙-戳态机升级"
架构甲 = "甲-界戳一票否决"
允许的戳态 = ("旁路", "待解旁路", "已解")


class 界戳违规(Exception):
    """界戳不变量被破坏：调用方必须整票回滚（失败闭）。"""

    def __init__(self, 原因: str, 状态码: int = 409):
        super().__init__(原因)
        self.原因 = 原因
        self.状态码 = 状态码


def 当前架构() -> str:
    """全局架构只有一个来源：超迟到旁路.架构（生产恒为乙，只在测试内翻转）。

    本模块的 架构 常量仅作默认值；只要全局开关翻到甲，这里就一致返回甲，
    迟到写闸与戳态机不会各说各话。延迟导入避免循环依赖。
    """
    try:
        from app.services import 超迟到旁路

        if 超迟到旁路.架构 == 超迟到旁路.架构甲:
            return 架构甲
    except Exception:
        pass
    return 架构


def _戳号(事件编号: str, 窗键: str, 旁路界: str) -> str:
    """戳号内嵌事件编号与旁路界：邻事件无法合法占用同一戳号。"""
    return f"戳:{事件编号}:{窗键}:{旁路界}"


def 取戳(conn, 事件编号: str):
    return conn.execute(
        "SELECT * FROM 旁路界戳 WHERE 事件编号=? ORDER BY 编号 LIMIT 1", (事件编号,)
    ).fetchone()


def 有戳(conn, 事件编号: str) -> bool:
    return 取戳(conn, 事件编号) is not None


def _校验时刻(水位快照: str, 旁路界: str) -> None:
    try:
        水位DT = 时间.解析(水位快照)
        界DT = 时间.解析(旁路界)
    except (ValueError, TypeError) as e:
        raise 界戳违规(f"界戳时刻格式非法:{e}") from None
    if 水位DT < 界DT:
        raise 界戳违规("水位快照未越过旁路界，禁止打戳")


def 打戳(
    conn,
    *,
    事件编号: str,
    窗键: str,
    水位快照: str,
    旁路界: str,
    处理时: str,
    允许迟到秒: int = 0,
    戳号: str = "",
) -> dict:
    """旁路成功后同票打戳。任何要素缺失即整票失败，绝不落空戳。"""
    if not 事件编号 or not 窗键 or not 水位快照 or not 旁路界:
        raise 界戳违规("界戳要素缺项（事件编号/窗键/水位快照/旁路界），禁止空戳")
    _校验时刻(水位快照, 旁路界)

    期望戳号 = _戳号(事件编号, 窗键, 旁路界)
    if 戳号 and 戳号 != 期望戳号:
        raise 界戳违规("戳号与事件编号/窗键/旁路界不符，禁止顶替")
    戳号 = 期望戳号

    已有 = 取戳(conn, 事件编号)
    if 已有:
        if 已有["戳号"] != 戳号:
            raise 界戳违规(
                f"同一事件已存在不同戳号，禁止顶替:{已有['戳号']} vs {戳号}"
            )
        return dict(已有)

    占号 = conn.execute(
        "SELECT 事件编号 FROM 旁路界戳 WHERE 戳号=?", (戳号,)
    ).fetchone()
    if 占号 and 占号["事件编号"] != 事件编号:
        raise 界戳违规(f"戳号已被邻事件占用，禁止顶替:{占号['事件编号']}")

    conn.execute(
        """INSERT INTO 旁路界戳(事件编号, 窗键, 水位快照, 旁路界, 戳号, 时刻, 戳态)
           VALUES (?,?,?,?,?,?, '旁路')""",
        (事件编号, 窗键, 水位快照, 旁路界, 戳号, 处理时),
    )
    return dict(取戳(conn, 事件编号))


def 标记已解(conn, 事件编号: str) -> None:
    改 = conn.execute(
        "UPDATE 旁路界戳 SET 戳态='已解' WHERE 事件编号=? AND 戳态 IN ('旁路','待解旁路')",
        (事件编号,),
    )
    if 改.rowcount != 1:
        raise 界戳违规("戳态推进失败（已解或缺失），事务回滚")


def 建待办(conn, 事件编号: str, 戳号: str, 窗键: str, 处理时: str) -> dict:
    工单号 = f"工单:{事件编号}:{戳号}"
    已有 = conn.execute(
        "SELECT * FROM 旁路待办 WHERE 事件编号=? ORDER BY 编号 DESC LIMIT 1",
        (事件编号,),
    ).fetchone()
    if 已有:
        if 已有["工单号"] != 工单号:
            raise 界戳违规("待办工单号与事件/戳号不符，禁止顶替")
        return dict(已有)
    占号 = conn.execute(
        "SELECT 事件编号 FROM 旁路待办 WHERE 工单号=?", (工单号,)
    ).fetchone()
    if 占号 and 占号["事件编号"] != 事件编号:
        raise 界戳违规("工单号已被邻事件占用，禁止顶替")
    conn.execute(
        """INSERT INTO 旁路待办(事件编号, 戳号, 窗键, 状态, 原因, 创建处理时, 工单号)
           VALUES (?,?,?, '待解', '旁路后迟到写，戳态已推进为待解旁路', ?, ?)""",
        (事件编号, 戳号, 窗键, 处理时, 工单号),
    )
    return dict(conn.execute(
        "SELECT * FROM 旁路待办 WHERE 事件编号=? ORDER BY 编号 DESC LIMIT 1",
        (事件编号,),
    ).fetchone())


def 消化迟到写(conn, 事件编号: str, 处理时: str) -> dict:
    """迟到写命中已有界戳的事件：按当前全局架构消化，绝不改正式累计。

    甲：整票拒绝（抛 界戳违规），戳态与待办都不变。
    乙：戳态推进为「待解旁路」，落待办；重复调用保持幂等。
    """
    戳 = 取戳(conn, 事件编号)
    if not 戳:
        raise 界戳违规(f"事件无界戳，不得走界戳消化:{事件编号}")
    if 戳["戳态"] == "已解":
        return {
            "事件编号": 事件编号,
            "命运": "幂等跳过",
            "戳态": "已解",
            "改累计": False,
            "落待办": False,
            "推进": False,
            "工单号": "",
        }
    if 戳["戳态"] not in ("旁路", "待解旁路"):
        raise 界戳违规(f"未知戳态:{戳['戳态']}")

    if 当前架构() == 架构甲:
        raise 界戳违规(
            "甲-界戳一票否决：该事件已有旁路界戳，迟到写整票拒绝，戳态与累计不变"
        )

    改 = conn.execute(
        "UPDATE 旁路界戳 SET 戳态='待解旁路' WHERE 事件编号=? AND 戳态='旁路'",
        (事件编号,),
    )
    推进 = 改.rowcount == 1
    if not 推进:
        现 = 取戳(conn, 事件编号)
        if not 现 or 现["戳态"] != "待解旁路":
            raise 界戳违规("戳态推进失败，事务回滚")
    待办 = 建待办(conn, 事件编号, 戳["戳号"], 戳["窗键"], 处理时)
    return {
        "事件编号": 事件编号,
        "命运": "待解旁路",
        "戳态": "待解旁路",
        "改累计": False,
        "落待办": True,
        "推进": 推进,
        "工单号": 待办["工单号"],
        "待办状态": 待办["状态"],
    }


def 应拒绝已旁路迟到(conn, 事件编号: str) -> bool:
    """迟到写闸用：当前架构下，有戳是否意味着必须拒绝。"""
    return 当前架构() == 架构甲 and 有戳(conn, 事件编号)


def 待办列表(conn, 事件编号: str | None = None) -> list[dict]:
    if 事件编号:
        rows = conn.execute(
            "SELECT * FROM 旁路待办 WHERE 事件编号=? ORDER BY 编号 DESC", (事件编号,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM 旁路待办 ORDER BY 编号 DESC LIMIT 100"
        ).fetchall()
    return [dict(r) for r in rows]


def 取待办(conn, 事件编号: str):
    return conn.execute(
        "SELECT * FROM 旁路待办 WHERE 事件编号=? ORDER BY 编号 DESC LIMIT 1",
        (事件编号,),
    ).fetchone()


def 核证(conn, 事件编号: str | None = None) -> dict:
    """旁路界戳核证只读接口：逐项核验硬约束，不改任何状态。"""
    if 事件编号:
        戳行 = [dict(r) for r in conn.execute(
            "SELECT * FROM 旁路界戳 WHERE 事件编号=? ORDER BY 编号", (事件编号,)
        )]
    else:
        戳行 = [dict(r) for r in conn.execute(
            "SELECT * FROM 旁路界戳 ORDER BY 编号 DESC LIMIT 200"
        )]

    待办行 = 待办列表(conn, 事件编号)

    空戳 = conn.execute(
        """SELECT COUNT(*) FROM 旁路匣 b
           WHERE b.升格状态 IN ('待升格','已升格') AND b.事件编号<>''
             AND NOT EXISTS (SELECT 1 FROM 旁路界戳 s WHERE s.事件编号=b.事件编号)"""
    ).fetchone()[0]

    匣双记 = conn.execute(
        """SELECT COUNT(*) FROM 旁路匣 b JOIN 互动事件 e ON e.事件编号=b.事件编号
           WHERE b.升格状态='待升格' AND (e.命运<>'已旁路' OR e.窗键<>'')"""
    ).fetchone()[0]

    戳双记 = conn.execute(
        """SELECT COUNT(*) FROM 旁路界戳 s JOIN 互动事件 e ON e.事件编号=s.事件编号
           WHERE s.戳态 IN ('旁路','待解旁路') AND e.命运='已入窗'"""
    ).fetchone()[0]

    双记 = 匣双记 + 戳双记

    顶替 = conn.execute(
        """SELECT COUNT(*) FROM (
             SELECT 戳号 FROM 旁路界戳 GROUP BY 戳号
             HAVING COUNT(*) > 1 OR COUNT(DISTINCT 事件编号) > 1
           )"""
    ).fetchone()[0]
    # 戳号必须内嵌自身事件编号：邻事件占号或改号都算顶替。
    顶替 += conn.execute(
        """SELECT COUNT(*) FROM 旁路界戳
           WHERE 戳号='' OR 戳号 NOT LIKE '戳:' || 事件编号 || ':%'"""
    ).fetchone()[0]
    工单顶替 = conn.execute(
        """SELECT COUNT(*) FROM (
             SELECT 工单号 FROM 旁路待办 GROUP BY 工单号
             HAVING COUNT(*) > 1 OR COUNT(DISTINCT 事件编号) > 1
           )"""
    ).fetchone()[0]
    工单顶替 += conn.execute(
        """SELECT COUNT(*) FROM 旁路待办
           WHERE 工单号='' OR 工单号 NOT LIKE '工单:' || 事件编号 || ':%'"""
    ).fetchone()[0]
    顶替 += 工单顶替

    非法态 = conn.execute(
        "SELECT COUNT(*) FROM 旁路界戳 WHERE 戳态 NOT IN ('旁路','待解旁路','已解')"
    ).fetchone()[0]

    待办失配 = conn.execute(
        """SELECT COUNT(*) FROM 旁路待办 d
           WHERE (d.状态='待解' AND NOT EXISTS (
                    SELECT 1 FROM 旁路界戳 s
                    WHERE s.事件编号=d.事件编号 AND s.戳态='待解旁路'))
              OR (d.状态='已解' AND NOT EXISTS (
                    SELECT 1 FROM 旁路界戳 s
                    WHERE s.事件编号=d.事件编号 AND s.戳态='已解'))"""
    ).fetchone()[0]
    缺待办 = conn.execute(
        """SELECT COUNT(*) FROM 旁路界戳 s
           WHERE s.戳态='待解旁路' AND NOT EXISTS (
             SELECT 1 FROM 旁路待办 d
             WHERE d.事件编号=s.事件编号 AND d.状态='待解')"""
    ).fetchone()[0]
    待办失配 += 缺待办

    问题 = []
    if 空戳:
        问题.append(f"旁路成功戳空:{空戳}")
    if 匣双记:
        问题.append(f"匣内待升格事件已挂窗/已入窗:{匣双记}")
    if 戳双记:
        问题.append(f"戳态未解事件已改正式累计:{戳双记}")
    if 顶替:
        问题.append(f"戳号/工单号被重复、为空或被邻事件顶替:{顶替}")
    if 非法态:
        问题.append(f"非法戳态:{非法态}")
    if 待办失配:
        问题.append(f"待办与戳态不一致:{待办失配}")

    戳态推进 = 当前架构() != 架构甲
    禁迟到写 = 当前架构() == 架构甲
    return {
        "架构": 当前架构(),
        "禁迟到写": 禁迟到写,
        "可再裁": True,
        "戳态推进": 戳态推进,
        "迟到写遇戳": (
            "整票拒绝；戳态保持「旁路」，不落待办，累计不变"
            if 禁迟到写
            else "不改正式累计；戳态推进为「待解旁路」并落待办"
        ),
        "解旁路": (
            "甲无待办可解；正式累计永不被该事件改动"
            if 禁迟到写
            else "持解旁路工单可解；解后仅乙改正式累计"
        ),
        "对照口径": {
            架构甲: {
                "迟到写遇戳": "整票拒绝；戳态保持「旁路」，不落待办，累计不变",
                "戳态是否推进": False,
                "是否落待办": False,
                "解旁路": "甲无待办可解；正式累计永不被该事件改动",
            },
            架构: {
                "迟到写遇戳": "不改正式累计；戳态推进为「待解旁路」并落待办",
                "戳态是否推进": True,
                "是否落待办": True,
                "解旁路": "持解旁路工单可解；解后仅乙改正式累计",
            },
        },
        "界戳": 戳行,
        "待办": 待办行,
        "核证": {
            "是否通过": not 问题,
            "问题": 问题,
            "旁路成功戳空": 空戳,
            "同事件既在匣又改累计": 双记,
            "戳号被邻事件顶替": 顶替,
            "戳态非法": 非法态,
        },
    }


def 查界戳(conn, 事件编号: str | None = None) -> dict:
    """旧查询入口：保留 界戳/禁迟到写/可再裁，并附上核证结论。"""
    果 = 核证(conn, 事件编号)
    return {
        "架构": 果["架构"],
        "界戳": 果["界戳"],
        "待办": 果["待办"],
        "禁迟到写": 果["禁迟到写"],
        "戳态推进": 果["戳态推进"],
        "可再裁": 果["可再裁"],
        "核证": 果["核证"],
        "对照口径": 果["对照口径"],
        "口径": {
            "迟到写遇戳": 果["迟到写遇戳"],
            "解旁路": 果["解旁路"],
        },
    }
