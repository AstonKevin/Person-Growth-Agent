from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker,declarative_base

from .core.config import get_settings

# 连接数据库（连接串含密码，统一从 .env 读取，不硬编码）
engine = create_engine(get_settings().database_url)

# 创建对话工厂
SessionLocal = sessionmaker(autoflush=False,bind=engine)

# 模型基类
Base = declarative_base()
""" 所有继承它的 Python 类都会被 SQLAlchemy 自动识别并映射为数据库中的一张表"""

# 依赖项
def get_db():
    db = SessionLocal() # 建立一个session实例
    try:
        yield db # 如何理解这里的yield
        """
        depends依赖注入：先执行depend依赖中的函数，再把依赖的返回值注入到函数中
        在此之前yield抛出db对象，既是返回值
        """
    finally:
        db.close()
