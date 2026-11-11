from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base


class Team(Base):
    __tablename__ = "teams"
    
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False, index=True)
    __table_args__ = (Index("ix_teams_company_name", "company_id", "name", unique=True),)
    # Note: manager_id removed to avoid circular FK - can be added later with migration
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    company = relationship("Company", backref="teams")
    memberships = relationship("TeamMembership", back_populates="team", cascade="all, delete-orphan")
    leads = relationship("Lead", back_populates="team")
    clients = relationship("Client", back_populates="team")
