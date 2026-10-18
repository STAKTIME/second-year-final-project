import sqlite3

DB_NAME = "eco_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Добавляем новые колонки display_name и is_anonymous
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            display_name TEXT,
            is_anonymous BOOLEAN DEFAULT 0,
            score INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def add_user(user_id, username):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()
    conn.close()

def add_points(user_id, points):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET score = score + ? WHERE user_id = ?", (points, user_id))
    conn.commit()
    conn.close()

def get_top_users(limit=5):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Берем только тех, кто НЕ анонимный
    cursor.execute("SELECT display_name, username, score FROM users WHERE is_anonymous = 0 ORDER BY score DESC LIMIT ?", (limit,))
    top = cursor.fetchall()
    conn.close()
    return top

def get_user_profile(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Достаем все данные, включая новые
    cursor.execute("SELECT username, display_name, score, is_anonymous FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

# Новые функции для изменения профиля
def set_display_name(user_id, new_name):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET display_name = ? WHERE user_id = ?", (new_name, user_id))
    conn.commit()
    conn.close()

def toggle_anonymity(user_id, is_anon):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_anonymous = ? WHERE user_id = ?", (is_anon, user_id))
    conn.commit()
    conn.close()