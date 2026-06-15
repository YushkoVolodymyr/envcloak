"""Unit tests for envguard_core. Run: python3 -m unittest -v"""

import unittest

import envguard_core as eg

SAMPLE = """\
# Database config
NODE_ENV=production
export DB_HOST=localhost
DB_PORT=5432
DATABASE_URL=postgres://admin:s3cr3tP@ss@db.internal:5432/app
API_KEY=my_fake_4eC39HqLyjWDarjtT1zdp7dc
JWT=eyJhbGc.eyJzdWI.SflKxwRJ
EMPTY=
QUOTED="hello world"  # greeting
FEATURE_X=true
COUNT=42
UNICODE=пароль123
"""


class TestBlur(unittest.TestCase):
    def setUp(self):
        self.cfg = eg.merge_config(None)

    def test_length_preserved_exactly(self):
        out = eg.render_blurred(SAMPLE, self.cfg)
        self.assertEqual(len(out), len(SAMPLE))
        # byte-aligned line by line too
        for a, b in zip(SAMPLE.splitlines(), out.splitlines()):
            self.assertEqual(len(a), len(b))

    def test_char_classes(self):
        out = eg.render_blurred("API_KEY=Abc123-_.\n", self.cfg)
        self.assertEqual(out, "API_KEY=Xxx000-_.\n")

    def test_structure_preserved_url(self):
        out = eg.render_blurred(SAMPLE, self.cfg)
        line = [l for l in out.splitlines() if l.startswith("DATABASE_URL=")][0]
        # scheme letters blurred but :// @ : / structure kept
        self.assertIn("://", line)
        self.assertIn("@", line)
        self.assertNotIn("admin", line)
        self.assertNotIn("s3cr3t", line)

    def test_secret_is_masked(self):
        out = eg.render_blurred(SAMPLE, self.cfg)
        self.assertNotIn("my_fake_4eC39HqLyjWDarjtT1zdp7dc", out)
        self.assertIn("xx_xxxx_0", out)  # shape of my_fake_4...

    def test_reveal_allowlist_keys(self):
        out = eg.render_blurred(SAMPLE, self.cfg)
        self.assertIn("NODE_ENV=production", out)
        self.assertIn("DB_PORT=5432", out)

    def test_reveal_boolean_value(self):
        out = eg.render_blurred(SAMPLE, self.cfg)
        self.assertIn("FEATURE_X=true", out)

    def test_numeric_blurred_by_default(self):
        out = eg.render_blurred(SAMPLE, self.cfg)
        self.assertIn("COUNT=00", out)  # 42 -> 00 (not in reveal list)

    def test_empty_value_untouched(self):
        self.assertIn("EMPTY=\n", eg.render_blurred(SAMPLE, self.cfg))

    def test_quoted_value_quotes_and_comment_preserved(self):
        out = eg.render_blurred(SAMPLE, self.cfg)
        line = [l for l in out.splitlines() if l.startswith("QUOTED=")][0]
        self.assertTrue(line.startswith('QUOTED="'))
        self.assertIn("# greeting", line)
        self.assertNotIn("hello world", line)

    def test_comment_not_blurred(self):
        self.assertIn("# Database config", eg.render_blurred(SAMPLE, self.cfg))

    def test_unicode_is_blurred(self):
        out = eg.render_blurred(SAMPLE, self.cfg)
        self.assertNotIn("пароль", out)  # non-ASCII must not leak

    def test_partial_reveal(self):
        cfg = eg.merge_config({"partial_reveal": 2})
        out = eg.render_blurred("API_KEY=abcdefgh\n", cfg)
        self.assertEqual(out, "API_KEY=abxxxxgh\n")


class TestSummary(unittest.TestCase):
    def test_summary_flags(self):
        rows = {r["key"]: r for r in eg.summarize(SAMPLE)}
        self.assertTrue(rows["NODE_ENV"]["revealed"])
        self.assertFalse(rows["API_KEY"]["revealed"])
        self.assertTrue(rows["DB_HOST"]["exported"])
        self.assertTrue(rows["QUOTED"]["quoted"])
        self.assertEqual(rows["NODE_ENV"]["line"], 2)


