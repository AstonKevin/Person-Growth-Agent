"""用量审计的数据库操作：写入 + 聚合查询（供管理后台统计）。

聚合全部在 SQL 层完成（count / sum / group_by），不在 Python 里循环硬算。
"""
from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.usage import UsageLog


def add_usage(
    db: Session,
    *,
    user_id: int,
    model: str,
    status: str,
    provider: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
    error_code: str | None = None,
) -> UsageLog:
    """写入一条用量记录（只含元数据，不含 prompt/内容）。"""
    row = UsageLog(
        user_id=user_id,
        model=model,
        provider=provider,
        status=status,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        error_code=error_code,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _apply_since(stmt, since: datetime | None):
    return stmt.where(UsageLog.created_at >= since) if since else stmt


def total_counts(db: Session, since: datetime | None = None) -> dict:
    """总请求 / 成功 / 失败 / token 汇总（一条 SQL）。"""
    stmt = select(
        func.count(UsageLog.id),
        func.coalesce(func.sum(case((UsageLog.status == "success", 1), else_=0)), 0),
        func.coalesce(func.sum(case((UsageLog.status == "error", 1), else_=0)), 0),
        func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
        func.coalesce(func.sum(UsageLog.completion_tokens), 0),
    )
    stmt = _apply_since(stmt, since)
    total, success, error, pt, ct = db.execute(stmt).one()
    return {
        "total": total,
        "success": int(success),
        "error": int(error),
        "prompt_tokens": int(pt),
        "completion_tokens": int(ct),
    }


def counts_by_model(db: Session, since: datetime | None = None) -> list[dict]:
    """按模型聚合：请求数、token 汇总。"""
    stmt = (
        select(
            UsageLog.model,
            func.count(UsageLog.id),
            func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(UsageLog.completion_tokens), 0),
        )
        .group_by(UsageLog.model)
        .order_by(func.count(UsageLog.id).desc())
    )
    stmt = _apply_since(stmt, since)
    return [
        {
            "model": model,
            "count": count,
            "prompt_tokens": int(pt),
            "completion_tokens": int(ct),
        }
        for model, count, pt, ct in db.execute(stmt).all()
    ]


def counts_by_day(db: Session, days: int = 30) -> list[dict]:
    """近 N 天每日请求数与失败数（趋势图用）。"""
    since = datetime.now() - timedelta(days=days)
    day = func.date(UsageLog.created_at)
    stmt = (
        select(
            day,
            func.count(UsageLog.id),
            func.coalesce(func.sum(case((UsageLog.status == "error", 1), else_=0)), 0),
        )
        .where(UsageLog.created_at >= since)
        .group_by(day)
        .order_by(day)
    )
    return [
        {"day": str(d), "total": int(total), "error": int(err)}
        for d, total, err in db.execute(stmt).all()
    ]
