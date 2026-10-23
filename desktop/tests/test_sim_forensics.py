"""SIM forensics: parsers against real handset output, no device required.

Every fixture here is trimmed from a TECNO KL6 (Android 14) answering over ADB.
The old module read ``gsm.sim.state`` expecting the word "ready" -- Android 14
says ``LOADED`` -- so it reported ``unknown`` on a perfectly good SIM, took only
the first element of a two-SIM array, and accepted the filler dots from an
unserved ``service call iphonesubinfo`` parcel as an IMSI.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desktop.core import database, sim_forensics

#: getprop output, one entry per slot. Slot 2 has no card.
PROPS = {
    "gsm.sim.state": "LOADED,LOADED,ABSENT",
    "gsm.sim.operator.alpha": "TIGO GH,MTN GH",
    "gsm.sim.operator.iso-country": "gh,gh",
    "gsm.sim.operator.numeric": "62003,62001",
}

#: Trimmed dumpsys isub. Two inserted cards plus two remembered profiles.
ISUB = """
ICCID:
  [SubscriptionInfoInternal: id=1 iccId=892330100[****] simSlotIndex=1 portIndex=0 isEmbedded=0 carrierId=1515 displayName=MTN GH carrierName=MTN GH isOpportunistic=0 displayNameSource=UNKNOWN number= dataRoaming=0 mcc=620 mnc=01 ehplmns= hplmns= cardString=892330100[****] countryIso=gh type=LOCAL_SIM imsi=620010416[****]]
  [SubscriptionInfoInternal: id=3 iccId=892332740[****] simSlotIndex=0 portIndex=0 isEmbedded=0 carrierId=2108 displayName=AT GH carrierName=TIGO GH isOpportunistic=0 displayNameSource=USER_INPUT number= dataRoaming=0 mcc=620 mnc=03 ehplmns= hplmns=62003 countryIso=gh type=LOCAL_SIM imsi=620031077[****]]
  [SubscriptionInfoInternal: id=2 iccId=892332740[****] simSlotIndex=-1 portIndex=-1 isEmbedded=0 carrierId=2108 displayName=TIGO GH carrierName=TIGO GH isOpportunistic=0 number= dataRoaming=0 mcc=620 mnc=03 countryIso=gh imsi=620031071[****]]
  [SubscriptionInfoInternal: id=5 iccId=892330205[****] simSlotIndex=-1 portIndex=-1 isEmbedded=0 carrierId=2383 displayName=Telecel carrierName=No service isOpportunistic=0 number= dataRoaming=0 mcc=620 mnc=02 countryIso=gh imsi=620020573[****]]
