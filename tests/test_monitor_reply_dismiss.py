import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bosshunter.db import add_history, get_db, get_jobs_needing_resume, insert_job, update_job_status


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "AI运营",
        "company": "Example",
        "salary": "20-30K",
        "city": "北京",
        "experience": "1-3年",
        "jd": "负责AI内容运营",
        "hr_name": "HR",
        "hr_title": "招聘者",
        "hr_active": "",
        "company_size": "",
        "company_industry": "",
        "url": "https://example.com/job",
    }


class MonitorReplyDismissTests(unittest.TestCase):
    def test_dismissed_pending_reply_is_not_recreated_by_monitor(self):
        from bosshunter.executor import monitor

        messages = [
            {"sender": "me", "text": "您好，我对岗位很感兴趣。"},
            {"sender": "hr", "text": "方便介绍一下你的AI内容运营经验吗？"},
        ]
        pending_detail = monitor._build_reply_detail(messages, "可以，我有AI内容运营经验。")

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("job-1"))
                update_job_status(db, "job-1", "replied")
                add_history(db, "job-1", "reply_pending", pending_detail)
                add_history(
                    db,
                    "job-1",
                    "reply_dismissed",
                    monitor._build_reply_resolution_detail(
                        "reply_dismissed.v1",
                        "Web Dashboard 放弃回复建议",
                        pending_detail,
                    ),
                )
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="target-1"), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(messages, ensure_ascii=False)), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor, "_generate_auto_reply", return_value="可以，我有AI内容运营经验。") as generate_reply:
                action = monitor._handle_conversation(_job("job-1") | {"status": "replied"}, {"monitor": {}})

            verify_db = get_db(db_path)
            try:
                actions = [
                    row["action"]
                    for row in verify_db.execute(
                        "SELECT action FROM history WHERE job_id = ? ORDER BY id",
                        ("job-1",),
                    ).fetchall()
                ]
            finally:
                verify_db.close()

        self.assertEqual(action, "skipped_dismissed_reply")
        self.assertEqual(actions, ["reply_pending", "reply_dismissed"])
        generate_reply.assert_not_called()

    def test_new_hr_message_after_dismissed_reply_creates_new_pending_reply(self):
        from bosshunter.executor import monitor

        dismissed_messages = [
            {"sender": "me", "text": "您好，我对岗位很感兴趣。"},
            {"sender": "hr", "text": "方便介绍一下你的AI内容运营经验吗？"},
        ]
        new_messages = [
            *dismissed_messages,
            {"sender": "hr", "text": "那你能说说最近做过的增长项目吗？"},
        ]
        pending_detail = monitor._build_reply_detail(dismissed_messages, "可以，我有AI内容运营经验。")

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("job-2"))
                update_job_status(db, "job-2", "replied")
                add_history(db, "job-2", "reply_pending", pending_detail)
                add_history(
                    db,
                    "job-2",
                    "reply_dismissed",
                    monitor._build_reply_resolution_detail(
                        "reply_dismissed.v1",
                        "Web Dashboard 放弃回复建议",
                        pending_detail,
                    ),
                )
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="target-1"), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(new_messages, ensure_ascii=False)), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor, "_generate_auto_reply", return_value="我最近做过一个增长项目。") as generate_reply:
                action = monitor._handle_conversation(_job("job-2") | {"status": "replied"}, {"monitor": {}})

            verify_db = get_db(db_path)
            try:
                actions = [
                    row["action"]
                    for row in verify_db.execute(
                        "SELECT action FROM history WHERE job_id = ? ORDER BY id",
                        ("job-2",),
                    ).fetchall()
                ]
            finally:
                verify_db.close()

        self.assertEqual(action, "reply_pending")
        self.assertEqual(actions, ["reply_pending", "reply_dismissed", "reply_pending"])
        generate_reply.assert_called_once()

    def test_resume_request_card_with_reject_button_is_not_treated_as_rejection(self):
        from bosshunter.executor import monitor

        messages = [
            {"sender": "hr", "text": "您是否接受此工作地点? 理想国际大厦 暂不考虑可以接受"},
            {"sender": "hr", "text": "已读 2周"},
            {
                "sender": "hr",
                "text": "我想要一份您的附件简历，您是否同意 拒绝 同意 拒绝 同意",
            },
        ]

        self.assertTrue(monitor._detect_resume_request(messages))
        self.assertTrue(monitor._has_resume_request_card(messages))
        self.assertFalse(monitor._detect_rejection(messages))

    def test_failed_tailored_resume_generation_does_not_mark_job_needs_resume(self):
        from bosshunter.executor import monitor

        messages = [
            {"sender": "me", "text": "您好，我对岗位很感兴趣。"},
            {"sender": "hr", "text": "方便发一份你的简历过来吗？"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("job-resume-failed"))
                update_job_status(db, "job-resume-failed", "sent")
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="target-1"), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(messages, ensure_ascii=False)), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor, "_send_message_in_chat", return_value=True), \
                 patch("bosshunter.ai.resume.generate_tailored_resume", return_value=None):
                action = monitor._handle_conversation(
                    _job("job-resume-failed") | {"status": "sent"},
                    {"profile": {"portfolio_url": "https://example.com/resume"}},
                )

            verify_db = get_db(db_path)
            try:
                row = verify_db.execute(
                    "SELECT status, resume_path FROM jobs WHERE id = ?",
                    ("job-resume-failed",),
                ).fetchone()
                actions = [
                    item["action"]
                    for item in verify_db.execute(
                        "SELECT action FROM history WHERE job_id = ? ORDER BY id",
                        ("job-resume-failed",),
                    ).fetchall()
                ]
                needing_resume = get_jobs_needing_resume(verify_db)
            finally:
                verify_db.close()

        self.assertEqual(action, "failed")
        self.assertEqual(row["status"], "sent")
        self.assertIsNone(row["resume_path"])
        self.assertEqual(actions, ["resume_failed"])
        self.assertEqual(needing_resume, [])


if __name__ == "__main__":
    unittest.main()