class TestEdits(unittest.TestCase):
    def test_set_value_unquoted(self):
        out = eg.set_value(SAMPLE, "API_KEY", "newsecret123")
        self.assertIn("API_KEY=newsecret123", out)

    def test_set_value_quotes_when_needed(self):
        out = eg.set_value(SAMPLE, "API_KEY", "has spaces#hash")
        self.assertIn('API_KEY="has spaces#hash"', out)

    def test_set_value_preserves_inline_comment(self):
        out = eg.set_value(SAMPLE, "QUOTED", "bye")
        line = [l for l in out.splitlines() if l.startswith("QUOTED=")][0]
        self.assertIn("# greeting", line)

    def test_set_value_missing_key_raises(self):
        with self.assertRaises(ValueError):
            eg.set_value(SAMPLE, "NOPE", "x")

    def test_round_trip_guard(self):
        blurred = eg.render_blurred(SAMPLE)
        masked = [l for l in blurred.splitlines() if l.startswith("API_KEY=")][0]
        masked_value = masked.split("=", 1)[1]
        with self.assertRaises(ValueError):
            eg.set_value(SAMPLE, "API_KEY", masked_value)

    def test_round_trip_guard_inexact_mask(self):
        # an all-x/0 value that is NOT a byte-exact echo must still be rejected
        with self.assertRaises(ValueError):
            eg.set_value(SAMPLE, "API_KEY", "xx_xxxx_0xX00xxxxxxxxxxxxx0xxx0xx")

    def test_set_value_real_secret_allowed(self):
        # a genuine new secret (has real letters/digits) is fine
        out = eg.set_value(SAMPLE, "API_KEY", "my_fake_brandNew99")
        self.assertIn("API_KEY=my_fake_brandNew99", out)

    def test_rename_key_preserves_value(self):
        out = eg.rename_key(SAMPLE, "API_KEY", "SERVICE_KEY")
        self.assertIn("SERVICE_KEY=my_fake_4eC39HqLyjWDarjtT1zdp7dc", out)
        self.assertNotIn("API_KEY=", out)

    def test_rename_to_existing_raises(self):
        with self.assertRaises(ValueError):
            eg.rename_key(SAMPLE, "API_KEY", "JWT")

    def test_rename_invalid_name(self):
        with self.assertRaises(ValueError):
            eg.rename_key(SAMPLE, "API_KEY", "bad name")

    def test_add_key(self):
        out = eg.add_key(SAMPLE, "NEW_VAR", "val")
        self.assertIn("NEW_VAR=val\n", out)

    def test_add_existing_raises(self):
        with self.assertRaises(ValueError):
            eg.add_key(SAMPLE, "API_KEY", "x")

    def test_add_key_appends_newline_if_missing(self):
        out = eg.add_key("A=1", "B", "2")
        self.assertEqual(out, "A=1\nB=2\n")

    def test_delete_key(self):
        out = eg.delete_key(SAMPLE, "API_KEY")
        self.assertNotIn("API_KEY", out)
        self.assertIn("JWT=", out)  # neighbours intact

    def test_delete_missing_raises(self):
        with self.assertRaises(ValueError):
            eg.delete_key(SAMPLE, "NOPE")

    def test_idempotent_blur(self):
        once = eg.render_blurred(SAMPLE)
        twice = eg.render_blurred(once)
        # revealed lines stable; masked lines already only x/0 so stable too
        self.assertEqual(len(once), len(twice))


class TestReplaceRange(unittest.TestCase):
    def test_replace_range_on_comment(self):
        text = "# old comment\nA=secretvalue\n"
        out = eg.replace_range(text, 2, 13, "new comment")
        self.assertIn("# new comment", out)
        self.assertIn("A=secretvalue", out)

    def test_replace_range_refuses_masked_value(self):
        text = "A=secretvalue\n"
        blurred = eg.render_blurred(text)
        idx = blurred.index("xxxx")  # inside the masked value
        with self.assertRaises(ValueError):
            eg.replace_range(text, idx, idx + 4, "evil")

    def test_replace_range_rejects_echo(self):
        text = "# hello\nA=secretvalue\n"
        blurred = eg.render_blurred(text)
        with self.assertRaises(ValueError):
            eg.replace_range(text, 0, 7, blurred[0:7])


class TestMultiline(unittest.TestCase):
    def test_multiline_quoted_value(self):
        text = 'KEY="line1\nline2"\nNEXT=plain\n'
        out = eg.render_blurred(text)
        self.assertEqual(len(out), len(text))
        self.assertIn("NEXT=", out)
        self.assertNotIn("line1", out)
        rows = {r["key"]: r for r in eg.summarize(text)}
        self.assertIn("KEY", rows)
        self.assertIn("NEXT", rows)


if __name__ == "__main__":
    unittest.main()
