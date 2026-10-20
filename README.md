# Эко-бот — выпускной проект 2 курса

Telegram-бот, который помогает правильно сортировать мусор:

- 📸 **распознаёт мусор по фото** — 6 классов: картон, стекло, металл, бумага, пластик, прочий мусор;
- ♻️ **подсказывает, куда его сдать**;
- 🤖 **отвечает на вопросы с помощью Gemini** — по фото («что из этого можно сделать?») и текстом («куда сдать батарейки?»);
- 🏆 **начисляет эко-очки** за подтверждённые распознавания и ведёт **топ** участников;
- ⚙️ **профиль**: своё имя в топе, можно скрыться из топа;
- 👎 собирает отзывы «неверно» с правильным классом — данные для дообучения модели.

Версия: **2.4**

## Как это устроено

```
Фото из Telegram ──► classifier.py (ResNet18, PyTorch) ──► класс + уверенность
                                                          │
            ┌─────────────────────────────────────────────┘
            ▼
main.py (pyTelegramBotAPI) ──► db.py (SQLite: пользователи, фото, очки, отзывы)
            │
            └──► Gemini API (google-genai) — советы по фото и ответы на вопросы
```

| Файл | Что внутри |
|---|---|
| `main.py` | обработчики Telegram: меню, фото, инлайн-кнопки, профиль, вопросы ИИ |
| `classifier.py` | загрузка модели (скачивается автоматически при первом запуске) и предсказание |
| `db.py` | база SQLite с миграциями схемы (`PRAGMA user_version`) |
| `utils.py` | экранирование HTML, разбиение длинных сообщений, проверка имени, ограничение частоты |
| `tests/` | тесты базы и вспомогательных функций (`python -m unittest`) |
| `deploy/eco-bot.service` | запуск как службы systemd на Linux / Raspberry Pi |

### Правила начисления очков

- +10 очков за фото, если пользователь подтвердил, что бот распознал верно;
- только автору фото и только один раз за фото;
- за повторно отправленное то же самое фото очки не начисляются;
- не больше 100 очков в сутки;
- если модель не уверена (меньше 60%), бот показывает варианты и просит фото получше — очков за такое фото нет.

Ограничения частоты: фото — не чаще раза в 3 с, запросы к Gemini — раз в 15 с и не больше 30 в сутки на пользователя.

## Установка и запуск

Нужен Python 3.10+.

```bash
git clone https://github.com/STAKTIME/second-year-final-project.git
cd second-year-final-project
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
```

`--extra-index-url ...cpu` ставит PyTorch без CUDA (~200 МБ вместо нескольких ГБ). Если есть видеокарта NVIDIA, эту часть можно убрать.

Настройка ключей:

```bash
cp config.example.py config.py      # Windows: copy config.example.py config.py
```

Впишите в `config.py`:

- `BOT_TOKEN` — токен бота от [@BotFather](https://t.me/BotFather);
- `GEMINI_API_KEY` — ключ [Gemini API](https://aistudio.google.com/apikey) (если оставить пустым, бот работает без ИИ-помощника).

Вместо `config.py` можно задать переменные окружения с теми же именами.

Запуск:

```bash
python main.py
```

При первом запуске модель (~130 МБ) скачается в `models/best/`.

### Devcontainer / Codespaces

В `.devcontainer/` есть настройки для VS Code Dev Containers и GitHub Codespaces: окружение и зависимости ставятся автоматически.

## Разработка

```bash
python -m unittest discover -s tests -v   # тесты
pip install ruff && ruff check .          # линтер
```

Тесты и линтер автоматически запускаются на GitHub Actions при каждом push (`.github/workflows/ci.yml`).

## Авторство модели

Модель классификации (ResNet18, дообученная на 6 классах) и её веса взяты из проекта
[NeoLafuente/garbage_classifier](https://github.com/NeoLafuente/garbage_classifier)
(лицензия MIT, © 2025 Melchor Lafuente & Iker Jauregui) — см. [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Сам бот, база данных, система очков и интеграция с Gemini написаны в рамках этого проекта.
