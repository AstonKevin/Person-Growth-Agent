from sqlalchemy import Column,Integer,String,func,ForeignKey,DateTime,Text
from ..database import Base

class Memory(Base):
    __tablename__ = "memory"

    id = Column(Integer,primary_key=True,index=True)
    user_id = Column(Integer,ForeignKey("user.id"),nullable=False)
    memory_type = Column(String(20),default="short")
    content = Column(Text,nullable=False)
    created_at = Column(DateTime, nullable=True)