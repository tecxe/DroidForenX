"""Hash an artifact's stored file and record its digests.

Usage:
    python manage.py hash_evidence <artifact_id|ART-xxx> [--alg sha256 sha512]

Writes authoritative HashRecords, the artifact's fast-copy digest fields, and
a standalone .hash manifest beside the stored file.
"""

from django.core.management.base import BaseCommand, CommandError

from evidence.models import Artifact
from hashing.service import hash_artifact


class Command(BaseCommand):
    help = "Hash an evidence artifact and record its digests."

    def add_arguments(self, parser):
        parser.add_argument("artifact_id", type=str)
        parser.add_argument("--alg", nargs="*", default=["md5", "sha1", "sha256", "sha512"])

    def handle(self, *args, **options):
        try:
            artifact = Artifact.objects.get(artifact_id=options["artifact_id"])
        except Artifact.DoesNotExist:
            raise CommandError(f"Artifact {options['artifact_id']} not found.")

        try:
            digests = hash_artifact(artifact, algorithms=options["alg"])
        except Exception as exc:  # MissingFileError etc.
            raise CommandError(str(exc))

        for alg, digest in digests.items():
            self.stdout.write(self.style.SUCCESS(f"{alg.upper()} {digest}  {artifact.name}"))
        self.stdout.write(self.style.SUCCESS(f"Hashed artifact {artifact.artifact_id}."))
