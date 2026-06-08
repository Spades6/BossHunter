import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ConfigExampleTests(unittest.TestCase):
    def test_example_uses_search_cities_list(self):
        config = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))

        self.assertIn("cities", config["search"])
        self.assertIsInstance(config["search"]["cities"], list)
        self.assertNotIn("city", config["search"])


class ConfigValidationTests(unittest.TestCase):
    def test_load_config_rejects_unsupported_ai_provider(self):
        from bosshunter.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("ai:\n  provider: openai\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "仅支持 Anthropic|ai.provider: anthropic"):
                load_config(config_path)


class ConfirmationUiTests(unittest.TestCase):
    @patch("bosshunter.ui.confirm.Prompt.ask")
    @patch("bosshunter.ui.confirm.get_jobs_pending_confirmation")
    @patch("bosshunter.ui.confirm.get_db")
    def test_confirmation_defaults_to_individual_selection(self, get_db, get_jobs_pending_confirmation, prompt_ask):
        from bosshunter.ui.confirm import show_confirmation

        db = Mock()
        get_db.return_value = db
        get_jobs_pending_confirmation.return_value = [
            {
                "id": "job-1",
                "company": "Example",
                "title": "Engineer",
                "salary": "10-20K",
                "score": 88,
                "score_reason": "good match",
                "greeting": "",
            }
        ]
        prompt_ask.return_value = "q"

        result = show_confirmation({})

        self.assertFalse(result)
        self.assertEqual(prompt_ask.call_args_list[0].kwargs["default"], "s")


class DashboardPageTests(unittest.TestCase):
    def setUp(self):
        self.source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "pages"
            / "DashboardPage.tsx"
        ).read_text(encoding="utf-8")

    def test_dashboard_renders_recent_activity_history(self):
        self.assertIn("RecentActivity", self.source)
        self.assertIn("history", self.source)
        self.assertIn("<RecentActivity data={history}", self.source)

    def test_dashboard_exposes_manual_refresh_button(self):
        self.assertIn("RefreshCw", self.source)
        self.assertIn("onClick={refresh}", self.source)


if __name__ == "__main__":
    unittest.main()
