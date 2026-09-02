"""Standalone SIM card forensics — extract SIM data via ADB."""
import re
import logging
from . import adb_manager as adb

logger = logging.getLogger(__name__)


def extract_sim_info(serial: str) -> dict:
    """Extract SIM card information from device."""
    sim = {"slot": 1, "status": "unknown", "imsi": "", "iccid": "", "msisdn": "", "carrier": "", "country": ""}

    ok, state = adb.adb_shell(serial, "getprop gsm.sim.state")
    if ok and state:
        first = state.split(",")[0].strip().lower()
        if "ready" in first:
            sim["status"] = "present"
        elif "absent" in first:
            sim["status"] = "absent"
        elif "pin" in first:
            sim["status"] = "locked_pin"
        elif "puk" in first:
            sim["status"] = "locked_puk"

    ok, carrier = adb.adb_shell(serial, "getprop gsm.sim.operator.alpha")
    if ok and carrier.strip():
        sim["carrier"] = carrier.strip()

    ok, country = adb.adb_shell(serial, "getprop gsm.sim.operator.iso-country")
    if ok and country.strip():
        sim["country"] = country.strip()

    for cmd, field in [
        ("service call iphonesubinfo 7", "imsi"),
        ("service call iphonesubinfo 11", "iccid"),
    ]:
        ok, raw = adb.adb_shell(serial, cmd)
        if ok and raw:
            parts = re.findall(r"'([^']*)'", raw)
            if parts:
                val = "".join(parts).replace("\x00", "").strip()
                if val:
                    sim[field] = val

    ok, msisdn_raw = adb.adb_shell(serial, "service call iphonesubinfo 15")
    if ok and msisdn_raw:
        parts = re.findall(r"'([^']*)'", msisdn_raw)
        if parts:
            val = "".join(parts).replace("\x00", "").strip()
            if val:
                sim["msisdn"] = val

    return sim


def extract_sim_contacts(serial: str) -> list:
    """Extract contacts stored on the SIM card (ADN)."""
    data = adb.query_content(serial, "content://icc/adn")
    contacts = []
    if "Row:" in data:
        for line in data.splitlines():
            if "Row:" not in line:
                continue
            contact = {}
            for field in ["tag", "number", "email"]:
                m = re.search(rf"{field}=([^,]+)", line)
                if m:
                    contact[field] = m.group(1).strip()
            if contact:
                contacts.append(contact)
    return contacts


def extract_sim_messages(serial: str) -> list:
    """Extract SMS messages stored on the SIM card."""
    data = adb.query_content(serial, "content://sms/sim")
    messages = []
    if "Row:" in data:
        for line in data.splitlines():
            if "Row:" not in line:
                continue
            msg = {}
            for field in ["address", "body", "date", "type"]:
                m = re.search(rf"{field}=([^,]+)", line)
                if m:
                    msg[field] = m.group(1).strip()
            if msg:
                messages.append(msg)
    return messages
