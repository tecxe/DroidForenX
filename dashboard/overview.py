"""Dashboard Overview — case summary and investigation statistics."""

import sqlite3
from datetime import datetime, timezone


class DashboardOverview:
    """Provides high-level investigation statistics and summaries."""

    def __init__(self, db_path: str = "droidforenx.db"):
        self.db_path = db_path

    def get_case_stats(self, case_id: str = "") -> dict:
        stats = {
            "total_artifacts": 0,
            "sms_count": 0,
            "call_count": 0,
            "contact_count": 0,
            "media_count": 0,
            "document_count": 0,
            "transaction_count": 0,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        return stats

    def get_acquisition_progress(self) -> dict:
        return {
            "total_phases": 12,
            "completed_phases": 0,
            "current_phase": "idle",
            "percentage": 0,
        }
