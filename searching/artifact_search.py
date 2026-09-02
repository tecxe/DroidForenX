"""Artifact Search — keyword search, regex matching, cross-reference queries."""

import re
import sqlite3
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ArtifactSearcher:
    """Searches across forensic artifacts for keywords, patterns, and correlations."""

    def __init__(self, db_path: str = "droidforenx.db"):
        self.db_path = db_path

    def search_sms(self, keyword: str, case_id: str = "") -> list:
        results = []
        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                rows = conn.execute(
                    "SELECT * FROM examination_results WHERE result_data LIKE ?",
                    (f"%{keyword}%",)
                ).fetchall()
                for row in rows:
                    results.append({"id": row[0], "module": row[3], "data": row[4]})
        except Exception as e:
            logger.warning(f"Search error: {e}")
        return results

    def search_regex(self, pattern: str, text_data: str) -> list:
        try:
            matches = re.finditer(pattern, text_data, re.IGNORECASE)
            return [{"match": m.group(), "start": m.start(), "end": m.end()} for m in matches]
        except re.error:
            return []

    def search_by_date_range(self, start_date: str, end_date: str, data: list) -> list:
        return [d for d in data if start_date <= d.get("date", "") <= end_date]

    def cross_reference(self, data_sets: list, key: str) -> dict:
        from collections import Counter
        all_keys = []
        for ds in data_sets:
            for item in ds:
                val = item.get(key, "")
                if val:
                    all_keys.append(val)
        return dict(Counter(all_keys).most_common(100))
