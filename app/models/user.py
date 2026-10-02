from sqlalchemy import Column,Integer,Boolean,Text,String,DateTime,func
from ..database import Base

class User(Base):
    __tablename__ = "user"
    id = Column(Integer,primary_key=True,nullable=False)
    name = Column(String(50),index=True,unique=True,nullable=False)
    password_hash = Column(String(100),nullable=False)
    create_time = Column(DateTime,default=func.now() ,nullable=False)
    update_time = Column(DateTime,nullable=True)
    preference = Column(Text,nullable=True)