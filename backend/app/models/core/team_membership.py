from sqlalchemy import Column, Integer, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class TeamMembership(Base):
    """
    Many-to-many membership between users and teams (company-scoped).
    Roles remain global on User; this table only expresses membership.
    """

    __tablename__ = "team_memberships"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="ux_team_memberships_team_user"),
        Index("ix_team_memberships_company_team", "company_id", "team_id"),
        Index("ix_team_memberships_company_user", "company_id", "user_id"),
    )

    company = relationship("Company")
    team = relationship("Team", back_populates="memberships")
    user = relationship("User", back_populates="team_memberships")

