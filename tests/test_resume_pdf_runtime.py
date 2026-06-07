import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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
