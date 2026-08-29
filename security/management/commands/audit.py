"""View or emit security audit log events.

Usage:
    python manage.py audit        # recent audit events (all types)
    python manage.py audit --type login --limit 50
    python manage.py audit --event ACCESS "evidence EV-2026-00001 viewed" --user alice
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from security.models import AuditEventType
from security.service import audit, recent


class Command(BaseCommand):
    help = "Inspect or emit entries in the (append-only) security audit log."

    def add_arguments(self, parser):
        parser.add_argument("event", nargs="?", type=str, default=None)
        parser.add_argument("detail", nargs="*", type=str)
        parser.add_argument("--list", action="store_true", help="list recent events")
        parser.add_argument("--type", type=str, default=None,
                            choices=list(AuditEventType.values))
        parser.add_argument("--subject", type=str, default="")
        parser.add_argument("--action", type=str, default="")
        parser.add_argument("--user", type=str, default=None)
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        if options["list"] or not options["event"]:
            events = recent(options.get("type"), limit=options["limit"])
            if not events:
                self.stdout.write(self.style.WARNING("No audit events recorded."))
                return
            for e in events:
                self.stdout.write(
                    f"  [{e.created_at.isoformat()}] {e.event_type} "
                    f"user={e.username or '-'} ip={e.ip_address or '-'} "
                    f"{e.action} {e.subject}"
                )
            return

        if options["event"] not in AuditEventType.values:
            raise CommandError(
                f"Unknown event type '{options['event']}'. "
                f"Valid: {', '.join(AuditEventType.values)}"
            )
        actor = None
        if options["user"]:
            actor = get_user_model().objects.filter(username=options["user"]).first()
        ev = audit(
            options["event"],
            actor=actor,
            action=options["action"],
            subject=options["subject"],
            detail=" ".join(options["detail"]),
        )
        self.stdout.write(self.style.SUCCESS(f"Audited {ev.event_type}."))
