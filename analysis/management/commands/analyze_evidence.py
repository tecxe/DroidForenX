"""Analyze evidence artifacts (type detection + metadata extraction).

Usage:
    python manage.py analyze_evidence [artifact_id ...]
       (default: all artifacts that have a stored file)

Detects each file's type and extracts metadata, writing results into the
artifact records.
"""

from django.core.management.base import BaseCommand, CommandError

from evidence.models import Artifact
from analysis.service import analyze_artifact, NoFileError


class Command(BaseCommand):
    help = "Analyze evidence artifacts (type detection + metadata)."

    def add_arguments(self, parser):
        parser.add_argument("artifact_ids", nargs="*", type=str)

    def handle(self, *args, **options):
        ids = options["artifact_ids"]
        qs = Artifact.objects.filter(artifact_id__in=ids) if ids else Artifact.objects.all()
        done = 0
        for art in qs.order_by("artifact_id"):
            try:
                result = analyze_artifact(art)
            except NoFileError:
                self.stdout.write(self.style.WARNING(f"{art.artifact_id} {art.name}: NO FILE"))
                continue
            done += 1
            kind = result.get("type_description", "unknown type")
            self.stdout.write(self.style.SUCCESS(f"{art.artifact_id} {art.name}: {kind}"))
        self.stdout.write(f"Analyzed {done} artifact(s).")
