"""Dashboard views: browser UI for the forensic platform.

All views require an authenticated operator. Evidence and report download are
limited to staff by default (audit trail is staff-only).
"""

from pathlib import Path

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from cases.models import Case
from custody.service import custody_trail
from devices.models import Device
from evidence.models import Artifact, Evidence
from reports.models import Report
from reports.service import render_case_report
from security.models import AuditLog, AuditEventType
from security.service import audit

from acquisition.engine import acquire, AcquisitionError


@login_required
def home(request):
    cases = Case.objects.all().order_by("-opened")
    for c in cases:
        c.evidence_count = Evidence.objects.filter(case=c).count()
        c.run_count = c.acquisition_runs.count()
    running = (
        c.acquisition_runs.filter(state="running").count()
        for c in cases
    )
    return render(request, "dashboard/home.html", {
        "cases": cases,
        "device_count": Device.objects.count(),
        "evidence_count": Evidence.objects.count(),
        "artifact_count": Artifact.objects.count(),
        "running_total": sum(1 for _ in running),
    })


@login_required
def case_detail(request, case_pk):
    case = get_object_or_404(Case, pk=case_pk)
    evidence = Evidence.objects.filter(case=case).order_by("-collected_at")
    runs = case.acquisition_runs.all().order_by("-created")
    return render(request, "dashboard/case_detail.html", {
        "case": case,
        "evidence": evidence,
        "runs": runs,
        "devices": Device.objects.all(),
    })


@login_required
def evidence_detail(request, evidence_pk):
    ev = get_object_or_404(Evidence, pk=evidence_pk)
    artifacts = ev.artifacts.all().order_by("name")
    hashes = [a for a in artifacts if a.md5 or a.sha256 or a.sha512]
    trail = custody_trail(ev)
    return render(request, "dashboard/evidence_detail.html", {
        "evidence": ev,
        "artifacts": artifacts,
        "hashed": artifacts.filter(sha256__gt="") if artifacts else [],
        "trail": trail,
    })


@login_required
def artifact_detail(request, artifact_pk):
    art = get_object_or_404(Artifact, pk=artifact_pk)
    hashes = {"MD5": art.md5, "SHA-1": getattr(art, "sha1", ""), "SHA-256": art.sha256,
              "SHA-512": art.sha512}
    return render(request, "dashboard/artifact_detail.html", {
        "artifact": art,
        "evidence": art.evidence,
        "hashes": {k: v for k, v in hashes.items() if v},
        "metadata": (art.metadata or {}),
    })


@require_http_methods(["GET", "POST"])
@login_required
def acquire_view(request, case_pk):
    case = get_object_or_404(Case, pk=case_pk)
    devices = Device.objects.all()

    if request.method == "POST":
        serial = (request.POST.get("serial") or "").strip()
        level = request.POST.get("level", "1")
        method = request.POST.get("method", "adb_logical")
        if not serial:
            return HttpResponseBadRequest("A device serial is required.")
        device = _get_or_create_device(serial)
        try:
            run = acquire(case, device.serial_number, device=device,
                          level=level, method=method, operator=request.user)
            audit(AuditEventType.WRITE, actor=request.user, action="acquisition",
                  subject=case.case_number, ip_address=_ip(request),
                  detail=f"run {run.id} status={run.status}")
            return redirect("dashboard:evidence", evidence_pk=run.evidence_id) \
                if run.evidence_id else redirect("dashboard:case", case_pk=case_pk)
        except AcquisitionError as exc:
            audit(AuditEventType.FAILURE, actor=request.user, action="acquisition",
                  subject=case.case_number, ip_address=_ip(request), detail=str(exc))
            return render(request, "dashboard/acquire.html", {
                "case": case, "devices": devices, "error": str(exc),
            }, status=200)

    return render(request, "dashboard/acquire.html", {
        "case": case, "devices": devices,
    })


@login_required
def search_view(request):
    from searching.service import search
    query = request.GET.get("q", "").strip()
    results = search(query) if query else []
    ids = [r.artifact_id for r in results]
    art_map = {
        a.artifact_id: a
        for a in Artifact.objects.filter(artifact_id__in=ids)
    }
    for r in results:
        a = art_map.get(r.artifact_id)
        r.artifact_pk = a.pk if a else None
        r.case_number = a.evidence.case.case_number if a else None
    return render(request, "dashboard/search.html", {
        "query": query,
        "results": results,
    })


@require_http_methods(["POST"])
@login_required
def report_generate(request, case_pk):
    case = get_object_or_404(Case, pk=case_pk)
    fmt = request.POST.get("format", Report.Format.PDF)
    if fmt not in Report.Format.values:
        return HttpResponseBadRequest("Unsupported report format.")
    report = render_case_report(case, fmt, user=request.user)
    audit(AuditEventType.EXPORT, actor=request.user, action="report",
          subject=case.case_number, ip_address=_ip(request), detail=f"{fmt} report")
    return redirect("dashboard:report_download", report_pk=report.pk)


@login_required
def report_download(request, report_pk):
    report = get_object_or_404(Report, pk=report_pk)
    path = Path(report.path)
    if not path.exists():
        raise PermissionDenied("Report file is missing on disk.")
    return FileResponse(open(path, "rb"), as_attachment=True,
                        filename=path.name)


@staff_member_required(login_url=settings.LOGIN_URL)
def audit_view(request):
    events = AuditLog.objects.all()[:200]
    count_rows = [
        (label, AuditLog.objects.filter(event_type=value).count())
        for value, label in AuditEventType.choices
    ]
    return render(request, "dashboard/audit.html", {
        "events": events, "count_rows": count_rows,
    })


# --------------------------------------------------------------------------- #
def _get_or_create_device(serial):
    device = Device.objects.filter(serial_number=serial).first()
    if device:
        return device
    return Device.objects.create(
        serial_number=serial,
        manufacturer=Device.Manufacturer.GENERIC,
        model=serial,
        connection_state=Device.ConnectionState.CONNECTED,
    )


def _ip(request):
    return request.META.get("REMOTE_ADDR") or None
