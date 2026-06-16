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


class TestRemovableDenyRules(unittest.TestCase):
    def test_covers_base_wildcards_and_depth(self):
        rules = ri.removable_deny_rules()
        for r in ("Read(.env)", "Read(.env.*)", "Read(**/.env)", "Read(**/.env.*)",
                  "Edit(.env)", "Write(.env.*)"):
            self.assertIn(r, rules)

    def test_covers_legacy_enumerated_suffixes(self):
        rules = ri.removable_deny_rules()
        self.assertIn("Read(.env.local)", rules)
        self.assertIn("Read(.env.production)", rules)


class TestLoadProtectedGlobs(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old = os.environ.get("CLAUDE_PLUGIN_ROOT")
        os.environ["CLAUDE_PLUGIN_ROOT"] = self.dir

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        else:
            os.environ["CLAUDE_PLUGIN_ROOT"] = self._old

    def _write_config(self, obj):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_base_when_no_config(self):
        self.assertEqual(ri.load_protected_globs(), list(ri.BASE_PROTECTED_GLOBS))

    def test_extends_with_allowed_path_globs(self):
        self._write_config({"allowed_path_globs": ["config/*.env", "secrets/*"]})
        globs = ri.load_protected_globs()
        self.assertEqual(globs[:2], list(ri.BASE_PROTECTED_GLOBS))
        self.assertIn("config/*.env", globs)
        self.assertIn("secrets/*", globs)

    def test_ignores_duplicates_and_non_strings(self):
        self._write_config({"allowed_path_globs": [".env", 123, "", "x/*.env"]})
        globs = ri.load_protected_globs()
        self.assertEqual(globs.count(".env"), 1)
        self.assertIn("x/*.env", globs)

    def test_bad_config_falls_back_to_base(self):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertEqual(ri.load_protected_globs(), list(ri.BASE_PROTECTED_GLOBS))


class TestMergePermissions(unittest.TestCase):
    def test_adds_allow_under_both_prefixes_no_deny(self):
        data = {}
        self.assertTrue(ri.merge_permissions(data))
        perms = data["permissions"]
        # plugin-namespaced names are what auto mode actually matches
        self.assertIn("mcp__plugin_envcloak_envcloak", perms["allow"])
        self.assertIn("mcp__plugin_envcloak_envcloak__env_read", perms["allow"])
        # plain (non-plugin) install names kept as a fallback
        self.assertIn("mcp__envcloak__env_read", perms["allow"])
        for name in ri.ENV_TOOL_NAMES:
            self.assertIn(f"mcp__plugin_envcloak_envcloak__{name}", perms["allow"])
            self.assertIn(f"mcp__envcloak__{name}", perms["allow"])
        # we no longer inject any .env deny rules
        self.assertEqual(perms.get("deny", []), [])

    def test_retracts_env_deny_rules(self):
        data = {"permissions": {"deny": [
            "Read(.env)", "Read(.env.*)", "Read(**/.env)", "Read(**/.env.*)",
            "Edit(.env)", "Write(.env.*)", "Read(.env.local)",
            "Read(secrets.txt)",  # unrelated user rule -> must survive
        ]}}
        self.assertTrue(ri.merge_permissions(data))
        self.assertEqual(data["permissions"]["deny"], ["Read(secrets.txt)"])

    def test_retracts_config_derived_env_deny(self):
        # a glob from allowed_path_globs should also be retracted
        d = tempfile.mkdtemp()
        old = os.environ.get("CLAUDE_PLUGIN_ROOT")
        os.environ["CLAUDE_PLUGIN_ROOT"] = d
        try:
            with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"allowed_path_globs": ["config/*.env"]}, f)
            data = {"permissions": {"deny": ["Read(config/*.env)", "Edit(keep.me)"]}}
            ri.merge_permissions(data)
            self.assertEqual(data["permissions"]["deny"], ["Edit(keep.me)"])
        finally:
            if old is None:
                os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            else:
                os.environ["CLAUDE_PLUGIN_ROOT"] = old

    def test_idempotent_no_duplicates(self):
        data = {}
        ri.merge_permissions(data)
        self.assertFalse(ri.merge_permissions(data))  # nothing new
        allow = data["permissions"]["allow"]
        self.assertEqual(len(allow), len(set(allow)))

    def test_preserves_existing_allow_order(self):
        data = {"permissions": {"allow": ["Bash(ls:*)"]}}
        ri.merge_permissions(data)
        self.assertEqual(data["permissions"]["allow"][0], "Bash(ls:*)")
        self.assertIn("mcp__plugin_envcloak_envcloak__env_read", data["permissions"]["allow"])

    def test_tolerates_non_list_buckets(self):
        data = {"permissions": {"deny": "oops", "allow": None}}
        self.assertTrue(ri.merge_permissions(data))
        self.assertIsInstance(data["permissions"]["allow"], list)
        self.assertIn("mcp__plugin_envcloak_envcloak__env_read", data["permissions"]["allow"])


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
