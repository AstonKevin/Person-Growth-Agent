from sqlalchemy import Column,Integer,String,DateTime,func,ForeignKey
from ..database import Base

class UsageLog(Base):
    """LLM 用量审计：只记元数据，不记 prompt / 目标内容。"""
    __tablename__ = "usage_log"
    id = Column(Integer,primary_key=True,nullable=False)
    user_id = Column(Integer,ForeignKey("user.id"),nullable=False,index=True)

    created_at = Column(DateTime,default=func.now(),nullable=False,index=True)
    model = Column(String(100),nullable=False)
    provider = Column(String(20),nullable=True)
    status = Column(String(20),nullable=False)        # success / error

    prompt_tokens = Column(Integer,nullable=True)
    completion_tokens = Column(Integer,nullable=True)
    latency_ms = Column(Integer,nullable=True)
    error_code = Column(String(50),nullable=True)
