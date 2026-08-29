"""Analyze a SQLite evidence artifact (schema, counts, deleted-data).

Usage:
    python manage.py analyze_sqlite <artifact_id>

Reports live tables/row counts and a deleted-data (freelist/remains)
assessment.
"""

from django.core.management.base import BaseCommand, CommandError

from evidence.models import Artifact
from database.service import (
    NoFileError,
    NotSqliteError,
    analyze_artifact_database,
)


class Command(BaseCommand):
    help = "Analyze a SQLite evidence artifact."

    def add_arguments(self, parser):
        parser.add_argument("artifact_id", type=str)

    def handle(self, *args, **options):
        try:
            artifact = Artifact.objects.get(artifact_id=options["artifact_id"])
        except Artifact.DoesNotExist:
            raise CommandError(f"Artifact {options['artifact_id']} not found.")

        try:
            summary = analyze_artifact_database(artifact)
        except NoFileError as exc:
            raise CommandError(str(exc))
        except NotSqliteError as exc:
            self.stdout.write(self.style.WARNING(str(exc)))
            return

        for t in summary["tables"]:
            self.stdout.write(
                self.style.SUCCESS(f"table {t['name']}: {t['row_count']} rows "
                                   f"({', '.join(t['columns'])})")
            )
        d = summary["deleted_assessment"]
        self.stdout.write(
            f"deleted-data: {d['has_deleted_remains']} | freelist pages={d['freelist_count']} "
            f"| pages with remains={d['pages_with_remains']} "
            f"| freeblock bytes={d['total_freeblock_bytes']}"
        )
