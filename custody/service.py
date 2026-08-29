"""Chain-of-custody service.

High-level logging of custody events and retrieval of the complete, ordered
trail for an evidence item. Events are strictly append-only.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .models import CustodyAction, CustodyEvent

logger = logging.getLogger(__name__)


def log_event(
    evidence,
    action: str,
    *,
    actor=None,
    investigator=None,
    description: str = "",
    note: str = "",
) -> CustodyEvent:
    """Append a custody event to an evidence item's trail."""
    if action not in CustodyAction.values:
        raise ValueError(f"Unknown custody action: {action}")
    event = CustodyEvent.objects.create(
        evidence=evidence,
        action=action,
        actor=actor,
        investigator=investigator,
        description=description,
        note=note,
    )
    logger.info("Custody %s on %s by %s", action, evidence.evidence_id, actor)
    return event


def custody_trail(evidence) -> List[CustodyEvent]:
    """Return the full, chronological chain-of-custody for an evidence item."""
    return list(CustodyEvent.objects.filter(evidence=evidence).order_by("occurred_at", "id"))


def current_custodian(evidence) -> Optional[CustodyEvent]:
    """The most recent custody event (current custodian / action)."""
    return (
        CustodyEvent.objects.filter(evidence=evidence)
        .order_by("occurred_at", "id")
        .last()
    )


def record_handoff(evidence, *, from_actor=None, to_actor=None, investigator=None,
                   description: str = "") -> CustodyEvent:
    """Record a transfer of custody between two actors."""
    return log_event(
        evidence,
        CustodyAction.TRANSFERRED,
        actor=to_actor,
        investigator=investigator,
        description=description
        or f"Custody transferred{' from ' + str(from_actor) if from_actor else ''}"
           f"{' to ' + str(to_actor) if to_actor else ''}".strip(),
    )


def is_intact(evidence) -> bool:
    """True when the evidence has at least one custody event (a trail exists)."""
    return CustodyEvent.objects.filter(evidence=evidence).exists()
