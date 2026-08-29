"""Analyze a social-media/messaging evidence artifact (e.g. WhatsApp db).

Usage:
    python manage.py analyze_social <artifact_id>
"""

from django.core.management.base import BaseCommand, CommandError

from evidence.models import Artifact
from social.service import NoFileError, UnsupportedAppError, analyze_social_artifact


class Command(BaseCommand):
    help = "Analyze a social-media artifact database."

    def add_arguments(self, parser):
        parser.add_argument("artifact_id", type=str)

    def handle(self, *args, **options):
        try:
            artifact = Artifact.objects.get(artifact_id=options["artifact_id"])
        except Artifact.DoesNotExist:
            raise CommandError(f"Artifact {options['artifact_id']} not found.")

        try:
            summary = analyze_social_artifact(artifact)
        except NoFileError as exc:
            raise CommandError(str(exc))
        except UnsupportedAppError as exc:
            self.stdout.write(self.style.WARNING(str(exc)))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"{summary['app']}: {summary['message_count']} messages, "
                f"{summary['contact_count']} contacts, tables: {summary['tables_found']}"
            )
        )
