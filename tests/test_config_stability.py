"""The Bearer token must survive a restart. 2.4.6 broke exactly this.

A patch aimed at the start banner matched the wrong occurrence of `if gw_key:`
and landed inside the config-finalisation function, where it referenced names
that do not exist in that scope. Finalising the config raised NameError, the
caller fell back to building a fresh config, and every start minted a new token,
so already-configured folders lost their connection.

The cosmetic bug it was chasing was trivial. The bug it caused was not. These
tests pin the contract: finalisation returns a config, never invents a token,
and never invents a gateway key.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import launcher


class ConfigFinalisationIsStable(unittest.TestCase):
    def base(self):
        return {
            "token": "bridge-secret-token-KEEPME",
            "workspace": str(ROOT),
            "auth_mode": "legacy",
        }

    def test_it_returns_a_config(self):
        with mock.patch.object(launcher, "load_json", return_value={}):
            result = launcher.ensure_ide_gateway_key(self.base())
        self.assertIsInstance(result, dict)

    def test_the_token_is_never_regenerated(self):
        with mock.patch.object(launcher, "load_json", return_value={}):
            once = launcher.ensure_ide_gateway_key(self.base())
            twice = launcher.ensure_ide_gateway_key(dict(once))
        self.assertEqual(once["token"], "bridge-secret-token-KEEPME")
        self.assertEqual(twice["token"], "bridge-secret-token-KEEPME")

    def test_no_gateway_key_is_invented(self):
        with mock.patch.object(launcher, "load_json", return_value={}):
            result = launcher.ensure_ide_gateway_key(self.base())
        self.assertFalse(result.get("ide_gateway_api_key"))

    def test_an_existing_gateway_key_is_preserved(self):
        stored = {"ide_gateway_api_key": "ideg_existing"}
        with mock.patch.object(launcher, "load_json", return_value=stored):
            result = launcher.ensure_ide_gateway_key(self.base())
        self.assertEqual(result.get("ide_gateway_api_key"), "ideg_existing")


class NoStrayProbeInConfigCode(unittest.TestCase):
    def test_finalisation_does_not_open_sockets(self):
        source = (ROOT / "launcher.py").read_text(encoding="utf-8")
        start = source.index("def ensure_ide_gateway_key")
        end = source.index("\ndef ", start + 10)
        self.assertNotIn("socket", source[start:end])


if __name__ == "__main__":
    unittest.main()
