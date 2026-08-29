"""Carve a source artifact for recoverable files and register them.

Usage:
    python manage.py carve_evidence <artifact_id> [--out DIR] [--min-size N]
"""

from django.core.management.base import BaseCommand, CommandError

from evidence.models import Artifact
from carving.service import NoFileError, carve_artifact


class Command(BaseCommand):
    help = "Carve recoverable files from an evidence artifact."

    def add_arguments(self, parser):
        parser.add_argument("artifact_id", type=str)
        parser.add_argument("--out", type=str, default=None)
        parser.add_argument("--min-size", type=int, default=512)

    def handle(self, *args, **options):
        try:
            artifact = Artifact.objects.get(artifact_id=options["artifact_id"])
        except Artifact.DoesNotExist:
            raise CommandError(f"Artifact {options['artifact_id']} not found.")
        out = options["out"]

        try:
            summary = carve_artifact(artifact, min_size=options["min_size"])
        except NoFileError as exc:
            raise CommandError(str(exc))

        self.stdout.write(
            self.style.SUCCESS(
                f"Carved {summary['carved_total']} file(s) "
                f"({summary['recovered_fully']} full, {summary['truncated']} truncated)."
            )
        )
        for name in summary["artifacts"]:
            self.stdout.write(f"  {name}")
        self.stdout.write(f"Output dir: {summary['out_dir']}")
