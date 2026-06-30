# db/models.py
import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Model3D(Base):
    __tablename__ = 'parsed_models'

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    source_url = Column(String(512), nullable=False, unique=True)
    remote_image_url = Column(String(512))
    local_image_path = Column(String(512))
    description = Column(Text)
    downloads_count = Column(Integer, default=0)
    likes_count = Column(Integer, default=0)
    published_at = Column(DateTime)
    estimated_weight_g = Column(Integer, nullable=True)
    estimated_time_min = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)