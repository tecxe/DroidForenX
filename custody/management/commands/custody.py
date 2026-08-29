"""Manage chain-of-custody for an evidence item.

Usage:
    python manage.py custody <evidence_id> list|log <ACTION> [--desc DESCRIPTION] [--note NOTE]
"""

from django.core.management.base import BaseCommand, CommandError

from custody.models import CustodyAction
from custody.service import custody_trail, log_event
from evidence.models import Evidence


class Command(BaseCommand):
    help = "Inspect or log chain-of-custody for evidence."

    def add_arguments(self, parser):
        parser.add_argument("evidence_id", type=str)
        sub = parser.add_subparsers(dest="subcommand", required=True)
        list_p = sub.add_parser("list")
        list_p.set_defaults(func="list")
        log_p = sub.add_parser("log")
        log_p.add_argument("action", type=str,
                           choices=list(CustodyAction.values))
        log_p.add_argument("--desc", type=str, default="")
        log_p.add_argument("--note", type=str, default="")
        log_p.set_defaults(func="log")

    def handle(self, *args, **options):
        try:
            evidence = Evidence.objects.get(evidence_id=options["evidence_id"])
        except Evidence.DoesNotExist:
            raise CommandError(f"Evidence {options['evidence_id']} not found.")

        if options["func"] == "list":
            trail = custody_trail(evidence)
            if not trail:
                self.stdout.write(self.style.WARNING("No custody events recorded yet."))
                return
            for e in trail:
                self.stdout.write(f"  [{e.occurred_at.isoformat()}] {e.action}: {e.description}")
            return

        event = log_event(
            evidence,
            options["action"],
            description=options["desc"],
            note=options["note"],
        )
        self.stdout.write(
            self.style.SUCCESS(f"Logged {event.action} on {evidence.evidence_id}.")
        )
