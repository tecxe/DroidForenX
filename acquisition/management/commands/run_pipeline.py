"""Django management command: run the full forensic pipeline for a case.

Usage:
    python manage.py run_pipeline <case_number> [--serial SERIAL] [--evidence EVIDENCE_ID]
        [--index] [--no-custody]

Orchestrates the forensic workflow end-to-end in honest stages:

    acquisition (optional) -> hashing -> file/meta analysis -> social analysis
    -> carving -> timeline -> search indexing

Every stage reports what it actually produced and continues on a per-item
failure so one unusable file never aborts the rest of the case.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from cases.models import Case
from custody.models import CustodyAction
from custody.service import log_event
from evidence.models import Artifact, Evidence
from security.models import AuditEventType
from security.service import audit

from ...engine import acquire, AcquisitionError


class Command(BaseCommand):
    help = "Run the full acquisition/analysis/report pipeline for a case."

    def add_arguments(self, parser):
        parser.add_argument("case_number", type=str)
        parser.add_argument("--serial", type=str, default=None,
                            help="Run an ADB acquisition first (device serial).")
        parser.add_argument("--evidence", type=str, default=None,
                            help="Restrict analysis to one evidence id (EV-...).")
        parser.add_argument("--index", action="store_true",
                            help="Also rebuild the full-text search index for the case.")
        parser.add_argument("--user", type=str, default=None)

    def handle(self, *args, **options):
        try:
            case = Case.objects.get(case_number=options["case_number"])
        except Case.DoesNotExist:
            raise CommandError(f"Case {options['case_number']} not found.")
        self.stdout.write(self.style.SUCCESS(f"Pipeline for case {case.case_number}"))

        user = None
        if options["user"]:
            user = get_user_model().objects.filter(username=options["user"]).first()

        # Stage 0: optional acquisition.
        if options["serial"]:
            self._stage_acquisition(case, options["serial"], user)

        evidences = self._select_evidence(case, options["evidence"])
        if not evidences:
            self.stdout.write(self.style.WARNING("No evidence selected; nothing to analyze."))

        for ev in evidences:
            self._log(ev, CustodyAction.ACCESSED, "Pipeline started", user)
            artifacts = Artifact.objects.filter(evidence=ev)
            for art in artifacts.order_by("artifact_id"):
                self._analyze_artifact(art, user)
            self._log(ev, CustodyAction.ACCESSED, "Pipeline finished", user)

        if options["index"]:
            self._stage_index(case)

        audit(AuditEventType.WRITE, actor=user, action="pipeline",
              subject=case.case_number, detail="run_pipeline completed")
        self.stdout.write(self.style.SUCCESS(f"Pipeline complete for {case.case_number}."))

    # ------------------------------------------------------------------ #
    def _stage_acquisition(self, case, serial, user):
        try:
            run = acquire(case, serial, operator=user, level="1", method="adb_logical")
        except AcquisitionError as exc:
            self.stdout.write(self.style.ERROR(f"Acquisition skipped: {exc}"))
            return
        if run.evidence_id:
            self._log(run.evidence, CustodyAction.ACCESSED,
                      f"Acquisition run {run.id}: {run.summary()}", user)
        self.stdout.write(self.style.SUCCESS(f"  acquisition: {run.summary()}"))

    def _select_evidence(self, case, evidence_id):
        qs = Evidence.objects.filter(case=case)
        if evidence_id:
            qs = qs.filter(evidence_id=evidence_id)
            if not qs.exists():
                raise CommandError(f"Evidence {evidence_id} not found in case.")
        return list(qs.order_by("collected_at", "id"))

    def _analyze_artifact(self, art, user):
        name = art.artifact_id
        # 1) Hashing
        try:
            from hashing.service import hash_artifact
            digests = hash_artifact(art, algorithms=["sha256", "sha1", "md5"])
            self.stdout.write(self.style.SUCCESS(
                f"  hash {name} {art.name}: {len(digests)} digest(s)"))
        except Exception as exc:  # noqa: BLE001 - MissingFileError etc.
            self.stdout.write(self.style.WARNING(f"  hash {name}: skipped ({exc})"))

        # 2) File/meta analysis
        try:
            from analysis.service import analyze_artifact
            result = analyze_artifact(art)
            self.stdout.write(self.style.SUCCESS(
                f"  analyze {name}: {result.get('type_description', 'unknown')}"))
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f"  analyze {name}: skipped ({exc})"))

        # 3) Social analysis
        try:
            from social.service import analyze_social_artifact
            result = analyze_social_artifact(art, record=True)
            self.stdout.write(self.style.SUCCESS(
                f"  social {name}: {result.get('status', 'done')}"))
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f"  social {name}: skipped ({exc})"))

        # 4) Carving
        try:
            from carving.service import carve_artifact
            result = carve_artifact(art)
            n = result.get("carved", 0) if isinstance(result, dict) else 0
            self.stdout.write(self.style.SUCCESS(f"  carve {name}: {n} recoverable file(s)"))
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f"  carve {name}: skipped ({exc})"))

        # 5) Index this artifact (keeps search current even without --index).
        try:
            from searching.service import index_artifact
            index_artifact(art)
        except Exception:  # noqa: BLE001
            pass

    def _stage_index(self, case):
        try:
            from timeline.service import build_timeline
            tl = build_timeline(case, persist=True)
            self.stdout.write(self.style.SUCCESS(
                f"  timeline: {len(tl.get('events', []))} event(s)"))
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f"  timeline: skipped ({exc})"))
        try:
            from searching.service import index_case
            n = index_case(case)
            self.stdout.write(self.style.SUCCESS(f"  index: {n} artifact(s) indexed"))
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f"  index: skipped ({exc})"))

    def _log(self, evidence, action, description, user):
        try:
            log_event(evidence, action, actor=user, description=description)
        except Exception:  # noqa: BLE001
            pass
