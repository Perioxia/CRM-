from sqlalchemy import Column, Integer, String, DateTime, Boolean, Enum, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.core.enums import CompanyStatus


class Company(Base):
    """Multi-tenant company entity. All business data is scoped by company_id."""
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    company_code = Column(String(3), unique=True, index=True, nullable=True)
    status = Column(Enum(CompanyStatus, values_callable=lambda x: [e.value for e in x], native_enum=False, length=50), nullable=False, default=CompanyStatus.PENDING, index=True)  # pending, active, suspended, rejected
    plan = Column(String(50), nullable=True)  # free, pro, enterprise
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    require_2fa = Column(Boolean, default=False, nullable=False)
    is_sandbox = Column(Boolean, default=False, nullable=False)
    sandbox_parent_id = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True)
