from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.core.enums import TransferRequestStatus

class TeamTransferRequest(Base):
    __tablename__ = "team_transfer_requests"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  # User being transferred
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    current_team_id = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)
    target_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    reason = Column(String(500), nullable=True)
    status = Column(Enum(TransferRequestStatus, values_callable=lambda x: [e.value for e in x], native_enum=False, length=20), nullable=False, default=TransferRequestStatus.PENDING, index=True)  # pending, approved, rejected
    admin_comment = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    company = relationship("Company", backref="transfer_requests")
    target_user = relationship("User", foreign_keys=[user_id], backref="transfer_pleas")
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    current_team = relationship("Team", foreign_keys=[current_team_id])
    target_team = relationship("Team", foreign_keys=[target_team_id])
