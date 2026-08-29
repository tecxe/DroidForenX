"""Wire Django auth signals to the security audit log."""

from django.contrib.auth import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditEventType
from .service import audit


def _request_info(request):
    ip = None
    ua = ""
    if request is not None:
        ip = request.META.get("REMOTE_ADDR") or None
        ua = (request.META.get("HTTP_USER_AGENT") or "")[:512]
    return ip, ua


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    ip, ua = _request_info(request)
    audit(AuditEventType.LOGIN, actor=user, action="authentication",
          subject="login", detail=f"User logged in.", ip_address=ip, user_agent=ua)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    ip, ua = _request_info(request)
    audit(AuditEventType.LOGOUT, actor=user or None, action="authentication",
          subject="logout", detail="User logged out.", ip_address=ip, user_agent=ua)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    ip, ua = _request_info(request)
    username = (credentials or {}).get("username", "")
    audit(AuditEventType.LOGIN_FAILED, username=username, action="authentication",
          subject="login_failed", detail=f"Failed login attempt for '{username}'.",
          ip_address=ip, user_agent=ua)
