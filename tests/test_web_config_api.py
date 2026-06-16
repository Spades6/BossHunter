import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from bosshunter.web import server


class WebConfigApiTests(unittest.TestCase):
	def test_redacted_config_does_not_return_raw_api_key(self):
		config = {"ai": {"api_key": "sk-ant-12345678", "model": "claude"}}

		redacted = server._redact_config_for_response(config)

		self.assertNotIn("api_key", redacted["ai"])
		self.assertEqual(redacted["ai"]["api_key_masked"], "sk-a***5678")
		self.assertEqual(config["ai"]["api_key"], "sk-ant-12345678")

	def test_sanitize_config_strips_display_fields_and_preserves_blank_key(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"api_key": "sk-ant-12345678", "model": "old"}}, sort_keys=False),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"api_key": "",
						"api_key_masked": "sk-a***5678",
						"model": "new",
					}
				})

		self.assertEqual(cleaned["ai"]["api_key"], "sk-ant-12345678")
		self.assertEqual(cleaned["ai"]["model"], "new")
		self.assertNotIn("api_key_masked", cleaned["ai"])

	def test_sanitize_config_preserves_omitted_api_key(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"api_key": "sk-ant-12345678", "model": "old"}}, sort_keys=False),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"api_key_masked": "sk-a***5678",
						"model": "new",
					}
				})

		self.assertEqual(cleaned["ai"]["api_key"], "sk-ant-12345678")
		self.assertEqual(cleaned["ai"]["model"], "new")
		self.assertNotIn("api_key_masked", cleaned["ai"])

	def test_sanitize_config_accepts_new_api_key(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"api_key": "sk-ant-old", "model": "old"}}, sort_keys=False),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"api_key": "sk-ant-new",
						"api_key_masked": "sk-a***-old",
					}
				})

		self.assertEqual(cleaned["ai"]["api_key"], "sk-ant-new")
		self.assertNotIn("api_key_masked", cleaned["ai"])

	def test_sanitize_config_forces_fixed_anthropic_provider(self):
		# Arrange
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(yaml.dump({"ai": {"provider": "anthropic"}}, sort_keys=False), encoding="utf-8")

			# Act
			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({"ai": {"provider": "openai", "model": "claude-sonnet-4-6"}})

		# Assert
		self.assertEqual(cleaned["ai"]["provider"], "anthropic")
		self.assertEqual(cleaned["ai"]["model"], "claude-sonnet-4-6")

	def test_redacted_config_does_not_return_raw_auth_token(self):
		config = {"ai": {"auth_token": "auth-token-12345678", "model": "claude"}}

		redacted = server._redact_config_for_response(config)

		self.assertNotIn("auth_token", redacted["ai"])
		self.assertEqual(redacted["ai"]["auth_token_masked"], "auth***5678")
		self.assertEqual(config["ai"]["auth_token"], "auth-token-12345678")

	def test_sanitize_config_strips_auth_token_display_fields_and_preserves_blank_token(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"auth_token": "auth-token-12345678", "model": "old"}}, sort_keys=False),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"auth_token": "",
						"auth_token_masked": "auth***5678",
						"has_auth_token": True,
						"model": "new",
					}
				})

		self.assertEqual(cleaned["ai"]["auth_token"], "auth-token-12345678")
		self.assertEqual(cleaned["ai"]["model"], "new")
		self.assertNotIn("auth_token_masked", cleaned["ai"])
		self.assertNotIn("has_auth_token", cleaned["ai"])


if __name__ == "__main__":
	unittest.main()
