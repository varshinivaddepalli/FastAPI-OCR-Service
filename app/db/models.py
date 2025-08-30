from sqlalchemy import Column, Integer, String, DateTime, func, Text
from sqlalchemy.dialects.mysql import JSON as MySQLJSON
from app.db.session import Base

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    filename = Column(String(255), nullable=False)
    blob_url = Column(String(1024), nullable=False)
    json_data = Column(MySQLJSON, nullable=True)
    status = Column(String(32), nullable=False, server_default="processing")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
