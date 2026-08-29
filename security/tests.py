"""Tests for the security audit log."""

from django.contrib.auth import get_user_model, authenticate
from django.test import TestCase

from .models import AuditEventType, AuditLog
from .service import audit, recent


class AuditModelTest(TestCase):
    def test_append_only_no_update(self):
        e = audit(AuditEventType.SYSTEM, action="start", subject="boot")
        with self.assertRaises(NotImplementedError):
            e.detail = "changed"
            e.save()

    def test_append_only_no_delete(self):
        e = audit(AuditEventType.SYSTEM, action="start", subject="boot")
        with self.assertRaises(NotImplementedError):
            e.delete()


class AuditServiceTest(TestCase):
    def test_audit_creates_event_with_username_resolution(self):
        user = get_user_model().objects.create_user("ada")
        e = audit(AuditEventType.ACCESS, actor=user, action="view",
                  subject="evidence/EV-1", detail="opened item")
        self.assertEqual(e.username, "ada")
        self.assertEqual(e.action, "view")
        self.assertEqual(e.subject, "evidence/EV-1")

    def test_recent_filters_by_type(self):
        audit(AuditEventType.LOGIN, username="a")
        audit(AuditEventType.ACCESS, username="a")
        logins = recent(AuditEventType.LOGIN)
        self.assertEqual(len(logins), 1)
        self.assertEqual(logins[0].event_type, AuditEventType.LOGIN)

    def test_unknown_event_type_rejected(self):
        with self.assertRaises(ValueError):
            audit("nonsense")


class AuthSignalTest(TestCase):
    def test_successful_login_recorded(self):
        user = get_user_model().objects.create_user("bob", password="pw12345x")
        self.client.login(username="bob", password="pw12345x")
        self.assertTrue(AuditLog.objects.filter(
            event_type=AuditEventType.LOGIN, username="bob").exists())

    def test_failed_login_recorded(self):
        get_user_model().objects.create_user("carol", password="right")
        # Use authenticate directly to trigger user_login_failed without creating
        # a session or hitting CSRF.
        authenticate(username="carol", password="wrong")
        self.assertTrue(AuditLog.objects.filter(
            event_type=AuditEventType.LOGIN_FAILED, username="carol").exists())
