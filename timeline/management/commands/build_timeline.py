"""Build and print the forensic timeline for a case.

Usage:
    python manage.py build_timeline <case_number>
"""

from django.core.management.base import BaseCommand, CommandError

from cases.models import Case
from timeline.service import build_timeline


class Command(BaseCommand):
    help = "Build the forensic timeline for a case."

    def add_arguments(self, parser):
        parser.add_argument("case_number", type=str)

    def handle(self, *args, **options):
        try:
            case = Case.objects.get(case_number=options["case_number"])
        except Case.DoesNotExist:
            raise CommandError(f"Case {options['case_number']} not found.")

        result = build_timeline(case)
        events = result["events"]
        self.stdout.write(f"Timeline for {case.case_number}: {len(events)} dated event(s).")
        for ev in events:
            self.stdout.write(
                f"  [{ev['occurred_at']}] {ev['event_type']}: {ev['summary']} (src={ev['source']})"
            )
        for art in result["timeless"]:
            self.stdout.write(self.style.WARNING(f"  (no timestamp) {art}"))
