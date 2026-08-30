"""View-level tests for the web dashboard."""

from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from acquisition.engine import AcquisitionError
from cases.models import Case, Investigator
from devices.models import Device
from evidence.services import register_artifact, register_evidence


def make_world():
    inv = Investigator.objects.create(name="Grace Hopper", badge_id="D-01")
    case = Case.objects.create(case_number="CASE-DASH-1", title="Dash Test",
                               lead_investigator=inv)
    device = Device.objects.create(
        serial_number="SER-DASH-1", manufacturer="google", model="Pixel 8",
        api_level=34)
    ev = register_evidence(case, device=device, title="dashboard evidence")
    register_artifact(ev, name="note.txt", source="memo",
                      metadata={"notes": "rendezvous at pier 7"})
    return case, ev


def login(client, *, staff=False):
    user = User.objects.create_user("ops", password="pw", is_staff=staff)
    client.login(username="ops", password="pw")
    return user


class AuthGateTest(TestCase):
    def test_anonymous_redirected_to_login(self):
        r = self.client.get("/dashboard/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dashboard/login/", r.url)

    def test_login_page_renders(self):
        r = self.client.get("/dashboard/login/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Sign in")


class HomeAndCaseTest(TestCase):
    def setUp(self):
        self.case, self.ev = make_world()
        login(self.client)

    def test_home_lists_case(self):
        r = self.client.get("/dashboard/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "CASE-DASH-1")

    def test_case_detail_200(self):
        r = self.client.get(f"/dashboard/case/{self.case.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "dashboard evidence")

    def test_evidence_detail_200(self):
        r = self.client.get(f"/dashboard/evidence/{self.ev.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "note.txt")

    def test_artifact_detail_200(self):
        art = self.ev.artifacts.first()
        r = self.client.get(f"/dashboard/artifact/{art.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "note.txt")


class AcquireViewTest(TestCase):
    def test_post_requires_serial(self):
        _, ev = make_world()
        login(self.client)
        r = self.client.post(f"/dashboard/case/{ev.case.pk}/acquire/", {})
        self.assertEqual(r.status_code, 400)

    def test_post_without_adb_fails_gracefully(self):
        _, ev = make_world()
        login(self.client)
        with mock.patch("dashboard.views.acquire",
                        side_effect=AcquisitionError("no adb")):
            r = self.client.post(f"/dashboard/case/{ev.case.pk}/acquire/",
                                 {"serial": "SER-DASH-1", "level": "1"})
        self.assertContains(r, "no adb")


class SearchTest(TestCase):
    def test_search_finds_artifact(self):
        case, _ = make_world()
        login(self.client)
        from searching.service import index_case
        index_case(case)
        r = self.client.get("/dashboard/search/", {"q": "pier"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "note.txt")


class AuditGateTest(TestCase):
    def test_non_staff_denied(self):
        make_world()
        login(self.client, staff=False)
        r = self.client.get("/dashboard/audit/")
        self.assertNotEqual(r.status_code, 200)

    def test_staff_allowed(self):
        _, ev = make_world()
        login(self.client, staff=True)
        r = self.client.get("/dashboard/audit/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Audit log")
