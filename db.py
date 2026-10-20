"""Работа с базой SQLite: пользователи, фото, очки, отзывы о распознавании."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "eco_bot.db"

# Номер версии схемы хранится в PRAGMA user_version
SCHEMA_VERSION = 2


@contextmanager
def connect():
    """Открывает соединение, коммитит при успехе, откатывает при ошибке и всегда закрывает."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path=None):
    """Создаёт базу или обновляет схему существующей до SCHEMA_VERSION."""
    global DB_PATH
    if path is not None:
        DB_PATH = Path(path)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]

        if version < 1:
            # Версия 1 — исходная таблица из первых версий бота
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    display_name TEXT,
                    is_anonymous BOOLEAN DEFAULT 0,
                    score INTEGER DEFAULT 0
                )
            """)

        if version < 2:
            # Версия 2: ключ для проверки уникальности имени и таблица фото
            columns = [row[1] for row in conn.execute("PRAGMA table_info(users)")]
            if "display_name_key" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN display_name_key TEXT")
            if "first_name" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS users_display_name_key
                ON users(display_name_key) WHERE display_name_key IS NOT NULL
            """)
            # Заполняем ключ для уже заданных имён; дубликаты оставляем без ключа
            for user_id, name in conn.execute(
                "SELECT user_id, display_name FROM users WHERE display_name IS NOT NULL"
            ).fetchall():
                try:
                    conn.execute(
                        "UPDATE users SET display_name_key = ? WHERE user_id = ?",
                        (name_key(name), user_id),
                    )
                except sqlite3.IntegrityError:
                    pass
            conn.execute("""
                CREATE TABLE IF NOT EXISTS photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    file_id TEXT NOT NULL,
                    file_unique_id TEXT NOT NULL,
                    predicted_class TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    feedback INTEGER,          -- NULL: нет отзыва, 1: верно, 0: неверно
                    correct_class TEXT,        -- правильный класс от пользователя
                    points INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS photos_user ON photos(user_id, created_at)")

        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def name_key(name):
    """Ключ для сравнения имён без учёта регистра (работает и для кириллицы)."""
    return " ".join(name.casefold().split())


def add_user(user_id, username, first_name=None):
    """Добавляет пользователя или обновляет его ник и имя из Telegram."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, first_name = excluded.first_name
            """,
            (user_id, username, first_name),
        )


def get_top_users(limit=5):
    """Топ по очкам, без анонимных и без пользователей с нулём очков."""
    with connect() as conn:
        return conn.execute(
            """
            SELECT display_name, username, first_name, score FROM users
            WHERE is_anonymous = 0 AND score > 0
            ORDER BY score DESC, user_id LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_user_profile(user_id):
    """(username, display_name, first_name, score, is_anonymous) или None."""
    with connect() as conn:
        return conn.execute(
            "SELECT username, display_name, first_name, score, is_anonymous FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()


def set_display_name(user_id, new_name):
    """Меняет имя. Возвращает False, если такое имя уже занято другим пользователем."""
    try:
        with connect() as conn:
            conn.execute(
                "UPDATE users SET display_name = ?, display_name_key = ? WHERE user_id = ?",
                (new_name, name_key(new_name), user_id),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def toggle_anonymity(user_id):
    """Переключает видимость в топе и возвращает новое значение is_anonymous."""
    with connect() as conn:
        conn.execute(
            "UPDATE users SET is_anonymous = NOT is_anonymous WHERE user_id = ?", (user_id,)
        )
        row = conn.execute(
            "SELECT is_anonymous FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return bool(row[0]) if row else None


# ==== ФОТО И ОЧКИ ====
def add_photo(user_id, chat_id, file_id, file_unique_id, predicted_class, confidence):
    """Сохраняет результат распознавания и возвращает id записи."""
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO photos (user_id, chat_id, file_id, file_unique_id, predicted_class, confidence)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, chat_id, file_id, file_unique_id, predicted_class, confidence),
        )
        return cur.lastrowid


def get_photo(photo_id):
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()


def rate_photo(photo_id, user_id, correct, points, daily_limit):
    """Сохраняет отзыв о распознавании и начисляет очки за подтверждение.

    Очки начисляются один раз за фото, только его автору, не за повторно
    отправленное то же фото и не больше daily_limit в сутки.
    Возвращает (status, points): status — "ok", "duplicate", "limit",
    "already" (отзыв уже был), "foreign" (чужое фото) или "not_found".
    """
    with connect() as conn:
        # BEGIN IMMEDIATE блокирует запись: два одновременных нажатия не начислят очки дважды
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT user_id, file_unique_id, feedback FROM photos WHERE id = ?", (photo_id,)
            ).fetchone()
            if row is None:
                result = ("not_found", 0)
            elif row[0] != user_id:
                result = ("foreign", 0)
            elif row[2] is not None:
                result = ("already", 0)
            else:
                status, award = "ok", 0
                if correct:
                    award = points
                    duplicate = conn.execute(
                        """
                        SELECT 1 FROM photos
                        WHERE user_id = ? AND file_unique_id = ? AND points > 0 AND id != ?
                        """,
                        (user_id, row[1], photo_id),
                    ).fetchone()
                    today = conn.execute(
                        """
                        SELECT COALESCE(SUM(points), 0) FROM photos
                        WHERE user_id = ? AND created_at >= datetime('now', '-1 day')
                        """,
                        (user_id,),
                    ).fetchone()[0]
                    if duplicate:
                        status, award = "duplicate", 0
                    elif today + award > daily_limit:
                        status, award = "limit", 0
                conn.execute(
                    "UPDATE photos SET feedback = ?, points = ? WHERE id = ?",
                    (1 if correct else 0, award, photo_id),
                )
                if award:
                    conn.execute(
                        "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
                    )
                    conn.execute(
                        "UPDATE users SET score = score + ? WHERE user_id = ?", (award, user_id)
                    )
                result = (status, award)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return result


def set_correct_class(photo_id, user_id, correct_class):
    """Сохраняет правильный класс для фото, отмеченного как «неверно». True при успехе."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE photos SET correct_class = ? WHERE id = ? AND user_id = ? AND feedback = 0",
            (correct_class, photo_id, user_id),
        )
        return cur.rowcount == 1
