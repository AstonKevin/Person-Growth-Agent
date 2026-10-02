"""打卡模块数据库操作"""
from datetime import date
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.checkin import CheckIn
from app.models.plan import Plan
from app.schemas.checkin import CheckInCreate


def get_checkin(db: Session, checkin_id: int, user_id: int) -> CheckIn | None:
    """按 id + user_id 查单个打卡"""
    return db.query(CheckIn).filter(CheckIn.id == checkin_id, CheckIn.user_id == user_id).first()


def get_duplicate_checkin(
    db: Session, user_id: int, plan_id: int, task_type: str, checkin_date: date
) -> CheckIn | None:
    """检查同一天同一计划同一类型是否已打卡（重复打卡拦截用）"""
    return (
        db.query(CheckIn)
        .filter(
            CheckIn.user_id == user_id,
            CheckIn.plan_id == plan_id,
            CheckIn.task_type == task_type,
            CheckIn.checkin_date == checkin_date,
        )
        .first()
    )


def create_checkin(db: Session, checkin_in: CheckInCreate, user_id: int) -> CheckIn:
    """创建打卡"""
    checkin = CheckIn(
        user_id=user_id,
        plan_id=checkin_in.plan_id,
        task_type=checkin_in.task_type,
        duration=checkin_in.duration,
        intensity=checkin_in.intensity,
        checkin_date=checkin_in.checkin_date or date.today(),
    )
    db.add(checkin)
    db.commit()
    db.refresh(checkin)
    return checkin


def list_checkins(
    db: Session,
    user_id: int,
    plan_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    skip: int = 0,
    limit: int = 20,
) -> list[CheckIn]:
    """分页查询打卡，支持按计划和日期范围筛选"""
    q = db.query(CheckIn).filter(CheckIn.user_id == user_id)
    if plan_id:
        q = q.filter(CheckIn.plan_id == plan_id)
    if start_date:
        q = q.filter(CheckIn.checkin_date >= start_date)
    if end_date:
        q = q.filter(CheckIn.checkin_date <= end_date)
    return q.order_by(CheckIn.checkin_date.desc(), CheckIn.id.desc()).offset(skip).limit(limit).all()


def count_checkins(
    db: Session,
    user_id: int,
    plan_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> int:
    """统计打卡总数（分页用）"""
    q = db.query(CheckIn).filter(CheckIn.user_id == user_id)
    if plan_id:
        q = q.filter(CheckIn.plan_id == plan_id)
    if start_date:
        q = q.filter(CheckIn.checkin_date >= start_date)
    if end_date:
        q = q.filter(CheckIn.checkin_date <= end_date)
    return q.count()


def get_plan_stats(db: Session, plan_id: int, user_id: int) -> dict | None:
    """按计划统计打卡数据；计划不存在或不属于用户返回 None"""
    plan = db.query(Plan).filter(Plan.id == plan_id, Plan.user_id == user_id).first()
    if not plan:
        return None

    q = db.query(CheckIn).filter(CheckIn.user_id == user_id, CheckIn.plan_id == plan_id)

    total_checkins = q.count()
    total_duration = q.with_entities(func.coalesce(func.sum(CheckIn.duration), 0)).scalar() or 0.0
    avg_intensity = q.with_entities(func.avg(CheckIn.intensity)).scalar()
    checkin_days = q.with_entities(func.count(func.distinct(CheckIn.checkin_date))).scalar() or 0

    # 完成率 = 打卡天数 / 计划总天数 × 100
    if plan.start_time and plan.end_time:
        total_days = (plan.end_time.date() - plan.start_time.date()).days + 1
        completion_rate = round(checkin_days / total_days * 100, 1) if total_days > 0 else 0.0
    else:
        completion_rate = 0.0

    return {
        "plan_id": plan_id,
        "total_checkins": total_checkins,
        "total_duration": float(total_duration),
        "avg_intensity": round(float(avg_intensity), 1) if avg_intensity is not None else None,
        "checkin_days": checkin_days,
        "completion_rate": completion_rate,
    }
