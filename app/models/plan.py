from sqlalchemy import Column,Integer,Boolean,Text,String,DateTime,func,ForeignKey
from ..database import Base

class Plan(Base):
    __tablename__ = "plan"
    id = Column(Integer,primary_key=True,nullable=False)
    user_id = Column(Integer,ForeignKey("user.id"),unique=False,nullable=False,index=True)
    goal = Column(String(255),nullable=False)
    daily_plan = Column(Text,nullable=False)
    week_plan = Column(Text,nullable=False)

    create_time = Column(DateTime,default=func.now(),nullable=False)
    update_time = Column(DateTime,nullable=True)

    start_time = Column(DateTime,nullable=False)
    end_time = Column(DateTime,nullable=False)
    stauts = Column(String(50),default="未完成",nullable=False)