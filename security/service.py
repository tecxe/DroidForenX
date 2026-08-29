"""Security audit service."""

from __future__ import annotations

import logging
from typing import Optional

from .models import AuditEventType, AuditLog

logger = logging.getLogger(__name__)


def audit(
    event_type: str,
    *,
    actor=None,
    username: str = "",
    action: str = "",
    subject: str = "",
    detail: str = "",
    ip_address=None,
    user_agent: str = "",
) -> AuditLog:
    """Append a security audit event."""
    if event_type not in AuditEventType.values:
        raise ValueError(f"Unknown audit event type: {event_type}")
    event = AuditLog.objects.create(
        event_type=event_type,
        actor=actor,
        username=username or (actor.get_username() if actor else ""),
        action=action,
        subject=subject,
        detail=detail,
        ip_address=ip_address,
        user_agent=user_agent[:512],
    )
    logger.info("Audit %s: %s %s %s", event_type, event.username, action, subject)
    return event


def recent(event_type: Optional[str] = None, limit: int = 100):
    qs = AuditLog.objects.all()
    if event_type:
        qs = qs.filter(event_type=event_type)
    return list(qs[:limit])