"""

ADN_TIGO = """Row: 0 index=1, name=AirtelTigo Money, number=*110#, emails=NULL, additionalNumber=, groupIds=NULL, _id=0, aas=0, sne=NULL
Row: 1 index=2, name=AirtelTigo Menu, number=*111#, emails=NULL, additionalNumber=, groupIds=NULL, _id=1, aas=0, sne=NULL
Row: 2 index=3, name=Doe, John, number=0244123456, emails=john@example.com, additionalNumber=, groupIds=NULL, _id=2, aas=0, sne=NULL
"""

PARcel_JUNK = "Result: Parcel(\t00000000 ffffffff   '........')"


class SimStateParserTest(unittest.TestCase):
    def test_every_slot_is_reported_not_just_the_first(self):
        sims = sim_forensics.parse_sim_states(
            PROPS["gsm.sim.state"], PROPS["gsm.sim.operator.alpha"],
            PROPS["gsm.sim.operator.iso-country"], PROPS["gsm.sim.operator.numeric"])

        self.assertEqual([sim["slot"] for sim in sims], [0, 1, 2])
        self.assertEqual([sim["status"] for sim in sims],
                         ["present", "present", "absent"])

    def test_loaded_counts_as_present(self):
        # Android 14 says LOADED; the old code only looked for "ready".
        sims = sim_forensics.parse_sim_states("LOADED", "TIGO GH", "gh", "62003")
        self.assertEqual(sims[0]["status"], "present")

    def test_carrier_country_and_network_code_are_per_slot(self):
        sims = sim_forensics.parse_sim_states(
            PROPS["gsm.sim.state"], PROPS["gsm.sim.operator.alpha"],
            PROPS["gsm.sim.operator.iso-country"], PROPS["gsm.sim.operator.numeric"])

        self.assertEqual((sims[0]["carrier"], sims[0]["mcc"], sims[0]["mnc"]),
                         ("TIGO GH", "620", "03"))
        self.assertEqual((sims[1]["carrier"], sims[1]["mcc"], sims[1]["mnc"]),
                         ("MTN GH", "620", "01"))
        self.assertEqual(sims[1]["country"], "GH")

    def test_locked_card_states_are_not_folded_into_present(self):
        sims = sim_forensics.parse_sim_states("PIN_REQUIRED", "", "", "")
        self.assertEqual(sims[0]["status"], "locked_pin")


class IsubParserTest(unittest.TestCase):
    def setUp(self):
        self.subs = sim_forensics.parse_isub(ISUB)

    def test_all_four_subscription_records_are_kept(self):
        self.assertEqual(len(self.subs), 4)

    def test_inserted_cards_carry_slot_iccid_imsi_and_network(self):
        inserted = {sub["slot"]: sub for sub in self.subs if sub["slot"] >= 0}
        self.assertEqual(set(inserted), {0, 1})
        self.assertEqual(inserted[0]["iccid"], "892332740")
        self.assertEqual(inserted[0]["imsi"], "620031077")
        self.assertEqual(inserted[0]["carrier"], "TIGO GH")
        self.assertEqual((inserted[0]["mcc"], inserted[0]["mnc"]), ("620", "03"))
        self.assertEqual(inserted[1]["carrier"], "MTN GH")
        self.assertEqual(inserted[1]["subscription_id"], 1)

    def test_masked_identifiers_are_flagged_not_padded(self):
        tigo = next(sub for sub in self.subs if sub["slot"] == 0)
        self.assertIn("iccid", tigo["redacted"])
        self.assertIn("imsi", tigo["redacted"])
        self.assertNotIn("*", tigo["iccid"])
        self.assertEqual(len(tigo["iccid"]), 9)

    def test_removed_cards_are_remembered_separately(self):
        remembered = [sub for sub in self.subs if sub["slot"] < 0]
        self.assertEqual(len(remembered), 2)
        self.assertIn("Telecel", {sub["display_name"] for sub in remembered})

    def test_carrier_names_with_spaces_survive(self):
        telecel = next(sub for sub in self.subs if sub["display_name"] == "Telecel")
        self.assertEqual(telecel["carrier"], "No service")


class ParcelParserTest(unittest.TestCase):
    def test_filler_parcels_are_not_identifiers(self):
        # This is what Android 14 returns for an unserved iphonesubinfo call.
        self.assertEqual(sim_forensics.parse_parcel_identifier(PARcel_JUNK, "imsi"), "")

    def test_zeroed_parcels_are_not_identifiers(self):
        junk = "Result: Parcel(\t00000000 00000000 00000000  '............')"
        self.assertEqual(sim_forensics.parse_parcel_identifier(junk, "msisdn"), "")

    def test_a_real_parcel_payload_is_kept(self):
        raw = "Result: Parcel(\t00000000 0000000f 00360033 00320031 '........6.2.0.0.3.1.0.7.7')"
        self.assertEqual(sim_forensics.parse_parcel_identifier(raw, "imsi"), "620031077")


class AdnParserTest(unittest.TestCase):
    def test_contacts_come_back_with_numbers(self):
        contacts = sim_forensics.parse_adn(ADN_TIGO)
        self.assertEqual(len(contacts), 3)
        self.assertEqual(contacts[0]["name"], "AirtelTigo Money")
        self.assertEqual(contacts[0]["number"], "*110#")
        self.assertEqual(contacts[0]["email"], "")
        self.assertEqual(contacts[2]["adn_index"], "3")

    def test_a_comma_inside_a_name_does_not_split_the_row(self):
        contacts = sim_forensics.parse_adn(ADN_TIGO)
        self.assertEqual(contacts[2]["name"], "Doe, John")
        self.assertEqual(contacts[2]["number"], "0244123456")
        self.assertEqual(contacts[2]["email"], "john@example.com")

    def test_empty_provider_answers_yield_nothing(self):
        self.assertEqual(sim_forensics.parse_adn("No result found."), [])
        self.assertEqual(sim_forensics.parse_sim_sms("No result found."), [])


class ReadSimCardsTest(unittest.TestCase):
    """End-to-end read against a scripted ADB, so no handset is needed."""

    def _shell(self, serial, command, timeout=30):
        key = command.strip()
        if key == "dumpsys isub":
            return True, ISUB
        if key.startswith("service call"):
            return True, PARcel_JUNK
        return True, PROPS.get(key, "")

    def _query(self, serial, uri, sort=""):
        if uri.endswith("/subId/3"):
            return ADN_TIGO
        if uri.endswith("/subId/1"):
            return "No result found."
        if "sms/sim" in uri:
            return "No result found."
        return ""

    def _read(self):
        with mock.patch.object(sim_forensics.adb, "adb_shell", side_effect=self._shell), \
             mock.patch.object(sim_forensics.adb, "query_content", side_effect=self._query):
            return sim_forensics.read_sim_cards("SERIAL")

    def test_two_cards_are_merged_from_props_and_isub(self):
        result = self._read()
        present = [sim for sim in result["sims"] if sim["status"] == "present"]
        self.assertEqual(len(present), 2)
        self.assertEqual({sim["carrier"] for sim in present}, {"TIGO GH", "MTN GH"})
        self.assertTrue(all(sim["iccid"] for sim in present))

    def test_contacts_are_attributed_to_the_card_they_live_on(self):
        result = self._read()
        tigo = next(sim for sim in result["sims"] if sim["carrier"] == "TIGO GH")
        mtn = next(sim for sim in result["sims"] if sim["carrier"] == "MTN GH")
        self.assertEqual(len(tigo["contacts"]), 3)
        self.assertEqual(mtn["contacts"], [])

    def test_an_empty_provider_is_reported_as_empty_not_missing(self):
        result = self._read()
        self.assertEqual(result["sources"]["content://sms/sim"], "empty")
        self.assertEqual(result["sources"]["content://icc/adn"], "ok")

    def test_legacy_wrapper_still_returns_a_flat_record(self):
        with mock.patch.object(sim_forensics.adb, "adb_shell", side_effect=self._shell), \
             mock.patch.object(sim_forensics.adb, "query_content", side_effect=self._query):
            info = sim_forensics.extract_sim_info("SERIAL")
        self.assertEqual(info["status"], "present")
        self.assertEqual(info["carrier"], "TIGO GH")
        self.assertNotIn(".", info["imsi"])


class StoreSimCardsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-sim-"))
        self._restore = database.DB_PATH
        database.DB_PATH = self.tmp / "droidforenx.db"
        database.init_db()
        self._session("session-1")
        self._session("session-2")

    def _session(self, session_id):
        conn = database.get_db()
        try:
            conn.execute(
                "INSERT INTO forensic_sessions (id, case_id, status, current_stage,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (session_id, "", "in_progress", 1, "2026-10-23T00:00:00+00:00",
                 "2026-10-23T00:00:00+00:00"))
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        database.DB_PATH = self._restore
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _result(self):
        return {
            "extracted_at": "2026-10-23T00:00:00+00:00",
            "sims": [
                {"slot": 0, "status": "present", "iccid": "892332740",
                 "imsi": "620031077", "msisdn": "", "carrier": "TIGO GH",
                 "country": "GH",
                 "contacts": [{"name": "Doe, John", "number": "0244123456",
                               "email": "", "adn_index": "3"}],
                 "messages": [{"sender": "+233200000000", "body": "hello",
                               "timestamp": "1", "status": "1"}]},
                {"slot": 1, "status": "present", "iccid": "892330100",
                 "imsi": "620010416", "msisdn": "", "carrier": "MTN GH",
                 "country": "GH", "contacts": [], "messages": []},
            ],
        }

    def _counts(self):
        conn = database.get_db()
        try:
            return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                         for table in ("sim_cards", "sim_contacts", "sim_messages"))
        finally:
            conn.close()

    def test_extraction_is_written_with_its_contacts_and_messages(self):
        counts = sim_forensics.store_sim_cards("session-1", self._result())
        self.assertEqual(counts, {"sims": 2, "contacts": 1, "messages": 1,
                                  "persisted": True})
        self.assertEqual(self._counts(), (2, 1, 1))

    def test_re_running_replaces_rather_than_duplicates(self):
        sim_forensics.store_sim_cards("session-1", self._result())
        sim_forensics.store_sim_cards("session-1", self._result())
        self.assertEqual(self._counts(), (2, 1, 1))

    def test_an_extraction_without_a_session_is_not_silently_filed(self):
        counts = sim_forensics.store_sim_cards("", self._result())
        self.assertFalse(counts["persisted"])
        self.assertEqual(counts["reason"], "no active session")
        self.assertEqual(self._counts(), (0, 0, 0))

    def test_another_session_is_left_alone(self):
        sim_forensics.store_sim_cards("session-1", self._result())
        sim_forensics.store_sim_cards("session-2", self._result())
        self.assertEqual(self._counts(), (4, 2, 2))


if __name__ == "__main__":
    unittest.main()


