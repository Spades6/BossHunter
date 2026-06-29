import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bosshunter.db import (
    get_db,
    get_jobs_pending_confirmation,
    get_jobs_ready_to_send,
    get_jobs_with_send_errors,
    insert_job,
    update_job_greeting,
    update_job_score,
    update_job_status,
)
from bosshunter.executor.sender import send_greetings
from bosshunter.executor.sender import _send_greeting_once


def _job(job_id: str, title: str = "Engineer") -> dict:
    return {
        "id": job_id,
        "title": title,
        "company": "Example",
        "salary": "10-20K",
        "city": "Beijing",
        "experience": "1-3 years",
        "jd": "Build product features",
        "hr_name": "HR",
        "hr_title": "Recruiter",
        "hr_active": "",
        "company_size": "",
        "company_industry": "",
        "url": "https://example.com/job",
    }


class JobSelectionTests(unittest.TestCase):
    def test_send_greeting_reports_unavailable_job_page_before_clicking_chat(self):
        job = {
            "id": "gone",
            "url": "https://www.zhipin.com/job_detail/gone.html",
        }

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", return_value='{"success": false, "error": "job_page_unavailable", "history_detail": "岗位页面不存在或已下架", "skip_backoff": true}'), \
             patch("bosshunter.executor.sender.close_tab") as close_tab, \
             patch("bosshunter.executor.sender.time.sleep"):
            result, target_id = _send_greeting_once(
                job,
                "您好，我对这个岗位很感兴趣。",
                {"browse_before_greet": False},
            )

        self.assertIsNone(target_id)
        self.assertEqual(result["error"], "job_page_unavailable")
        self.assertEqual(result["history_detail"], "岗位页面不存在或已下架")
        close_tab.assert_called_once_with("target-1")

    def test_pending_confirmation_excludes_jobs_with_greetings(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                insert_job(db, _job("scored"))
                update_job_score(db, "scored", 88, "good match")
                update_job_status(db, "scored", "ready")

                insert_job(db, _job("sendable"))
                update_job_score(db, "sendable", 92, "great match")
                update_job_status(db, "sendable", "ready")
                update_job_greeting(db, "sendable", "Hi, this role looks like a strong fit.")

                jobs = get_jobs_pending_confirmation(db)
            finally:
                db.close()

        self.assertEqual([job["id"] for job in jobs], ["scored"])

    def test_ready_to_send_requires_a_non_empty_greeting(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                insert_job(db, _job("no-greeting"))
                update_job_status(db, "no-greeting", "ready")

                insert_job(db, _job("blank-greeting"))
                update_job_status(db, "blank-greeting", "ready")
                update_job_greeting(db, "blank-greeting", "   ")

                insert_job(db, _job("sendable"))
                update_job_status(db, "sendable", "ready")
                update_job_greeting(db, "sendable", "Hi, this role looks like a strong fit.")

                insert_job(db, _job("approved"))
                update_job_status(db, "approved", "approved")
                update_job_greeting(db, "approved", "Not ready for send status yet.")

                jobs = get_jobs_ready_to_send(db)
            finally:
                db.close()

        self.assertCountEqual([job["id"] for job in jobs], ["approved", "sendable"])

    def test_send_errors_return_only_jobs_with_generated_greetings(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                insert_job(db, _job("send-failed"))
                update_job_status(db, "send-failed", "error")
                update_job_greeting(db, "send-failed", "Hi, this role looks like a strong fit.")

                insert_job(db, _job("generation-failed"))
                update_job_status(db, "generation-failed", "error")

                insert_job(db, _job("sendable"))
                update_job_status(db, "sendable", "ready")
                update_job_greeting(db, "sendable", "Ready to send.")

                jobs = get_jobs_with_send_errors(db)
            finally:
                db.close()

        self.assertEqual([job["id"] for job in jobs], ["send-failed"])

    def test_send_greetings_force_bypasses_send_window_restriction(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("sendable"))
                update_job_status(db, "sendable", "ready")
                update_job_greeting(db, "sendable", "Ready to send.")
            finally:
                db.close()

            config = {
                "throttle": {
                    "send_windows": ["09:00-16:00"],
                    "daily_limit": 30,
                    "interval_min": 0,
                    "interval_max": 0,
                    "browse_before_greet": False,
                }
            }

            # Act
            with patch("bosshunter.db.DB_PATH", db_path), \
                 patch("bosshunter.throttle.datetime") as mock_datetime, \
                 patch("bosshunter.executor.sender._send_greeting_once", return_value=({"success": True}, None)):
                mock_datetime.now.return_value = datetime(2026, 6, 19, 20, 0)
                sent = send_greetings(config, force=True)

            verify_db = get_db(db_path)
            try:
                status = verify_db.execute("SELECT status FROM jobs WHERE id = 'sendable'").fetchone()["status"]
                outside_window_events = verify_db.execute(
                    "SELECT COUNT(*) AS c FROM risk_events WHERE event_type = 'outside_window'"
                ).fetchone()["c"]
            finally:
                verify_db.close()

        # Assert
        self.assertEqual(sent, 1)
        self.assertEqual(status, "sent")
        self.assertEqual(outside_window_events, 0)


if __name__ == "__main__":
    unittest.main()
