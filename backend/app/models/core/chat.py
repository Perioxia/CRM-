from sqlalchemy import BigInteger, Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.sql import func

from app.database import Base

# Each group is created only by its own migration; schema_sync.apply_schema never touches them.
CHAT_TABLES_040 = ("chat_conversations", "chat_participants", "chat_messages")
CHAT_TABLES_041 = ("chat_attachments",)
CHAT_TABLES_042 = ("chat_calls",)
CHAT_TABLES = CHAT_TABLES_040 + CHAT_TABLES_041 + CHAT_TABLES_042


class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    kind = Column(String(16), nullable=False, default="direct")
    # direct: "<lower user id>:<higher user id>" — one conversation per pair.
    # team: "team:<team id>" — one conversation per team.
    direct_key = Column(String(64), nullable=False)
    # Per-conversation message counter, incremented under a row lock on send.
    last_seq = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_message_at = Column(DateTime(timezone=True), nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("company_id", "direct_key", name="uq_chat_conversations_company_direct"),
    )


class ChatParticipant(Base):
    __tablename__ = "chat_participants"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    conversation_id = Column(
        Integer, ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    last_read_seq = Column(Integer, nullable=False, default=0, server_default="0")
    joined_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_chat_participants_conversation_user"),
        Index("ix_chat_participants_user_conversation", "user_id", "conversation_id"),
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    conversation_id = Column(
        Integer, ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False
    )
    seq = Column(Integer, nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body_enc = Column(Text, nullable=False)
    key_id = Column(String(16), nullable=False)
    client_msg_id = Column(String(36), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_chat_messages_conversation_seq"),
        UniqueConstraint("sender_id", "client_msg_id", name="uq_chat_messages_sender_client_msg"),
    )


class ChatAttachment(Base):
    """One file per message, stored on local disk under CHAT_FILES_DIR/<stored_name>."""

    __tablename__ = "chat_attachments"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    conversation_id = Column(
        Integer, ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False
    )
    message_id = Column(Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    uploader_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    stored_name = Column(String(64), nullable=False)
    filename_enc = Column(Text, nullable=False)
    key_id = Column(String(16), nullable=False)
    content_type = Column(String(100), nullable=False)
    category = Column(String(8), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("message_id", name="uq_chat_attachments_message"),
        UniqueConstraint("stored_name", name="uq_chat_attachments_stored_name"),
    )


class ChatCall(Base):
    """One 1-to-1 voice call. Audio and connection details never reach the server; this is the log."""

    __tablename__ = "chat_calls"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    conversation_id = Column(
        Integer, ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False
    )
    caller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    callee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # ringing | active while live; completed | missed | declined | cancelled | busy | unavailable | failed | interrupted once ended
    status = Column(String(12), nullable=False)
    # direct | relay | unknown — how the browsers connected, reported by the caller
    connection = Column(String(8), nullable=False, default="unknown", server_default="unknown")
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    answered_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_s = Column(Integer, nullable=True)
    # The chat-history entry written when the call ends.
    message_id = Column(Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True)

    __table_args__ = (
        UniqueConstraint("message_id", name="uq_chat_calls_message"),
        Index("ix_chat_calls_status", "status"),
    )
