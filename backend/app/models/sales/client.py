from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Float
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base


class Client(Base):
    __tablename__ = "clients"
    
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    company = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    gstin = Column(String(15), nullable=True)
    
    # Ownership
    assigned_to_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    
    # Converted from lead
    converted_from_lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Relationships
    tenant = relationship("Company", backref="clients", foreign_keys=[company_id])
    assigned_to = relationship("User", back_populates="clients")
    team = relationship("Team", back_populates="clients")
    account = relationship("Account", back_populates="contacts")
    invoices = relationship("Invoice", back_populates="client", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="client", cascade="all, delete-orphan")
    notes_list = relationship("Note", back_populates="client", cascade="all, delete-orphan")
