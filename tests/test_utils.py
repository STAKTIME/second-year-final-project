import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import RateLimiter, esc, markdown_to_html, split_message, validate_display_name


class TestText(unittest.TestCase):
    def test_esc(self):
        self.assertEqual(esc("<b>a & b</b>"), "&lt;b&gt;a &amp; b&lt;/b&gt;")

    def test_markdown_to_html(self):
        self.assertEqual(markdown_to_html("**Важно** <тег> & ещё"), "<b>Важно</b> &lt;тег&gt; &amp; ещё")
        self.assertEqual(markdown_to_html("* раз\n- два"), "• раз\n• два")
        self.assertEqual(markdown_to_html("## Заголовок"), "<b>Заголовок</b>")

    def test_split_short(self):
        self.assertEqual(split_message("привет", 10), ["привет"])

    def test_split_long(self):
        text = "\n\n".join(["абзац " * 50] * 10)
        parts = split_message(text, 400)
        self.assertTrue(all(len(p) <= 400 for p in parts))
        self.assertEqual("".join(parts).replace(" ", "").replace("\n", ""),
                         text.replace(" ", "").replace("\n", ""))

    def test_split_without_spaces(self):
        self.assertEqual(split_message("x" * 25, 10), ["x" * 10, "x" * 10, "x" * 5])


class TestDisplayName(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(validate_display_name("  Эко   Воин "), ("Эко Воин", None))

    def test_errors(self):
        for bad in (None, "", "   ", "\u200b", "x" * 31, "@admin"):
            name, error = validate_display_name(bad)
            self.assertIsNone(name, bad)
            self.assertTrue(error)

    def test_max_len(self):
        self.assertEqual(validate_display_name("x" * 30)[0], "x" * 30)


class TestRateLimiter(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.limiter = RateLimiter(10, daily_limit=2, clock=lambda: self.now)

    def test_interval(self):
        self.assertEqual(self.limiter.check(1), 0)
        self.now = 5
        self.assertAlmostEqual(self.limiter.check(1), 5)
        self.assertEqual(self.limiter.check(2), 0)  # другой пользователь
        self.now = 10
        self.assertEqual(self.limiter.check(1), 0)

    def test_daily_limit(self):
        self.limiter.check(1)
        self.now = 100
        self.limiter.check(1)
        self.now = 200
        self.assertGreater(self.limiter.check(1), 0)
        self.now = 86401
        self.assertEqual(self.limiter.check(1), 0)


if __name__ == "__main__":
    unittest.main()
