from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class AIConversation(Base):
    __tablename__ = "ai_conversations"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    idempotency_key = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, default="processing", index=True)
    user_message = Column(Text, nullable=False)
    context_json = Column(Text, nullable=True)
    ai_message = Column(Text, nullable=True)
    ai_reasoning = Column(Text, nullable=True)
    planned_actions_json = Column(Text, nullable=True)
    executed_actions_json = Column(Text, nullable=True)
    error_detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "user_id",
            "idempotency_key",
            name="ux_ai_conversations_company_user_idempotency",
        ),
    )

    company = relationship("Company")
    user = relationship("User")
