"""SIM card forensics over ADB.

What an unrooted handset will actually tell a shell user, and how to tell the
difference between "there is nothing here" and "I was not allowed to look":

  * ``getprop gsm.sim.state`` / ``operator.alpha`` / ``operator.iso-country`` /
    ``operator.numeric`` hold **comma-separated arrays, one entry per slot**.
    Reading only the first element silently reports one SIM on a dual-SIM phone,
    and looking for the word "ready" reports ``unknown`` on Android 14, which
    says ``LOADED``.
  * ``dumpsys isub`` enumerates every subscription record: ICCID, IMSI, carrier,
    display name, slot, MCC/MNC and country. Android 12+ masks the tail of
    ICCID/IMSI for non-system UIDs, so the digits arrive as ``892330100[****]``.
    That is recorded as an observation and flagged as redacted, never padded
    into a pretend-full identifier.
  * ``content://icc/adn`` returns the ADN records. On dual-SIM devices the bare
    URI only reaches the default subscription; ``.../adn/subId/<id>`` reaches
    each one, so contacts can be attributed to the card they live on.
  * ``service call iphonesubinfo N`` is a last-resort parcel read. On modern
    Android it answers with filler -- ``Result: Parcel( 00000000 ffffffff
    '........')`` -- and the old parser accepted those dots as an IMSI.
  * ``content://sms/sim`` normally answers ``No result found.``, which means
    zero messages, not a failure.

Every reader returns provenance alongside the value so the UI can say
NOT_ACCESSIBLE rather than show a confident blank.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import adb_manager as adb

logger = logging.getLogger(__name__)

#: ``gsm.sim.state`` values, lowercased, mapped to the words an examiner uses.
_SIM_STATES = {
    "loaded": "present",
    "ready": "present",
    "absent": "absent",
    "not_ready": "not_ready",
    "pin_required": "locked_pin",
    "puk_required": "locked_puk",
    "network_locked": "network_locked",
    "permanent_blocked": "blocked",
    "card_io_error": "card_error",
}

#: A parcel answer is only trusted if it carries at least this many digits.
_MIN_IDENTIFIER_DIGITS = {"imsi": 6, "iccid": 6, "msisdn": 5}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _split_prop(raw: str) -> List[str]:
    """Split a comma-separated getprop array, preserving slot positions."""
    if not raw:
        return []
    return [part.strip() for part in raw.strip().split(",")]


def _at(parts: List[str], index: int) -> str:
    return parts[index] if index < len(parts) else ""


def parse_sim_states(raw_state: str = "", raw_alpha: str = "",
                     raw_country: str = "", raw_numeric: str = "") -> List[Dict]:
    """One record per physical slot, from the parallel ``gsm.sim.*`` arrays."""
    states = _split_prop(raw_state)
    alphas = _split_prop(raw_alpha)
    countries = _split_prop(raw_country)
    numerics = _split_prop(raw_numeric)

    slots = max(len(states), len(alphas), len(countries), len(numerics))
    sims = []
    for slot in range(slots):
        token = _at(states, slot).lower()
        status = _SIM_STATES.get(token, "unknown" if token else "unknown")
        numeric = _at(numerics, slot)
        mcc, _, mnc = numeric.partition("-")
        if numeric and not mnc and len(numeric) >= 5:
            # gsm.sim.operator.numeric is MCC+MNC with no separator (62003).
            mcc, mnc = numeric[:3], numeric[3:]
        sims.append({
            "slot": slot,
            "status": status,
            "raw_state": _at(states, slot),
            "carrier": _at(alphas, slot),
            "country": _at(countries, slot).upper(),
            "mcc": mcc,
            "mnc": mnc,
            "numeric": numeric,
            "iccid": "",
            "imsi": "",
            "msisdn": "",
            "display_name": "",
            "subscription_id": None,
            "redacted": [],
            "source": "getprop",
        })
    return sims


def _field(record: str, name: str) -> Optional[str]:
    match = re.search(rf"\b{re.escape(name)}=([^\s,]*)", record)
    return match.group(1) if match else None


def _clean(value: Optional[str]) -> str:
    """Drop the redaction marker and the closing bracket from an isub value."""
    if not value:
        return ""
    return value.split("[")[0].rstrip("]")


def _spaced_field(record: str, key: str, until: str) -> Optional[str]:
    """Read a value that may contain spaces (``carrierName=No service``)."""
    match = re.search(rf"\b{re.escape(key)}=(.*?)\s+{re.escape(until)}=", record)
    return match.group(1).strip() if match else None


def parse_isub(raw: str) -> List[Dict]:
    """Parse ``dumpsys isub`` into subscription records.

    Returns every record, including profiles whose ``simSlotIndex`` is -1:
    those are subscriptions the device still remembers but no longer has a
    card for, which is exactly the kind of history an examiner wants.
    """
    if not raw:
        return []

    records = []
    chunks = re.split(r"\[?SubscriptionInfoInternal:", raw)[1:]
    for chunk in chunks:
        subscription_id = _field(chunk, "id")
        slot_raw = _field(chunk, "simSlotIndex")
        if subscription_id is None or slot_raw is None:
            continue
        try:
            slot = int(slot_raw)
        except ValueError:
            continue

        iccid_raw = _field(chunk, "iccId") or ""
        imsi_raw = _field(chunk, "imsi") or ""
        number_raw = _field(chunk, "number") or ""

        redacted = []
        if "[****]" in iccid_raw:
            redacted.append("iccid")
        if "[****]" in imsi_raw:
            redacted.append("imsi")

        records.append({
            "subscription_id": int(subscription_id),
            "slot": slot,
            "iccid": _clean(iccid_raw),
            "imsi": _clean(imsi_raw),
            "msisdn": _clean(number_raw),
            "display_name": _spaced_field(chunk, "displayName", "carrierName") or "",
            "carrier": _spaced_field(chunk, "carrierName", "isOpportunistic") or "",
            "mcc": _field(chunk, "mcc") or "",
            "mnc": _field(chunk, "mnc") or "",
            "country": (_field(chunk, "countryIso") or "").upper(),
            "redacted": redacted,
            "source": "dumpsys isub",
        })

    # dumpsys prints some records more than once (one per active phone account).
    # Collapse exact repeats but keep genuinely distinct profiles: a device can
    # remember two subscriptions that share an ICCID and differ only by IMSI.
    seen = set()
    unique = []
    for record in records:
        key = (record["subscription_id"], record["slot"], record["iccid"],
               record["imsi"], record["carrier"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def parse_parcel_identifier(raw: str, field: str = "imsi") -> str:
    """Pull an identifier out of an ``iphonesubinfo`` parcel, or return "".

    Android answers calls it will not serve with filler such as
    ``Result: Parcel( 00000000 ffffffff   '........')``. Only digit-bearing
    payloads long enough to be a real identifier are accepted.
    """
    if not raw:
        return ""
    payload = "".join(re.findall(r"'([^']*)'", raw))
    payload = payload.replace("\x00", "").strip()
    # The parcel view interleaves UTF-16 high bytes, so an identifier reads as
    # "........6.2.0.0.3.1.0.7.7". Keep the characters that can be part of one.
    value = "".join(char for char in payload if char.isdigit() or char == "+")
    digits = re.sub(r"\D", "", value)
    if len(digits) < _MIN_IDENTIFIER_DIGITS.get(field, 5):
        return ""
    if len(set(digits)) == 1:  # 00000000, 11111111
        return ""
    return value


def parse_adn(raw: str) -> List[Dict]:
    """Parse ``content://icc/adn`` rows into contacts.

    Splitting on every comma would truncate ``name=Doe, John``, so rows are
    split only where a new ``key=`` begins.
    """
    contacts = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line.startswith("Row:"):
            continue
        body = re.sub(r"^Row:\s*\d+\s*", "", line)
        fields = {}
        for part in re.split(r",\s*(?=\w+=)", body):
            if "=" not in part:
                continue
            key, _, value = part.partition("=")
            fields[key.strip()] = value.strip()
        if not fields:
            continue
        name = fields.get("name", "")
        number = fields.get("number", "")
        email = fields.get("emails", "")
        if name in ("", "NULL") and number in ("", "NULL"):
            continue
        contacts.append({
            "name": "" if name == "NULL" else name,
            "number": "" if number == "NULL" else number,
            "email": "" if email == "NULL" else email,
            "adn_index": fields.get("index", ""),
            "slot": fields.get("slot", ""),
        })
    return contacts


def parse_sim_sms(raw: str) -> List[Dict]:
    """Parse ``content://sms/sim`` rows. ``No result found.`` means zero."""
    messages = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line.startswith("Row:"):
            continue
        body = re.sub(r"^Row:\s*\d+\s*", "", line)
        fields = {}
        for part in re.split(r",\s*(?=\w+=)", body):
            if "=" not in part:
                continue
            key, _, value = part.partition("=")
            fields[key.strip()] = value.strip()
        if not fields:
            continue
        messages.append({
            "sender": fields.get("address", ""),
            "body": fields.get("body", ""),
            "timestamp": fields.get("date", ""),
            "status": fields.get("type", ""),
        })
    return messages


