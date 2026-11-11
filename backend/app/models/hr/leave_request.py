from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.core.enums import LeaveStatus


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    from_date = Column(DateTime(timezone=True), nullable=False)
    to_date = Column(DateTime(timezone=True), nullable=False)
    reason = Column(String(500), nullable=True)
    status = Column(Enum(LeaveStatus, values_callable=lambda x: [e.value for e in x], native_enum=False, length=20), nullable=False, default=LeaveStatus.PENDING, index=True)  # Pending, Approved, Rejected
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    company = relationship("Company", backref="leave_requests")
    user = relationship("User", foreign_keys=[user_id], backref="leave_requests")
    approved_by = relationship("User", foreign_keys=[approved_by_id])
