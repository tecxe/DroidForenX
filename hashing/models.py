"""HashRecord: provenance of recorded digests for an evidence artifact.

One row per (artifact, algorithm). The artifact's own fast-lookup digest fields
(sha256/md5/sha512) are derived copies; this table is the authoritative, audited
record of every digest and whether it has been re-verified.
"""

from django.db import models


class HashRecord(models.Model):
    """One recorded digest (one algorithm) for one artifact."""

    artifact = models.ForeignKey(
        "evidence.Artifact", on_delete=models.CASCADE, related_name="hash_records"
    )
    algorithm = models.CharField(max_length=16)  # md5 / sha1 / sha256 / sha512
    digest = models.CharField(max_length=128)
    file_path = models.CharField(max_length=1024, blank=True, default="")
    size_bytes = models.BigIntegerField(null=True, blank=True)

    verified_at = models.DateTimeField(null=True, blank=True)
    verified_ok = models.BooleanField(null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["algorithm"]
        constraints = [
            models.UniqueConstraint(
                fields=["artifact", "algorithm"],
                name="uniq_hash_record_algorithm",
            )
        ]
        indexes = [
            models.Index(fields=["artifact", "algorithm"]),
        ]

    def __str__(self):
        return f"{self.algorithm}:{self.digest[:12]}"