def _merge(slots: List[Dict], subscriptions: List[Dict]) -> List[Dict]:
    """Merge prop-slot records with isub subscription records by slot."""
    by_slot = {sim["slot"]: sim for sim in slots}
    for sub in subscriptions:
        if sub["slot"] < 0:
            continue  # remembered subscription with no card present
        target = by_slot.get(sub["slot"])
        if target is None:
            target = {
                "slot": sub["slot"],
                "status": "present",
                "raw_state": "",
                "carrier": "",
                "country": "",
                "mcc": "",
                "mnc": "",
                "numeric": "",
                "iccid": "",
                "imsi": "",
                "msisdn": "",
                "display_name": "",
                "subscription_id": None,
                "redacted": [],
                "source": "dumpsys isub",
            }
            by_slot[sub["slot"]] = target
        for key in ("iccid", "imsi", "msisdn", "carrier", "country", "mcc", "mnc",
                    "display_name"):
            if sub.get(key):
                target[key] = sub[key]
        if sub.get("redacted"):
            target["redacted"] = sorted(set(target["redacted"]) | set(sub["redacted"]))
        target["subscription_id"] = sub["subscription_id"]
    return [by_slot[slot] for slot in sorted(by_slot)]


def read_sim_cards(serial: str) -> Dict:
    """Read every SIM the handset will expose, with provenance."""
    sources: Dict[str, str] = {}
    errors: List[str] = []

    def shell(command: str) -> str:
        ok, output = adb.adb_shell(serial, command)
        if not ok:
            errors.append(command)
            return ""
        return output or ""

    raw_state = shell("getprop gsm.sim.state")
    raw_alpha = shell("getprop gsm.sim.operator.alpha")
    raw_country = shell("getprop gsm.sim.operator.iso-country")
    raw_numeric = shell("getprop gsm.sim.operator.numeric")
    sources["getprop"] = "ok" if raw_state else "unavailable"

    slots = parse_sim_states(raw_state, raw_alpha, raw_country, raw_numeric)

    raw_isub = shell("dumpsys isub")
    subscriptions = parse_isub(raw_isub)
    sources["dumpsys isub"] = "ok" if subscriptions else "unavailable"
    sims = _merge(slots, subscriptions)

    # A parcel read is only consulted when isub did not supply the identifier.
    if any(not sim["imsi"] for sim in sims if sim["status"] == "present"):
        for command, field in (("service call iphonesubinfo 7", "imsi"),
                               ("service call iphonesubinfo 11", "iccid"),
                               ("service call iphonesubinfo 15", "msisdn")):
            value = parse_parcel_identifier(shell(command), field)
            if not value:
                continue
            for sim in sims:
                if sim["status"] == "present" and not sim[field]:
                    sim[field] = value
                    sim["source"] = f"{sim['source']} + {command}"
                    break

    # Contacts: per-subscription ADN so they land on the right card.
    present = [sim for sim in sims if sim["status"] == "present"]
    adn_status = "not_queried"
    for sim in present:
        sim["contacts"] = []
        sim["messages"] = []
        uri = None
        if sim.get("subscription_id") is not None:
            uri = f"content://icc/adn/subId/{sim['subscription_id']}"
        elif sim["slot"] == 0:
            uri = "content://icc/adn"
        if not uri:
            adn_status = "not_accessible"
            continue
        raw = adb.query_content(serial, uri)
        if raw and "SecurityException" in raw:
            adn_status = "not_accessible"
            continue
        contacts = parse_adn(raw)
        if contacts:
            adn_status = "ok"
        elif adn_status == "not_queried":
            adn_status = "empty"
        sim["contacts"] = contacts
    for sim in sims:
        sim.setdefault("contacts", [])
        sim.setdefault("messages", [])

    # SIM-stored SMS is device-wide, not per subscription.
    raw_sms = adb.query_content(serial, "content://sms/sim")
    messages = parse_sim_sms(raw_sms)
    if messages and present:
        present[0]["messages"] = messages
    sources["content://icc/adn"] = adn_status
    sources["content://sms/sim"] = "ok" if messages else "empty"

    remembered = [sub for sub in subscriptions if sub["slot"] < 0]
    return {
        "sims": sims,
        "remembered_subscriptions": remembered,
        "sources": sources,
        "errors": errors,
        "extracted_at": _now(),
        "serial": serial,
    }


