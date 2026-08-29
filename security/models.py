"""Security audit logging.

An append-only record of security-relevant events: authentication, access to
forensic data, privileged/admin actions, exports, and system failures. The
audit trail is intentionally write-only from the application layer — events
cannot be edited or deleted.
"""

from django.conf import settings
from django.db import models


class AuditEventType(models.TextChoices):
    LOGIN = "login", "Login"
    LOGOUT = "logout", "Logout"
    LOGIN_FAILED = "login_failed", "Login Failed"
    ACCESS = "access", "Data Accessed"
    WRITE = "write", "Data Written"
    EXPORT = "export", "Data Exported"
    ADMIN = "admin", "Admin / Privileged Action"
    SYSTEM = "system", "System Event"
    FAILURE = "failure", "Failure / Error"
    POLICY = "policy", "Policy Change"


class AuditLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    event_type = models.CharField(max_length=24, choices=AuditEventType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_events",
    )
    username = models.CharField(max_length=150, blank=True, default="")
    action = models.CharField(max_length=256, blank=True, default="")
    subject = models.CharField(max_length=256, blank=True, default="")
    detail = models.TextField(blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event_type"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.created_at.isoformat()} {self.event_type} {self.username} {self.action}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise NotImplementedError("Audit log is append-only and cannot be modified.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise NotImplementedError("Audit log is append-only and cannot be deleted.")
