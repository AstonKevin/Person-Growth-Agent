from sqlalchemy import Column,Integer,Boolean,Text,String,DateTime,func,ForeignKey
from ..database import Base

class UserProvider(Base):
    """用户自带的 LLM 接入配置（BYOK）：每用户 1 条。
    api_key 加密存储，任何接口/后台都不展示明文。"""
    __tablename__ = "user_provider"
    id = Column(Integer,primary_key=True,nullable=False)
    user_id = Column(Integer,ForeignKey("user.id"),unique=True,nullable=False,index=True)

    provider = Column(String(20),default="openai",nullable=False)
    base_url = Column(String(255),nullable=False)
    encrypted_api_key = Column(Text,nullable=False)
    model = Column(String(100),nullable=False)

    verified = Column(Boolean,default=False,nullable=False)
    last_verified_at = Column(DateTime,nullable=True)

    create_time = Column(DateTime,default=func.now(),nullable=False)
    update_time = Column(DateTime,nullable=True)
