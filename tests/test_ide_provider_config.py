import os
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from plugins.ide_provider.state import normalize_config, DEFAULT_CONFIG


class IdeProviderConfigTests(unittest.TestCase):
    def test_empty_config_normalizes_to_defaults(self):
        result = normalize_config({})
        for key, value in DEFAULT_CONFIG.items():
            self.assertEqual(result[key], value)

    def test_zero_host_rejected(self):
        with self.assertRaisesRegex(ValueError, "default_host"):
            normalize_config({"default_host": "0.0.0.0"})

    def test_external_host_rejected(self):
        with self.assertRaisesRegex(ValueError, "default_host"):
            normalize_config({"default_host": "192.168.1.5"})

    def test_localhost_normalized_to_127_0_0_1(self):
        result = normalize_config({"default_host": "localhost"})
        self.assertEqual(result["default_host"], "127.0.0.1")

    def test_invalid_port_range_low_rejected(self):
        with self.assertRaisesRegex(ValueError, "port_range"):
            normalize_config({"port_range": [80, 8899]})

    def test_invalid_port_range_high_rejected(self):
        with self.assertRaisesRegex(ValueError, "port_range"):
            normalize_config({"port_range": [8787, 99999]})

    def test_inverted_port_range_rejected(self):
        with self.assertRaisesRegex(ValueError, "port_range"):
            normalize_config({"port_range": [9000, 8000]})

    def test_bad_model_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "default_model_id"):
            normalize_config({"default_model_id": "bad model!"})
        result = normalize_config({"default_model_id": "my-model.v1"})
        self.assertEqual(result["default_model_id"], "my-model.v1")

    def test_request_timeout_out_of_range_rejected(self):
        with self.assertRaisesRegex(ValueError, "request_timeout_seconds"):
            normalize_config({"request_timeout_seconds": 4})
        with self.assertRaisesRegex(ValueError, "request_timeout_seconds"):
            normalize_config({"request_timeout_seconds": 1801})

    def test_wait_timeout_out_of_range_rejected(self):
        with self.assertRaisesRegex(ValueError, "wait_timeout_seconds"):
            normalize_config({"wait_timeout_seconds": 0})
        with self.assertRaisesRegex(ValueError, "wait_timeout_seconds"):
            normalize_config({"wait_timeout_seconds": 86401})

    def test_max_pending_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, "max_pending_requests"):
            normalize_config({"max_pending_requests": 0})

    def test_custom_config_overrides_defaults(self):
        result = normalize_config({
            "port_range": [9000, 9100],
            "default_model_id": "custom-model",
        })
        self.assertEqual(result["port_range"], [9000, 9100])
        self.assertEqual(result["default_model_id"], "custom-model")


if __name__ == "__main__":
    unittest.main()
