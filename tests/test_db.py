import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"

    def tearDown(self):
        self.tmp.cleanup()

    def add_photo(self, user_id=1, unique="u1"):
        return db.add_photo(user_id, user_id, "file", unique, "glass", 0.9)


class TestMigration(DbTestCase):
    def test_upgrade_from_v1(self):
        # База в формате первых версий бота
        conn = sqlite3.connect(self.path)
        conn.execute("""CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT,
                        display_name TEXT, is_anonymous BOOLEAN DEFAULT 0, score INTEGER DEFAULT 0)""")
        conn.execute("INSERT INTO users VALUES (1, 'old', 'Иван', 0, 50)")
        conn.execute("INSERT INTO users VALUES (2, 'dup', 'иван', 0, 10)")
        conn.commit()
        conn.close()

        db.init_db(self.path)
        self.assertEqual(db.get_user_profile(1), ("old", "Иван", None, 50, 0))
        self.assertTrue(self.add_photo())
        # Имя первого пользователя занято и для третьего
        db.add_user(3, "new")
        self.assertFalse(db.set_display_name(3, "ИВАН"))

    def test_init_twice(self):
        db.init_db(self.path)
        db.init_db(self.path)


class TestUsers(DbTestCase):
    def setUp(self):
        super().setUp()
        db.init_db(self.path)

    def test_add_user_updates_username(self):
        db.add_user(1, "a")
        db.add_user(1, "b")
        self.assertEqual(db.get_user_profile(1)[0], "b")

    def test_unique_display_name(self):
        db.add_user(1, "a")
        db.add_user(2, "b")
        self.assertTrue(db.set_display_name(1, "Эко-Воин"))
        self.assertFalse(db.set_display_name(2, "эко-воин"))
        self.assertTrue(db.set_display_name(1, "эко-воин"))  # своё имя можно поменять регистром

    def test_toggle_anonymity(self):
        db.add_user(1, "a")
        self.assertTrue(db.toggle_anonymity(1))
        self.assertFalse(db.toggle_anonymity(1))
        self.assertIsNone(db.toggle_anonymity(999))

    def test_top_hides_anonymous_and_zero(self):
        for uid in (1, 2, 3):
            db.add_user(uid, f"u{uid}")
        db.rate_photo(self.add_photo(1, "a"), 1, True, 10, 100)
        db.rate_photo(self.add_photo(2, "b"), 2, True, 10, 100)
        db.toggle_anonymity(2)
        self.assertEqual(db.get_top_users(), [(None, "u1", None, 10)])


class TestRating(DbTestCase):
    def setUp(self):
        super().setUp()
        db.init_db(self.path)
        db.add_user(1, "a")

    def score(self, user_id=1):
        return db.get_user_profile(user_id)[3]

    def test_points_once_per_photo(self):
        pid = self.add_photo()
        self.assertEqual(db.rate_photo(pid, 1, True, 10, 100), ("ok", 10))
        self.assertEqual(db.rate_photo(pid, 1, True, 10, 100), ("already", 0))
        self.assertEqual(db.rate_photo(pid, 1, False, 10, 100), ("already", 0))
        self.assertEqual(self.score(), 10)

    def test_same_photo_resent(self):
        db.rate_photo(self.add_photo(unique="same"), 1, True, 10, 100)
        self.assertEqual(db.rate_photo(self.add_photo(unique="same"), 1, True, 10, 100), ("duplicate", 0))
        self.assertEqual(self.score(), 10)

    def test_daily_limit(self):
        results = [db.rate_photo(self.add_photo(unique=str(i)), 1, True, 10, 30)[0] for i in range(4)]
        self.assertEqual(results, ["ok", "ok", "ok", "limit"])
        self.assertEqual(self.score(), 30)

    def test_foreign_photo(self):
        db.add_user(2, "b")
        pid = self.add_photo(1)
        self.assertEqual(db.rate_photo(pid, 2, True, 10, 100), ("foreign", 0))
        self.assertEqual(self.score(2), 0)

    def test_not_found(self):
        self.assertEqual(db.rate_photo(12345, 1, True, 10, 100), ("not_found", 0))

    def test_user_missing_in_db_still_gets_points(self):
        pid = self.add_photo(user_id=7)
        db.rate_photo(pid, 7, True, 10, 100)
        self.assertEqual(self.score(7), 10)

    def test_correct_class_only_after_wrong(self):
        pid = self.add_photo()
        self.assertFalse(db.set_correct_class(pid, 1, "metal"))
        db.rate_photo(pid, 1, False, 0, 100)
        self.assertFalse(db.set_correct_class(pid, 2, "metal"))
        self.assertTrue(db.set_correct_class(pid, 1, "metal"))
        self.assertEqual(db.get_photo(pid)["correct_class"], "metal")
        self.assertEqual(self.score(), 0)


if __name__ == "__main__":
    unittest.main()
