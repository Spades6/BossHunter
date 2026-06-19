import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml


ROOT = Path(__file__).resolve().parents[1]


class PublicPrivacyTests(unittest.TestCase):
    def test_tracked_files_do_not_reference_company_api_brand(self):
        import subprocess

        blocked = "one" + "api"
        result = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        )

        offenders = []
        for rel_path in result.stdout.splitlines():
            path = ROOT / rel_path
            if not path.is_file():
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if blocked in source.lower():
                offenders.append(rel_path)

        self.assertEqual(offenders, [])


class VersionMetadataTests(unittest.TestCase):
    def test_release_version_is_consistent(self):
        import json

        import bosshunter
        from bosshunter.web.server import health

        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        sidebar_source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "components"
            / "layout"
            / "Sidebar.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn('version = "2.0.0"', pyproject)
        self.assertEqual(bosshunter.__version__, "2.0.0")
        self.assertEqual(json.loads(health())["version"], "2.0.0")
        self.assertIn("v2.0 · 本地控制台", sidebar_source)
        self.assertNotIn("v1.1.0", sidebar_source)


class ConfigExampleTests(unittest.TestCase):
    def test_example_uses_search_cities_list(self):
        config = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))

        self.assertIn("cities", config["search"])
        self.assertIsInstance(config["search"]["cities"], list)
        self.assertNotIn("city", config["search"])

    def test_example_defaults_to_not_allowing_internships(self):
        config = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))

        self.assertIs(config["profile"]["allow_internship"], False)

    def test_example_does_not_include_prefilter_threshold(self):
        config = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))

        self.assertNotIn("prefilter_threshold", config["scoring"])


