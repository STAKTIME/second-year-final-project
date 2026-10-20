"""Вспомогательные функции без зависимостей от Telegram и модели (их проверяют тесты)."""

import html
import re
import threading
import time
import unicodedata

TELEGRAM_LIMIT = 4096
NAME_MAX_LEN = 30


def esc(text) -> str:
    """Экранирует пользовательский текст для parse_mode="HTML"."""
    return html.escape(str(text), quote=False)


def markdown_to_html(text: str) -> str:
    """Переводит простой Markdown из ответа Gemini (**жирный**, списки) в безопасный HTML."""
    text = esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?m)^\s*[*-]\s+", "• ", text)
    text = re.sub(r"(?m)^#+\s*(.+)$", r"<b>\1</b>", text)
    return text


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Режет длинный текст на части не длиннее limit, по возможности по абзацам и строкам."""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


def validate_display_name(text) -> tuple[str | None, str | None]:
    """Проверяет новое имя для топа. Возвращает (имя, None) или (None, текст ошибки)."""
    if not isinstance(text, str):
        return None, "Пришли имя обычным текстом."
    # Убираем управляющие и невидимые символы, схлопываем пробелы
    cleaned = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return None, "Имя не может быть пустым."
    if len(cleaned) > NAME_MAX_LEN:
        return None, f"Слишком длинное имя (максимум {NAME_MAX_LEN} символов)."
    if cleaned.startswith("@"):
        return None, "Имя не должно начинаться с @, чтобы не путать его с чужими никами."
    return cleaned, None


class RateLimiter:
    """Не чаще одного действия в interval секунд и не больше daily_limit действий в сутки на пользователя."""

    def __init__(self, interval: float, daily_limit: int | None = None, clock=time.monotonic):
        self.interval = interval
        self.daily_limit = daily_limit
        self.clock = clock
        self._last = {}
        self._history = {}
        self._lock = threading.Lock()

    def check(self, user_id) -> float:
        """Возвращает 0, если действие разрешено (и засчитывает его), иначе сколько секунд ждать."""
        with self._lock:
            now = self.clock()
            wait = self._last.get(user_id, -self.interval) + self.interval - now
            if wait > 0:
                return wait
            if self.daily_limit is not None:
                history = [t for t in self._history.get(user_id, []) if now - t < 86400]
                if len(history) >= self.daily_limit:
                    self._history[user_id] = history
                    return 86400 - (now - history[0])
                history.append(now)
                self._history[user_id] = history
            self._last[user_id] = now
            return 0
