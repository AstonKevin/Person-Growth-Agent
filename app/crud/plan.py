"""计划模块数据库操作：所有查询都带 user_id 过滤，确保越权隔离"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.plan import Plan
from app.models.checkin import CheckIn
from app.schemas.plan import PlanCreate, PlanUpdate


def get_plan(db: Session, plan_id: int, user_id: int) -> Plan | None:
    """按 id + user_id 查单个计划（不存在或不属于当前用户都返回 None）"""
    return db.query(Plan).filter(Plan.id == plan_id, Plan.user_id == user_id).first()


def get_plans(
    db: Session,
    user_id: int,
    skip: int = 0,
    limit: int = 20,
    status: str | None = None,
) -> list[Plan]:
    """分页查询当前用户的计划，可按状态筛选，按创建时间倒序"""
    q = db.query(Plan).filter(Plan.user_id == user_id)
    if status:
        q = q.filter(Plan.stauts == status)  # 模型字段名是 stauts（拼写遗留）
    return q.order_by(Plan.create_time.desc()).offset(skip).limit(limit).all()


def count_plans(db: Session, user_id: int, status: str | None = None) -> int:
    """统计当前用户计划总数（用于分页 total）"""
    q = db.query(Plan).filter(Plan.user_id == user_id)
    if status:
        q = q.filter(Plan.stauts == status)
    return q.count()


def create_plan(db: Session, plan_in: PlanCreate, user_id: int) -> Plan:
    """创建计划：status 映射到模型的 stauts 字段"""
    plan = Plan(
        user_id=user_id,
        goal=plan_in.goal,
        daily_plan=plan_in.daily_plan,
        week_plan=plan_in.week_plan,
        start_time=plan_in.start_time,
        end_time=plan_in.end_time,
        stauts=plan_in.status,  # 注意：模型字段名是 stauts
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def update_plan(db: Session, plan: Plan, plan_in: PlanUpdate) -> Plan:
    """更新计划：只更新传入的字段，自动刷新 update_time"""
    update_data = plan_in.model_dump(exclude_unset=True)
    # 对外的 status 映射到内部的 stauts
    if "status" in update_data:
        update_data["stauts"] = update_data.pop("status")
    for field, value in update_data.items():
        setattr(plan, field, value)
    plan.update_time = func.now()
    db.commit()
    db.refresh(plan)
    return plan


def delete_plan(db: Session, plan: Plan) -> None:
    """删除计划：级联删除该计划下的所有打卡记录（外键约束）"""
    db.query(CheckIn).filter(CheckIn.plan_id == plan.id).delete()
    db.delete(plan)
    db.commit()
