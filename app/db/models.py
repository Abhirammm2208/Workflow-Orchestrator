"""
app/db/models.py
SQLAlchemy ORM models for run metadata.

NOTE: LangGraph manages its own checkpoint tables (checkpoints,
checkpoint_writes) via AsyncPostgresSaver.setup(). Those are NOT defined here.
This file owns only the application-level 'runs' table which provides
fast status lookups and list views without deserialising checkpoint JSON.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.database import Base


class Run(Base):
    """
    One row per workflow run. Mirrors the critical fields from AgentState
    so that GET /runs (list) is a cheap SQL query, not a checkpoint scan.
    """

    __tablename__ = "runs"

    # Primary key — same UUID used as LangGraph thread_id
    run_id = Column(String(36), primary_key=True, index=True)

    # Lifecycle status — kept in sync by the API layer after each graph operation
    # Values: running | paused | failed | completed | cancelled | rejected
    status = Column(String(20), nullable=False, default="running", index=True)

    # Denormalised triage outputs for fast filtering/reporting
    category = Column(String(20), nullable=True, index=True)   # Billing | Tech
    urgency = Column(String(10), nullable=True, index=True)    # High | Low

    # Original ticket snapshot (stored for audit / re-trigger without checkpoint)
    ticket_text = Column(Text, nullable=False)
    customer_email = Column(String(255), nullable=False)
    customer_id = Column(String(50), nullable=True)

    # Debug / testing flag stored so retries can clear it
    inject_failure = Column(Boolean, nullable=False, default=False)

    # Retry tracking
    retry_count = Column(Integer, nullable=False, default=0)

    # Reviewer information (populated when human approves/rejects)
    reviewer_note = Column(Text, nullable=True)
    reviewed_by = Column(String(100), nullable=True)

    # Full snapshot of artifacts at last known state (denormalised for quick reads)
    # Updated by the API layer after graph completion / pause / failure
    artifacts_snapshot = Column(JSONB, nullable=True, default=dict)

    # Last error message for quick display without checkpoint deserialisation
    last_error = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
        server_default=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Run id={self.run_id!r} status={self.status!r} "
            f"category={self.category!r} urgency={self.urgency!r}>"
        )

    def to_summary_dict(self) -> dict:
        """Lightweight dict for list endpoints — no heavy fields."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "category": self.category,
            "urgency": self.urgency,
            "customer_email": self.customer_email,
            "customer_id": self.customer_id,
            "retry_count": self.retry_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }
