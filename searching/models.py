"""Full-text search index over recovered evidence content.

Uses PostgreSQL's native full-text search (contrib.postgres) so queries are
scoped, ranked, and fast. ``SearchEntry`` holds one normalized, indexable text
blob per artifact, built from only the text that the platform actually
recovered (names, sources, message bodies, extracted metadata strings). The
search vector is recomputed whenever the entry is re-indexed.
"""

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models


class SearchEntry(models.Model):
    """One full-text search entry derived from an artifact."""

    artifact = models.OneToOneField(
        "evidence.Artifact", on_delete=models.CASCADE, related_name="search_entry"
    )
    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="search_entries")
    content = models.TextField(blank=True, default="")
    search_vector = SearchVectorField(null=True, editable=False)

    updated = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            GinIndex(fields=["search_vector"], name="search_entry_gin"),
        ]

    def __str__(self):
        return f"SearchEntry({self.artifact.artifact_id})"


class SearchResult:
    """Lightweight, honest search hit (not a DB row)."""

    def __init__(self, artifact_id, name, source, rank, snippet=""):
        self.artifact_id = artifact_id
        self.name = name
        self.source = source
        self.rank = rank
        self.snippet = snippet

    def __repr__(self):
        return f"<SearchResult {self.artifact_id} score={self.rank:.2f}>"
