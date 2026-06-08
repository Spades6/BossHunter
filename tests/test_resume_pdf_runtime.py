import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class ResumeArtifactTests(unittest.TestCase):
    def test_prompt_forbids_resume_tailoring_artifacts(self):
        from bosshunter.ai.resume import RESUME_TAILOR_PROMPT

        self.assertIn("只输出简历正文", RESUME_TAILOR_PROMPT)
        self.assertIn("不输出任何前言、说明、备注、免责声明", RESUME_TAILOR_PROMPT)
        self.assertIn("不允许单独新增“岗位匹配亮点”", RESUME_TAILOR_PROMPT)

    def test_finds_resume_artifact_phrases(self):
        from bosshunter.ai.resume import _find_resume_artifacts

        markdown = "以下内容基于原始简历整理。\n\n## 岗位匹配亮点\n- 补充说明：未虚构。"

        artifacts = _find_resume_artifacts(markdown)

        self.assertIn("以下内容基于", artifacts)
        self.assertIn("岗位匹配亮点", artifacts)
        self.assertIn("补充说明", artifacts)
        self.assertIn("未虚构", artifacts)

    @patch("bosshunter.ai.resume._render_pdf")
    @patch("bosshunter.ai.resume._call_claude")
    @patch("bosshunter.ai.resume.get_db")
    def test_dirty_resume_output_is_not_written(self, get_db, call_claude, render_pdf):
        from bosshunter.ai.resume import generate_tailored_resume

        db = Mock()
        db.execute.return_value.fetchone.return_value = {
            "id": "job-1",
            "company": "Example",
            "title": "Engineer",
            "salary": "10-20K",
            "jd": "需要 Python 经验",
        }
        get_db.return_value = db
        call_claude.return_value = "## 岗位匹配亮点\n以下内容基于原始简历整理。"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resume_path = root / "resume.md"
            output_dir = root / "out"
            resume_path.write_text("# 张三\n\nPython 工程师", encoding="utf-8")

            result = generate_tailored_resume(
                "job-1",
                {
                    "profile": {
                        "resume_path": str(resume_path),
                        "resume_output_dir": str(output_dir),
                    }
                },
            )

            self.assertIsNone(result)
            self.assertFalse(list(output_dir.glob("*")))

        render_pdf.assert_not_called()


class ResumePdfRuntimeTests(unittest.TestCase):
    @patch("bosshunter.ai.resume.close_tab")
    @patch("bosshunter.ai.resume.print_pdf")
    @patch("bosshunter.ai.resume.new_tab")
    def test_render_pdf_via_cdp_uses_browser_facade(self, new_tab, print_pdf, close_tab):
        from bosshunter.ai.resume import _render_pdf_via_cdp

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "resume.pdf"
            new_tab.return_value = "target-1"
            print_pdf.side_effect = lambda target, file_path: output.write_bytes(b"pdf") or True

            result = _render_pdf_via_cdp("<html><body>ok</body></html>", output)

        self.assertTrue(result)
        new_tab.assert_called_once()
        self.assertTrue(new_tab.call_args.args[0].startswith("file:///"))
        print_pdf.assert_called_once_with("target-1", output)
        close_tab.assert_called_once_with("target-1")


if __name__ == "__main__":
    unittest.main()
