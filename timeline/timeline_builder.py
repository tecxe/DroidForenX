"""Timeline Reconstruction — chronological event ordering and correlation."""

from datetime import datetime, timezone
from typing import Optional
import re


class TimelineBuilder:
    """Builds chronological timelines from multiple artifact sources."""

    def __init__(self):
        self.events: list[dict] = []

    def add_event(self, timestamp: str, event_type: str, source: str,
                  details: str = "", significance: str = "normal"):
        self.events.append({
            "timestamp": timestamp,
            "type": event_type,
            "source": source,
            "details": details,
            "significance": significance,
        })

    def add_sms_events(self, sms_data: list):
        for msg in sms_data:
            ts = msg.get("date", "")
            direction = msg.get("type", "")
            address = msg.get("address", "")
            self.add_event(ts, f"SMS {direction}", "SMS",
                          f"Message {direction.lower()} to/from {address}")

    def add_call_events(self, call_data: list):
        for call in call_data:
            ts = call.get("date", "")
            call_type = call.get("type", "")
            number = call.get("number", "")
            self.add_event(ts, f"Call {call_type}", "Call Log",
                          f"{call_type} call with {number}")

    def add_transaction_events(self, txn_data: list):
        for txn in txn_data:
            ts = txn.get("date", "")
            txn_type = txn.get("type", "")
            amount = txn.get("amount", "")
            self.add_event(ts, f"Transaction: {txn_type}", "MoMo",
                          f"{txn_type} {amount}", significance="financial")

    def build_timeline(self) -> list:
        def parse_ts(ts_str):
            try:
                clean = str(ts_str).replace("T", " ").split(".")[0].split("+")[0].strip()
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(clean, fmt)
                    except ValueError:
                        continue
            except Exception:
                pass
            return datetime.min
        self.events.sort(key=lambda e: parse_ts(e["timestamp"]))
        return self.events

    def get_gaps(self, min_gap_hours: int = 24) -> list:
        timeline = self.build_timeline()
        gaps = []
        for i in range(1, len(timeline)):
            try:
                t1 = parse_ts(timeline[i-1]["timestamp"])
                t2 = parse_ts(timeline[i]["timestamp"])
                diff = (t2 - t1).total_seconds() / 3600
                if diff >= min_gap_hours:
                    gaps.append({
                        "from": timeline[i-1]["timestamp"],
                        "to": timeline[i]["timestamp"],
                        "hours": round(diff, 1),
                    })
            except Exception:
                pass
        return gaps
