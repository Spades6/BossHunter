import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from bosshunter.db import get_db, insert_job, update_job_score, update_job_status
from bosshunter.web import server
from threading import Event

from bosshunter.web.tasks import WorkbenchTask, WorkbenchTaskRunner


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "Product Manager",
        "company": "Example",
        "salary": "20-30K",
        "city": "Shanghai",
        "experience": "1-3 years",
        "jd": "Build AI product features",
        "hr_name": "HR",
        "hr_title": "Recruiter",
        "hr_active": "",
        "company_size": "",
        "company_industry": "",
        "url": "https://example.com/job",
    }


class WebApiRouteTests(unittest.TestCase):
    def setUp(self):
        # Arrange
        self.original_base_dir = server.BASE_DIR

    def tearDown(self):
        # Cleanup
        server.set_base_dir(self.original_base_dir)

    def _request(self, path: str, method: str = "GET"):
        if "?" in path:
            path_info, query_string = path.split("?", 1)
        else:
            path_info, query_string = path, ""

        status_headers = {}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status
            status_headers["headers"] = dict(headers)

        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path_info,
            "QUERY_STRING": query_string,
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(b""),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }

        body = b"".join(
            chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
            for chunk in server.app(environ, start_response)
        ).decode("utf-8")
        return status_headers["status"], status_headers["headers"], body

    def test_web_api_missing_api_route_returns_json_404_not_spa_html(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            server.set_base_dir(Path(tmp))

            # Act
            status, headers, body = self._request("/api/does-not-exist")

        # Assert
        self.assertTrue(status.startswith("404"))
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(body), {"error": "Not found"})
        self.assertNotIn("<!doctype html", body.lower())

    def test_web_api_workbench_preflight_full_returns_json_payload(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            resume_path = base_dir / "resume.md"
            resume_path.write_text("# Resume", encoding="utf-8")
            (base_dir / "config.yaml").write_text(
                yaml.dump(
                    {
                        "profile": {"resume_path": str(resume_path)},
                        "search": {"keywords": ["AI产品经理"]},
                        "ai": {"api_key": "test-api-key"},
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            server.set_base_dir(base_dir)

            # Act
            status, headers, body = self._request("/api/workbench/preflight?mode=full")

        # Assert
        self.assertTrue(status.startswith("200"))
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(body), {"ok": True, "messages": []})

    def test_web_api_workbench_preflight_full_requires_ai_key(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            resume_path = base_dir / "resume.md"
            resume_path.write_text("# Resume", encoding="utf-8")
            (base_dir / "config.yaml").write_text(
                yaml.dump(
                    {
                        "profile": {"resume_path": str(resume_path)},
                        "search": {"keywords": ["AI产品经理"]},
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            server.set_base_dir(base_dir)

            # Act
            with patch.dict("os.environ", {}, clear=True):
                status, headers, body = self._request("/api/workbench/preflight?mode=full")

        # Assert
        self.assertTrue(status.startswith("200"))
        self.assertIn("application/json", headers["Content-Type"])
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertIn("请先在配置页填写 AI API Key，或设置 ANTHROPIC_API_KEY 环境变量。", payload["messages"])

    def test_web_api_activity_returns_json_without_runtime_name_error(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            server.set_base_dir(Path(tmp))

            # Act
            status, headers, body = self._request("/api/activity?days=7")

        # Assert
        self.assertTrue(status.startswith("200"))
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(body), [])

    def test_web_api_workbench_pending_confirmation_returns_ready_jobs(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-job"))
                update_job_score(db, "ready-job", 82, "good match")
                update_job_status(db, "ready-job", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            # Act
            status, headers, body = self._request("/api/workbench")

        # Assert
        payload = json.loads(body)
        self.assertTrue(status.startswith("200"))
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual([job["id"] for job in payload["pending_confirmation"]], ["ready-job"])

    def test_web_api_full_task_stays_running_while_waiting_for_frontend_confirmation(self):
        # Arrange
        confirmation_reached = False

        def fake_collect(task, config):
            nonlocal confirmation_reached
            confirmation_reached = True

        runner = WorkbenchTaskRunner()
        runner._executors["full"] = lambda task, config: server._execute_full(task, config)

        # Act
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-job"))
                update_job_score(db, "ready-job", 82, "good match")
                update_job_status(db, "ready-job", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            with patch.object(server, "_execute_collect", side_effect=fake_collect):
                task = runner.start("full", {})
                for _ in range(20):
                    status = runner.status()
                    active = status["active"]
                    if active and "等待前端确认投递" in active["logs"]:
                        break
                    time.sleep(0.01)
                time.sleep(0.05)
                status = runner.status()
                active = status["active"]
                if active:
                    runner._tasks[task["id"]].stop_requested.set()
                    runner.wait(timeout=1)

        # Assert
        self.assertTrue(confirmation_reached)
        self.assertIsNotNone(active)
        self.assertEqual(active["id"], task["id"])
        self.assertEqual(active["status"], "running")
        self.assertIn("等待前端确认投递", active["logs"])

    def test_task_stop_releases_active_slot_before_executor_returns(self):
        # Arrange
        started = Event()
        release = Event()

        def blocking_executor(task, config):
            started.set()
            release.wait(timeout=1)

        runner = WorkbenchTaskRunner({
            "collect": blocking_executor,
            "monitor": lambda task, config: None,
        })
        task = runner.start("collect", {})
        self.assertTrue(started.wait(timeout=1))

        try:
            # Act
            stopped = runner.stop(task["id"])
            status_after_stop = runner.status()
            second_task = runner.start("monitor", {})
            runner.wait(timeout=1)
        finally:
            release.set()
            runner.wait(timeout=1)

        # Assert
        self.assertEqual(stopped["status"], "stopping")
        self.assertIsNone(status_after_stop["active"])
        self.assertEqual(second_task["mode"], "monitor")

    def test_web_api_full_task_completes_when_no_jobs_need_confirmation(self):
        # Arrange
        calls = []

        def fake_collect(task, config):
            calls.append("collect")

        runner = WorkbenchTaskRunner()
        runner._executors["full"] = lambda task, config: server._execute_full(task, config)

        # Act
        with tempfile.TemporaryDirectory() as tmp:
            server.set_base_dir(Path(tmp))
            with patch.object(server, "_execute_collect", side_effect=fake_collect):
                task = runner.start("full", {})
                runner.wait(timeout=1)
                status = runner.status()
                last_task = status["last_task"]

        # Assert
        self.assertEqual(calls, ["collect"])
        self.assertIsNone(status["active"])
        self.assertEqual(last_task["id"], task["id"])
        self.assertEqual(last_task["status"], "completed")
        self.assertIn("没有待确认岗位，流程结束", last_task["logs"])

    def test_web_api_deliver_hands_selected_jobs_to_waiting_full_task(self):
        # Arrange
        confirmation_event = Event()
        full_task = WorkbenchTask(id="full-task", mode="full", label="运行全流程")
        full_task.context["waiting_confirmation"] = True
        full_task.context["confirmation_event"] = confirmation_event
        runner = WorkbenchTaskRunner()
        runner._tasks[full_task.id] = full_task

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-job"))
                update_job_status(db, "ready-job", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            body = json.dumps({"job_ids": ["ready-job"]}).encode("utf-8")
            status_headers = {}

            def start_response(status, headers, exc_info=None):
                status_headers["status"] = status
                status_headers["headers"] = dict(headers)

            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/api/workbench/deliver",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/json",
                "SERVER_NAME": "127.0.0.1",
                "SERVER_PORT": "8686",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": io.BytesIO(body),
                "wsgi.errors": io.StringIO(),
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
            }

            # Act
            with patch.object(server, "task_runner", runner):
                response_body = b"".join(
                    chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                    for chunk in server.app(environ, start_response)
                ).decode("utf-8")

        # Assert
        self.assertTrue(status_headers["status"].startswith("200"), response_body)
        self.assertTrue(confirmation_event.is_set())
        self.assertEqual(full_task.context["confirmed_job_ids"], ["ready-job"])
        self.assertEqual(json.loads(response_body)["id"], "full-task")

    def test_web_api_deliver_ignores_stale_stopped_full_task_waiting_context(self):
        # Arrange
        stale_event = Event()
        stale_task = WorkbenchTask(id="stale-full-task", mode="full", label="运行全流程", status="stopped")
        stale_task.context["waiting_confirmation"] = True
        stale_task.context["confirmation_event"] = stale_event

        active_event = Event()
        active_task = WorkbenchTask(id="active-full-task", mode="full", label="运行全流程")
        active_task.context["waiting_confirmation"] = True
        active_task.context["confirmation_event"] = active_event

        runner = WorkbenchTaskRunner()
        runner._tasks[stale_task.id] = stale_task
        runner._tasks[active_task.id] = active_task

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-job"))
                update_job_status(db, "ready-job", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            body = json.dumps({"job_ids": ["ready-job"]}).encode("utf-8")
            status_headers = {}

            def start_response(status, headers, exc_info=None):
                status_headers["status"] = status
                status_headers["headers"] = dict(headers)

            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/api/workbench/deliver",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/json",
                "SERVER_NAME": "127.0.0.1",
                "SERVER_PORT": "8686",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": io.BytesIO(body),
                "wsgi.errors": io.StringIO(),
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
            }

            # Act
            with patch.object(server, "task_runner", runner):
                response_body = b"".join(
                    chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                    for chunk in server.app(environ, start_response)
                ).decode("utf-8")

        # Assert
        self.assertTrue(status_headers["status"].startswith("200"), response_body)
        self.assertFalse(stale_event.is_set())
        self.assertTrue(active_event.is_set())
        self.assertNotIn("confirmed_job_ids", stale_task.context)
        self.assertEqual(active_task.context["confirmed_job_ids"], ["ready-job"])
        self.assertEqual(json.loads(response_body)["id"], "active-full-task")

    def test_web_api_full_task_continues_delivery_and_monitoring_after_confirmation(self):
        # Arrange
        calls = []

        def fake_collect(task, config):
            calls.append("collect")

        def fake_deliver(task, config):
            calls.append(("deliver", config.get("_workbench_job_ids")))

        def fake_monitor(task, config):
            calls.append("monitor")

        runner = WorkbenchTaskRunner()
        runner._executors["full"] = lambda task, config: server._execute_full(task, config)

        # Act
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-a"))
                update_job_score(db, "ready-a", 88, "good match")
                update_job_status(db, "ready-a", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            with patch.object(server, "_execute_collect", side_effect=fake_collect), \
                 patch.object(server, "_execute_deliver", side_effect=fake_deliver), \
                 patch.object(server, "_execute_monitor", side_effect=fake_monitor):
                task = runner.start("full", {})
                for _ in range(50):
                    running_task = runner._tasks[task["id"]]
                    confirmation_event = running_task.context.get("confirmation_event")
                    if isinstance(confirmation_event, Event):
                        running_task.context["confirmed_job_ids"] = ["ready-a", "ready-b"]
                        confirmation_event.set()
                        break
                    time.sleep(0.01)
                runner.wait(timeout=1)

        # Assert
        self.assertEqual(calls, ["collect", ("deliver", ["ready-a", "ready-b"]), "monitor"])

    def test_web_api_workbench_reject_marks_selected_ready_jobs_rejected(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("reject-a"))
                update_job_score(db, "reject-a", 82, "good match")
                update_job_status(db, "reject-a", "ready")

                insert_job(db, _job("reject-b"))
                update_job_score(db, "reject-b", 72, "ok match")
                update_job_status(db, "reject-b", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            body = json.dumps({"job_ids": ["reject-a", "reject-b"]}).encode("utf-8")
            status_headers = {}

            def start_response(status, headers, exc_info=None):
                status_headers["status"] = status
                status_headers["headers"] = dict(headers)

            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/api/workbench/reject",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/json",
                "SERVER_NAME": "127.0.0.1",
                "SERVER_PORT": "8686",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": io.BytesIO(body),
                "wsgi.errors": io.StringIO(),
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
            }

            # Act
            response_body = b"".join(
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                for chunk in server.app(environ, start_response)
            ).decode("utf-8")

            verify_db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                statuses = {
                    row["id"]: row["status"]
                    for row in verify_db.execute(
                        "SELECT id, status FROM jobs WHERE id IN ('reject-a', 'reject-b')"
                    ).fetchall()
                }
                history_actions = [
                    dict(row)
                    for row in verify_db.execute(
                        "SELECT job_id, action, detail FROM history ORDER BY id"
                    ).fetchall()
                ]
            finally:
                verify_db.close()

        # Assert
        self.assertTrue(status_headers["status"].startswith("200"), response_body)
        self.assertEqual(json.loads(response_body), {"success": True, "count": 2})
        self.assertEqual(statuses, {"reject-a": "rejected", "reject-b": "rejected"})
        self.assertEqual(
            history_actions,
            [
                {"job_id": "reject-a", "action": "rejected", "detail": "Web Dashboard 放弃投递"},
                {"job_id": "reject-b", "action": "rejected", "detail": "Web Dashboard 放弃投递"},
            ],
        )

    def test_web_api_workbench_reject_removes_jobs_from_pending_confirmation(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("reject-visible"))
                update_job_score(db, "reject-visible", 82, "good match")
                update_job_status(db, "reject-visible", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            body = json.dumps({"job_ids": ["reject-visible"]}).encode("utf-8")
            status_headers = {}

            def start_response(status, headers, exc_info=None):
                status_headers["status"] = status
                status_headers["headers"] = dict(headers)

            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/api/workbench/reject",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/json",
                "SERVER_NAME": "127.0.0.1",
                "SERVER_PORT": "8686",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": io.BytesIO(body),
                "wsgi.errors": io.StringIO(),
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
            }

            # Act
            response_body = b"".join(
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                for chunk in server.app(environ, start_response)
            ).decode("utf-8")
            workbench_status, _, workbench_body = self._request("/api/workbench")

        # Assert
        self.assertTrue(status_headers["status"].startswith("200"), response_body)
        self.assertTrue(workbench_status.startswith("200"), workbench_body)
        self.assertEqual(json.loads(workbench_body)["pending_confirmation"], [])


if __name__ == "__main__":
    unittest.main()
