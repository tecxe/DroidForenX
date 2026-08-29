"""Verify the integrity of evidence artifacts against recorded digests.

Usage:
    python manage.py verify_hashes [artifact_id ...]   # default: all stored artifacts

Recomputes digests for each artifact with a stored file and reports per-algorithm
VERIFIED / MISMATCH / NO FILE / NO RECORD. Exit code is 1 if any mismatch is
found, otherwise 0.
"""

from django.core.management.base import BaseCommand, CommandError

from evidence.models import Artifact
from hashing.service import artifact_disk_path, verify_artifact, verification_passed


class Command(BaseCommand):
    help = "Verify evidence artifact integrity against recorded hashes."

    def add_arguments(self, parser):
        parser.add_argument("artifact_ids", nargs="*", type=str)

    def handle(self, *args, **options):
        ids = options["artifact_ids"]
        qs = Artifact.objects.filter(artifact_id__in=ids) if ids else Artifact.objects.all()

        mismatches = []
        checked = 0
        for art in qs.order_by("artifact_id"):
            if artifact_disk_path(art) is None:
                self.stdout.write(self.style.WARNING(f"{art.artifact_id} {art.name}: NO FILE"))
                continue
            checked += 1
            result = verify_artifact(art)
            if result:
                ok = verification_passed(result)
                label = "VERIFIED" if ok else "MISMATCH"
                style = self.style.SUCCESS if ok else self.style.ERROR
                self.stdout.write(
                    style(f"{art.artifact_id} {art.name}: {label} "
                          f"({', '.join(f'{a.upper()}={str(v)}' for a, v in result.items())})")
                )
                if not ok:
                    mismatches.append(art.artifact_id)
            else:
                self.stdout.write(
                    self.style.WARNING(f"{art.artifact_id} {art.name}: NO RECORD (nothing to verify)")
                )

        self.stdout.write(f"Checked {checked} artifact(s).")
        if mismatches:
            self.stdout.write(self.style.ERROR(f"INTEGRITY FAILURES: {mismatches}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("All verified artifacts are intact."))
