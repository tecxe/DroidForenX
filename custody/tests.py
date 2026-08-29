"""Tests for chain of custody (append-only trail)."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from cases.models import Case, Investigator
from evidence.services import register_evidence

from .models import CustodyAction, CustodyEvent
from .service import (
    current_custodian,
    custody_trail,
    is_intact,
    log_event,
    record_handoff,
)


def make_evidence():
    inv = Investigator.objects.create(name="Ada")
    case = Case.objects.create(case_number="CASE-CUST-1", lead_investigator=inv)
    return register_evidence(case, title="custody item")


class CustodyModelTest(TestCase):
    def test_append_only_no_update(self):
        ev = make_evidence()
        e = log_event(ev, CustodyAction.ACQUIRED, description="acquired image")
        with self.assertRaises(NotImplementedError):
            e.description = "changed"
            e.save()

    def test_append_only_no_delete(self):
        ev = make_evidence()
        e = log_event(ev, CustodyAction.ACQUIRED)
        with self.assertRaises(NotImplementedError):
            e.delete()


class CustodyServiceTest(TestCase):
    def test_log_and_trail_order(self):
        ev = make_evidence()
        log_event(ev, CustodyAction.ACQUIRED, description="first")
        log_event(ev, CustodyAction.HASHED, description="second")
        log_event(ev, CustodyAction.ANALYZED, description="third")
        trail = custody_trail(ev)
        self.assertEqual(len(trail), 3)
        self.assertEqual([t.description for t in trail], ["first", "second", "third"])

    def test_current_custodian_is_last(self):
        ev = make_evidence()
        log_event(ev, CustodyAction.ACQUIRED)
        log_event(ev, CustodyAction.HASHED)
        last = log_event(ev, CustodyAction.EXPORTED)
        self.assertEqual(current_custodian(ev).pk, last.pk)

    def test_record_handoff(self):
        ev = make_evidence()
        User = get_user_model()
        alice = User.objects.create_user("alice", password="pw")
        bob = User.objects.create_user("bob", password="pw")
        e = record_handoff(ev, from_actor=alice, to_actor=bob)
        self.assertEqual(e.action, CustodyAction.TRANSFERRED)
        self.assertIn("alice", e.description)
        self.assertIn("bob", e.description)

    def test_is_intact(self):
        ev = make_evidence()
        self.assertFalse(is_intact(ev))
        log_event(ev, CustodyAction.STORED)
        self.assertTrue(is_intact(ev))

    def test_unknown_action_rejected(self):
        ev = make_evidence()
        with self.assertRaises(ValueError):
            log_event(ev, "not-an-action")