class ConfigValidationTests(unittest.TestCase):
    def test_load_config_rejects_unsupported_ai_provider(self):
        from bosshunter.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("ai:\n  provider: openai\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "仅支持 Anthropic|ai.provider: anthropic"):
                load_config(config_path)

    def test_load_config_defaults_to_not_allowing_internships(self):
        from bosshunter.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("profile:\n  salary_min: 10\n", encoding="utf-8")

            config = load_config(config_path)

        self.assertIs(config["profile"]["allow_internship"], False)
        self.assertNotIn("prefilter_threshold", config["scoring"])


class PrefilterHardGateTests(unittest.TestCase):
    def test_deal_breakers_still_match_title_only(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": ["外包"], "salary_min": 0}}
        job = {"title": "AI产品经理", "jd": "非外包项目，团队稳定", "salary": "20-30K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 100)
        self.assertEqual(reason, "预筛通过")

    def test_deal_breaker_in_title_is_filtered(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": ["外包"], "salary_min": 0}}
        job = {"title": "AI产品经理 外包", "jd": "", "salary": "20-30K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 0)
        self.assertEqual(reason, "触发排除词: 外包")

    def test_default_rejects_internship_titles(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": [], "salary_min": 0}}
        job = {"title": "AI产品实习生", "jd": "", "salary": "3-5K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 0)
        self.assertEqual(reason, "实习/管培岗位")

    def test_default_rejects_management_trainee_titles(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": [], "salary_min": 0}}
        job = {"title": "产品管培生", "jd": "", "salary": "8-12K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 0)
        self.assertEqual(reason, "实习/管培岗位")

    def test_allow_internship_lets_internship_titles_pass_prefilter(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": [], "allow_internship": True, "salary_min": 0}}
        job = {"title": "AI Product Intern", "jd": "", "salary": "3-5K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 100)
        self.assertEqual(reason, "预筛通过")

    def test_salary_below_minimum_is_filtered(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": [], "salary_min": 100}}
        job = {"title": "AI产品经理", "jd": "", "salary": "12K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 0)
        self.assertEqual(reason, "薪资低于硬性要求: 12K < 100K")

    def test_passing_job_returns_hard_gate_pass(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": ["外包", "996"], "salary_min": 15}}
        job = {"title": "AI产品经理", "jd": "", "salary": "20-30K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 100)
        self.assertEqual(reason, "预筛通过")


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

    def test_dashboard_renders_monitor_execution_history(self):
        self.assertIn("MonitorExecutionView", self.source)
        self.assertIn("history", self.source)
        self.assertIn("<MonitorExecutionView history={history}", self.source)

    def test_dashboard_exposes_manual_refresh_button(self):
        self.assertIn("RefreshCw", self.source)
        self.assertIn("onClick={refresh}", self.source)

    def test_dashboard_exposes_batch_reject_for_selected_pending_jobs(self):
        # Arrange: DashboardPage source is loaded in setUp.

        # Act / Assert
        self.assertIn("rejectSelectedJobs", self.source)
        self.assertIn("/api/workbench/reject", self.source)
        self.assertIn("放弃已选", self.source)
        self.assertIn("确定放弃这", self.source)
        self.assertIn("setSelected(prev => prev.filter", self.source)

    def test_dashboard_sends_ready_greetings_without_second_confirmation(self):
        # Arrange: DashboardPage source is loaded in setUp.

        # Act / Assert
        self.assertIn("sendReadyGreetings", self.source)
        self.assertIn("direct_send: true", self.source)
        self.assertIn("已直接进入发送流程", self.source)
        self.assertNotIn("confirmDeliver(pendingGreetingJobs.map", self.source)
        self.assertNotIn("confirmDeliver([job.id])}>发送招呼语", self.source)


class SidebarTests(unittest.TestCase):
    def setUp(self):
        # Arrange
        self.source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "components"
            / "layout"
            / "Sidebar.tsx"
        ).read_text(encoding="utf-8")

    def test_sidebar_star_link_places_github_icon_left_and_centers_star_label(self):
        # Act / Assert
        self.assertIn("relative flex items-center", self.source)
        self.assertIn("absolute left-3", self.source)
        self.assertIn("mx-auto flex items-center justify-center", self.source)
        self.assertIn("text-xl", self.source)
        self.assertIn("text-yellow-400", self.source)


class HeaderTests(unittest.TestCase):
    def setUp(self):
        # Arrange
        self.source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "components"
            / "layout"
            / "Header.tsx"
        ).read_text(encoding="utf-8")

    def test_header_version_metadata_right_side_omits_duplicate_console_label(self):
        # Act / Assert
        self.assertNotIn("v2.0 · 本地控制台", self.source)
        self.assertIn("本地服务运行中", self.source)


class ConfigPageTests(unittest.TestCase):
    def setUp(self):
        # Arrange
        self.source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "pages"
            / "ConfigPage.tsx"
        ).read_text(encoding="utf-8")
        self.hook_source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "hooks"
            / "useConfig.ts"
        ).read_text(encoding="utf-8")

    def test_config_page_does_not_render_prefilter_threshold(self):
        # Act / Assert
        self.assertNotIn("prefilter_threshold", self.source)
        self.assertNotIn("预筛阈值", self.source)

    def test_allow_internship_switch_appears_below_deal_breakers(self):
        # Act
        deal_breakers_index = self.source.index("排除关键词")
        allow_internship_index = self.source.index("接受实习/管培岗位")

        # Assert
        self.assertGreater(allow_internship_index, deal_breakers_index)
        self.assertIn("profile.allow_internship", self.source)

    def test_config_page_api_failure_displays_error_instead_of_infinite_loading(self):
        # Act / Assert
        self.assertIn("error", self.hook_source)
        self.assertIn("!configRes.ok", self.hook_source)
        self.assertIn("!schemaRes.ok", self.hook_source)
        self.assertIn("配置加载失败", self.source)
        self.assertIn("请确认后端服务已启动", self.source)
        self.assertIn("error", self.source)


class ConfigSchemaTests(unittest.TestCase):
    def setUp(self):
        import json

        self.schema_source = (
            ROOT / "src" / "bosshunter" / "web" / "config_schema.json"
        ).read_text(encoding="utf-8")
        self.schema = json.loads(self.schema_source)

    def test_schema_does_not_include_prefilter_threshold(self):
        self.assertNotIn("prefilter_threshold", self.schema_source)

    def test_schema_adds_allow_internship_after_deal_breakers(self):
        profile = next(section for section in self.schema["sections"] if section["key"] == "profile")
        keys = [field["key"] for field in profile["fields"]]

        self.assertIn("allow_internship", keys)
        self.assertGreater(keys.index("allow_internship"), keys.index("deal_breakers"))

        allow_field = profile["fields"][keys.index("allow_internship")]
        self.assertEqual(allow_field["label"], "接受实习/管培岗位")
        self.assertEqual(allow_field["type"], "switch")
        self.assertIs(allow_field["default"], False)


class ScorerPrefilterTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "src" / "bosshunter" / "ai" / "scorer.py").read_text(encoding="utf-8")

    def test_scorer_no_longer_depends_on_prefilter_threshold(self):
        self.assertNotIn("prefilter_threshold", self.source)
        self.assertIn("if qs == 0:", self.source)


if __name__ == "__main__":
    unittest.main()
