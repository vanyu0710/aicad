from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path


FEEDBACK_PATH = Path("outputs") / "feedback.csv"


def save_feedback(run_id: str, rating: int, issue: str, would_continue: bool, contact: str) -> str:
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    exists = FEEDBACK_PATH.exists()
    with FEEDBACK_PATH.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["created_at", "run_id", "rating", "issue", "would_continue", "contact"],
        )
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id,
                "rating": rating,
                "issue": issue,
                "would_continue": would_continue,
                "contact": contact,
            }
        )
    return str(FEEDBACK_PATH)