def store_sim_cards(session_id: str, result: Dict) -> Dict:
    """Persist an extraction into ``sim_cards`` / ``sim_contacts`` / ``sim_messages``.

    Rows from an earlier extraction of the same session are replaced, so
    re-running the tool does not double-count contacts.
    """
    import uuid

    from .database import get_db

    counts = {"sims": 0, "contacts": 0, "messages": 0, "persisted": False}
    conn = get_db()
    try:
        # sim_cards.session_id is a foreign key, so an extraction taken before a
        # case session exists cannot be filed. Say so instead of failing.
        if not session_id:
            counts["reason"] = "no active session"
            return counts
        known = conn.execute(
            "SELECT 1 FROM forensic_sessions WHERE id=?", (session_id,)).fetchone()
        if not known:
            counts["reason"] = "no matching session"
            return counts

        existing = [row[0] for row in conn.execute(
            "SELECT id FROM sim_cards WHERE session_id=?", (session_id,))]
        for sim_id in existing:
            conn.execute("DELETE FROM sim_contacts WHERE sim_id=?", (sim_id,))
            conn.execute("DELETE FROM sim_messages WHERE sim_id=?", (sim_id,))
        conn.execute("DELETE FROM sim_cards WHERE session_id=?", (session_id,))

        for sim in result.get("sims", []):
            sim_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO sim_cards (id, session_id, slot, status, imsi, iccid,"
                " msisdn, carrier, country, extracted_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sim_id, session_id, sim.get("slot", 0), sim.get("status", "unknown"),
                 sim.get("imsi", ""), sim.get("iccid", ""), sim.get("msisdn", ""),
                 sim.get("carrier", ""), sim.get("country", ""),
                 result.get("extracted_at") or _now()))
            counts["sims"] += 1

            for contact in sim.get("contacts", []):
                conn.execute(
                    "INSERT INTO sim_contacts (id, sim_id, name, phone_number, email,"
                    " adn_index) VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), sim_id, contact.get("name", ""),
                     contact.get("number", ""), contact.get("email", ""),
                     contact.get("adn_index", "")))
                counts["contacts"] += 1

            for message in sim.get("messages", []):
                conn.execute(
                    "INSERT INTO sim_messages (id, sim_id, sender, body, timestamp,"
                    " status) VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), sim_id, message.get("sender", ""),
                     message.get("body", ""), message.get("timestamp", ""),
                     message.get("status", "")))
                counts["messages"] += 1

        conn.commit()
        counts["persisted"] = True
    finally:
        conn.close()
    return counts


# --------------------------------------------------------------------------
# Compatibility wrappers: earlier callers expect a single flat SIM record.
# --------------------------------------------------------------------------

def extract_sim_info(serial: str) -> Dict:
    """First physically present SIM as a flat record (legacy callers)."""
    result = read_sim_cards(serial)
    present = [sim for sim in result["sims"] if sim["status"] == "present"]
    sim = (present or result["sims"] or [{}])[0]
    return {
        "slot": sim.get("slot", 1),
        "status": sim.get("status", "unknown"),
        "imsi": sim.get("imsi", ""),
        "iccid": sim.get("iccid", ""),
        "msisdn": sim.get("msisdn", ""),
        "carrier": sim.get("carrier", ""),
        "country": sim.get("country", ""),
    }


def extract_sim_contacts(serial: str) -> List[Dict]:
    result = read_sim_cards(serial)
    contacts: List[Dict] = []
    for sim in result["sims"]:
        contacts.extend(sim.get("contacts", []))
    return contacts


def extract_sim_messages(serial: str) -> List[Dict]:
    result = read_sim_cards(serial)
    for sim in result["sims"]:
        if sim.get("messages"):
            return sim["messages"]
    return []
