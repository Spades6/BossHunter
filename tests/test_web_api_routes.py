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

    def test_web_api_deliver_releases_full_task_waiting_for_confirmation(self):
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


if __name__ == "__main__":
    unittest.main()
