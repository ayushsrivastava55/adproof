"""Append-only audit log.

There is deliberately no update or delete helper. Corrections are new events
(SECURITY_AND_PRIVACY.md s7), and the DB trigger in db.py enforces that even
against direct SQL.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent

#: Actor recorded while this slice has no authentication. Phase 5 replaces this
#: with a real principal. It is never rendered as if a person acted.
SYSTEM_ACTOR = "system:orchestrator"


def record_audit(
    session: Session,
    *,
    workspace_id: str,
    category: str,
    subject_type: str,
    subject_id: str,
    actor: str = SYSTEM_ACTOR,
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        workspace_id=workspace_id,
        category=category,
        subject_type=subject_type,
        subject_id=subject_id,
        actor=actor,
        detail=detail,
    )
    session.add(event)
    session.flush()
    return event


def events_for_subject(
    session: Session, subject_type: str, subject_id: str
) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.subject_type == subject_type,
                AuditEvent.subject_id == subject_id,
            )
            .order_by(AuditEvent.created_at)
        )
    )
