from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.core.enums import UserRole, UserStatus


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    full_name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(Enum(UserRole, values_callable=lambda x: [e.value for e in x], native_enum=False, length=50), nullable=False, default=UserRole.SALES)
    status = Column(Enum(UserStatus, values_callable=lambda x: [e.value for e in x], native_enum=False, length=20), nullable=False, default=UserStatus.PENDING)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)  # NULL for Platform Admin
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    phone = Column(String(50), nullable=True)
    notification_prefs_json = Column(Text, nullable=True)
    employee_num = Column(Integer, nullable=True)  # Sequential number within company, assigned at creation
    is_active = Column(Boolean, default=True)
    last_active_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    totp_secret = Column(String(255), nullable=True)
    totp_enabled = Column(Boolean, default=False, nullable=False)
    totp_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    token_version = Column(Integer, nullable=False, default=0)

    # Company (Platform Admin may have company_id=NULL)
    company = relationship("Company", backref="users")

    # Multi-team memberships
    team_memberships = relationship("TeamMembership", back_populates="user", cascade="all, delete-orphan")

    # Self-referential for manager hierarchy
    manager = relationship("User", remote_side=[id], backref="direct_reports")
    
    # Lead and Client ownership
    leads = relationship(
        "Lead",
        back_populates="assigned_to",
        foreign_keys="Lead.assigned_to_id",
    )
    clients = relationship("Client", back_populates="assigned_to")
    
    # Tasks
    tasks_assigned = relationship("Task", foreign_keys="Task.assigned_to_id", back_populates="assigned_to")
    tasks_created = relationship("Task", foreign_keys="Task.assigned_by_id", back_populates="assigned_by")
    
    # Follow-ups
    follow_ups = relationship("FollowUp", back_populates="created_by")
    
    # Invoices
    invoices_created = relationship("Invoice", foreign_keys="Invoice.created_by_id", back_populates="created_by")
    invoices_approved = relationship("Invoice", foreign_keys="Invoice.approved_by_id", back_populates="approved_by")
    
    # Notes
    notes = relationship("Note", back_populates="created_by")
