"""Search service: index recovered evidence text and run scoped queries.

Indexing only ever includes text that the platform actually recovered onto an
artifact (names, sources, reasons, and stringified metadata values). Querying
never reaches beyond that established index into the live database, so results
are consistent with what the evidence record claims.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector, SearchHeadline
from django.db.models import F, Value

from cases.models import Case
from evidence.models import Artifact

from .models import SearchEntry, SearchResult

logger = logging.getLogger(__name__)


# -- content extraction --------------------------------------------------- #
def _scalar_text(value) -> List[str]:
    parts = []
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, (int, float, bool)):
        parts.append(str(value))
    elif isinstance(value, (list, tuple)):
        for item in value:
            parts.extend(_scalar_text(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            parts.extend(_scalar_text(item))
    return parts


def artifact_search_text(artifact: Artifact) -> str:
    """Build the indexable text blob for an artifact (recovered text only)."""
    parts: List[str] = [artifact.name, artifact.source, artifact.recovery_reason,
                        artifact.artifact_type]
    metadata = artifact.metadata or {}
    parts.extend(_scalar_text(metadata))
    # Collapse whitespace, dedupe.
    seen = set()
    out = []
    for p in parts:
        p = " ".join(str(p).split())
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return "\n".join(out)


# -- indexing ------------------------------------------------------------- #
def index_artifact(artifact: Artifact) -> SearchEntry:
    """Create/refresh the search entry for one artifact."""
    entry, _ = SearchEntry.objects.update_or_create(
        artifact=artifact,
        defaults={"case": artifact.evidence.case, "content": artifact_search_text(artifact)},
    )
    # Store the vector expression (GIN-indexed) from the content column.
    SearchEntry.objects.filter(pk=entry.pk).update(
        search_vector=SearchVector("content", config="english")
    )
    return SearchEntry.objects.get(pk=entry.pk)


def index_case(case: Case) -> int:
    """Re-index all artifacts belonging to a case. Returns count indexed."""
    from evidence.models import Evidence
    count = 0
    for evidence in Evidence.objects.filter(case=case):
        for artifact in evidence.artifacts.all():
            index_artifact(artifact)
            count += 1
    return count


def index_all() -> int:
    """Re-index every artifact. Returns count indexed."""
    count = 0
    for artifact in Artifact.objects.all():
        index_artifact(artifact)
        count += 1
    return count


# -- search --------------------------------------------------------------- #
def search(query: str, *, case: Optional[Case] = None,
           artifact_type: Optional[str] = None, limit: int = 50) -> List[SearchResult]:
    """Run a ranked full-text search over the indexed evidence.

    Scoping: restrict to ``case`` and/or ``artifact_type`` when supplied.
    Returns SearchResult objects ordered by relevance (rank desc).
    """
    q = SearchQuery(query, config="english")
    vector = SearchVector("content", config="english")
    rank = SearchRank(vector, q)

    qs = SearchEntry.objects.filter(search_vector=q)
    if case is not None:
        qs = qs.filter(case=case)
    if artifact_type:
        qs = qs.filter(artifact__artifact_type=artifact_type)

    headline = SearchHeadline("content", q, config="english", highlight_all=True, max_fragments=1)
    rows = qs.annotate(rank=rank, headline=headline).order_by("-rank")[:limit]

    results = []
    for row in rows:
        results.append(SearchResult(
            artifact_id=row.artifact.artifact_id,
            name=row.artifact.name,
            source=row.artifact.source,
            rank=float(row.rank or 0.0),
            snippet=getattr(row, "headline", "") or "",
        ))
    return results
