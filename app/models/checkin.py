from sqlalchemy import Column,Integer,Float,String,func,ForeignKey,Date
from ..database import Base

class CheckIn(Base):
    __tablename__ = "checkin"
    id = Column(Integer,primary_key=True,nullable=False )
    plan_id = Column(Integer,ForeignKey("plan.id"),nullable=False,index=True)
    user_id = Column(Integer,ForeignKey("user.id"))
    checkin_date = Column(Date,default=func.now(),nullable=False)
    task_type = Column(String(50), nullable=False)       # 学习/运动/...
    duration = Column(Float, nullable=True)              # 时长(小时)
    intensity = Column(Integer, nullable=True)           # 强度 1-5

