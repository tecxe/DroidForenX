"""Index and search forensic evidence.

Usage:
    python manage.py search_evidence "needle" [--case CASE_NUMBER] [--type T] [--rebuild]
    python manage.py search_evidence --rebuild          # re-index everything
"""

from django.core.management.base import BaseCommand, CommandError

from cases.models import Case
from searching.service import index_all, index_case, search


class Command(BaseCommand):
    help = "Full-text search over indexed evidence (and re-indexing)."

    def add_arguments(self, parser):
        parser.add_argument("terms", nargs="*", type=str)
        parser.add_argument("--case", type=str, default=None)
        parser.add_argument("--type", dest="artifact_type", type=str, default=None)
        parser.add_argument("--rebuild", action="store_true")

    def handle(self, *args, **options):
        if options["rebuild"]:
            n = index_all()
            self.stdout.write(self.style.SUCCESS(f"Indexed {n} artifact(s)."))
            return

        query = " ".join(options["terms"]).strip()
        if not query:
            raise CommandError("Provide search terms or use --rebuild.")

        case = None
        if options["case"]:
            try:
                case = Case.objects.get(case_number=options["case"])
            except Case.DoesNotExist:
                raise CommandError(f"Case {options['case']} not found. Run --rebuild first if newly imported.")

        results = search(query, case=case, artifact_type=options.get("artifact_type"))
        self.stdout.write(f"{len(results)} result(s):")
        for r in results:
            self.stdout.write(
                f"  [{r.rank:.2f}] {r.artifact_id} {r.name} ({r.source})"
            )
