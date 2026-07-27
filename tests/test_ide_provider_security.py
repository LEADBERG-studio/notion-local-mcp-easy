import os
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from plugins.ide_provider.security import generate_token, check_auth, redact_line


class IdeProviderSecurityTests(unittest.TestCase):
    def test_token_has_idep_prefix(self):
        token = generate_token()
        self.assertTrue(token.startswith("idep_"))

    def test_tokens_are_unique(self):
        tokens = {generate_token() for _ in range(100)}
        self.assertEqual(len(tokens), 100)

    def test_check_auth_accepts_valid_bearer(self):
        token = generate_token()
        self.assertTrue(check_auth({"Authorization": f"Bearer {token}"}, token))

    def test_check_auth_rejects_missing_auth(self):
        token = generate_token()
        self.assertFalse(check_auth({}, token))

    def test_check_auth_rejects_bad_token(self):
        token = generate_token()
        self.assertFalse(check_auth({"Authorization": "Bearer wrong-token"}, token))

    def test_check_auth_accepts_x_api_key_header(self):
        token = generate_token()
        self.assertTrue(check_auth({"X-API-Key": token}, token))

    def test_redact_line_removes_idep_token(self):
        line = redact_line("token=idep_abc123")
        self.assertNotIn("idep_abc123", line)

    def test_redact_line_removes_authorization_header(self):
        line = redact_line("Authorization: Bearer idep_xyz")
        self.assertIn("***", line)
        self.assertNotIn("idep_xyz", line)

    def test_redact_line_removes_password_field(self):
        line = redact_line("password: hunter2")
        self.assertIn("***", line)
        self.assertNotIn("hunter2", line)

    def test_redact_line_removes_json_quoted_password(self):
        line = redact_line('{"password":"hunter2"}')
        self.assertNotIn("hunter2", line)

    def test_redact_line_removes_json_quoted_api_key(self):
        line = redact_line('{"api_key":"secret123"}')
        self.assertNotIn("secret123", line)


if __name__ == "__main__":
    unittest.main()
