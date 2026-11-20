from sqlalchemy import Column,Integer,String,Text,JSON,Float
from ..config.database import Base
from ..config.settings import CONFIG
class SplitJob(Base):
    __tablename__=CONFIG.get('split_table','session_splits')
    id=Column(Integer,primary_key=True)
    name=Column(String(160),nullable=False)
    state=Column(String(20),default='queued',nullable=False)
    collection_ids=Column(JSON,nullable=False)
    collections=Column(JSON,nullable=False)
    sessions=Column(JSON,nullable=False)
    budget_seconds=Column(Float,nullable=False)
    progress=Column(JSON,nullable=True)
    result=Column(JSON,nullable=True)
    error=Column(Text,default='')
    nodes=Column(Integer,default=0)
    attempts=Column(Integer,default=0)
    created_at=Column(String(40),nullable=False)
    updated_at=Column(String(40),nullable=False)
