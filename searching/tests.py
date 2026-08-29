"""Tests for the full-text search service."""

from django.test import TestCase

from cases.models import Case, Investigator
from devices.models import Device
from evidence.services import register_artifact, register_evidence

from .models import SearchEntry
from .service import (
    artifact_search_text,
    index_all,
    index_artifact,
    index_case,
    search,
)


def make_case_and_data():
    inv = Investigator.objects.create(name="Ada")
    case = Case.objects.create(case_number="CASE-SEARCH-1", lead_investigator=inv)
    dev = Device.objects.create(serial_number="S1", manufacturer="google", model="Pixel")
    evidence = register_evidence(case, device=dev, title="wa container")
    a = register_artifact(
        evidence, name="msgstore.db", source="WhatsApp database",
        metadata={"social": {"whatsapp": {"notes": "meeting place downtown at 9pm"}}},
    )
    b = register_artifact(
        evidence, name="notepad.txt", source="memo file",
        metadata={"notes": "grocery list"},
    )
    return case, evidence, a, b


class IndexTest(TestCase):
    def test_artifact_search_text_contains_recovered_fields(self):
        case, ev, a, b = make_case_and_data()
        text = artifact_search_text(a)
        self.assertIn("msgstore.db", text)
        self.assertIn("WhatsApp database", text)
        self.assertIn("downtown", text)

    def test_index_artifact_creates_entry(self):
        case, ev, a, b = make_case_and_data()
        entry = index_artifact(a)
        self.assertEqual(SearchEntry.objects.filter(artifact=a).count(), 1)
        self.assertIsNotNone(entry.search_vector)

    def test_index_case_counts(self):
        case, ev, a, b = make_case_and_data()
        self.assertEqual(index_case(case), 2)


class SearchTest(TestCase):
    def setUp(self):
        self.case, self.ev, self.a, self.b = make_case_and_data()
        index_all()

    def test_search_finds_metadata_text(self):
        results = search("downtown")
        self.assertTrue(any(r.artifact_id == self.a.artifact_id for r in results))

    def test_search_scoped_to_case_excludes_others(self):
        inv = Investigator.objects.create(name="Grace")
        other = Case.objects.create(case_number="CASE-OTHER", lead_investigator=inv)
        ev2 = register_evidence(other, title="other")
        register_artifact(ev2, name="x.db", source="s", metadata={"t": "downtown marker"})
        index_all()
        results = search("downtown", case=self.case)
        self.assertTrue(all(r.artifact_id == self.a.artifact_id for r in results))

    def test_search_no_match_empty(self):
        self.assertEqual(search("zzzznomatch123"), [])

    def test_search_ranked_most_relevant_first(self):
        # Both have 'downtown' but 'a' has more of it.
        self.a.metadata["social"]["whatsapp"]["notes"] += " downtown downtown downtown"
        self.a.save()
        index_artifact(self.a)
        results = search("downtown")
        self.assertTrue(results)
        top = results[0]
        self.assertEqual(top.artifact_id, self.a.artifact_id)
