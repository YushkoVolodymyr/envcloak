"""Unit tests for tools/resolve_interpreter.py (the interpreter-baking brain).

Run: python -m unittest -v
"""

import importlib.util
import json
import os
import tempfile
import unittest

_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tools", "resolve_interpreter.py"
)
_spec = importlib.util.spec_from_file_location("resolve_interpreter", _PATH)
ri = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ri)


class TestSettingsPath(unittest.TestCase):
    def test_honours_config_dir(self):
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join("X", "cfg")
        try:
            self.assertEqual(
                ri.settings_path(), os.path.join("X", "cfg", "settings.json")
            )
        finally:
            if old is None:
                del os.environ["CLAUDE_CONFIG_DIR"]
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old


class TestLoadSettings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "settings.json")

    def test_missing_file_is_empty_and_ok(self):
        data, ok = ri.load_settings(self.path)
        self.assertEqual((data, ok), ({}, True))

    def test_valid_object(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"model": "opus"}, f)
        data, ok = ri.load_settings(self.path)
        self.assertTrue(ok)
        self.assertEqual(data["model"], "opus")

    def test_invalid_json_is_not_ok(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        data, ok = ri.load_settings(self.path)
        self.assertIsNone(data)
        self.assertFalse(ok)

    def test_non_object_top_level_is_not_ok(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        data, ok = ri.load_settings(self.path)
        self.assertIsNone(data)
        self.assertFalse(ok)


class TestMainBake(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = os.path.join(self.dir, "cfg")
        self._old = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_DATA")}
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _settings(self):
        with open(os.path.join(self.cfg, "settings.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_creates_and_bakes(self):
        self.assertEqual(ri.main(), 0)
        env = self._settings()["env"]
        self.assertEqual(env[ri.ENV_KEY], ri.sys.executable)

    def test_preserves_existing_keys(self):
        os.makedirs(self.cfg, exist_ok=True)
        with open(os.path.join(self.cfg, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"model": "opus", "env": {"FOO": "bar"}}, f)
        ri.main()
        data = self._settings()
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["env"]["FOO"], "bar")
        self.assertEqual(data["env"][ri.ENV_KEY], ri.sys.executable)

    def test_idempotent(self):
        ri.main()
        before = os.path.getmtime(os.path.join(self.cfg, "settings.json"))
        ri.main()  # value already matches -> no rewrite
        after = os.path.getmtime(os.path.join(self.cfg, "settings.json"))
        self.assertEqual(before, after)

    def test_does_not_clobber_invalid_settings(self):
        os.makedirs(self.cfg, exist_ok=True)
        raw = "{ this is : not json"
        with open(os.path.join(self.cfg, "settings.json"), "w", encoding="utf-8") as f:
            f.write(raw)
        self.assertEqual(ri.main(), 0)
        with open(os.path.join(self.cfg, "settings.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), raw)


if __name__ == "__main__":
    unittest.main()
