from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.core.enums import CallDirection


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    direction = Column(
        Enum(CallDirection, values_callable=lambda x: [e.value for e in x], native_enum=False),
        nullable=False,
    )
    duration_seconds = Column(Integer, nullable=True)
    outcome = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    logged_at = Column(DateTime, nullable=False)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True, index=True)
    deal_id = Column(Integer, ForeignKey("deals.id"), nullable=True, index=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    provider = Column(String(20), nullable=True)
    provider_call_id = Column(String(64), nullable=True, index=True)
    from_phone = Column(String(20), nullable=True)
    to_phone = Column(String(20), nullable=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    company = relationship("Company")
    created_by = relationship("User")
