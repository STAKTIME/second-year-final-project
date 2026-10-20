"""Эко-бот для Telegram: распознаёт мусор по фото, подсказывает, как его сдать,
начисляет эко-очки и отвечает на вопросы с помощью Gemini."""

import io
import logging
import os

import telebot
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from PIL import Image, UnidentifiedImageError
from telebot import types

import db
from classifier import CLASS_NAMES, DEFAULT_MODEL_PATH, GarbageClassifier
from utils import RateLimiter, esc, markdown_to_html, split_message, validate_display_name

__version__ = "1.6"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("eco_bot")


# ==== НАСТРОЙКА ====
def load_settings():
    """Берёт ключи из config.py (см. config.example.py), а если его нет — из переменных окружения."""
    try:
        import config
    except ImportError:
        config = None

    def get(name, default=None):
        value = getattr(config, name, None) if config else None
        return value or os.environ.get(name) or default

    settings = {
        "BOT_TOKEN": get("BOT_TOKEN"),
        "GEMINI_API_KEY": get("GEMINI_API_KEY"),
        "GEMINI_MODEL": get("GEMINI_MODEL", "gemini-2.5-flash"),
        "MODEL_PATH": get("MODEL_PATH", DEFAULT_MODEL_PATH),
    }
    if not settings["BOT_TOKEN"]:
        raise SystemExit(
            "Не задан BOT_TOKEN. Скопируйте config.example.py в config.py и впишите токен "
            "или задайте переменную окружения BOT_TOKEN."
        )
    return settings


SETTINGS = load_settings()

POINTS_PER_PHOTO = 10
DAILY_POINTS_LIMIT = 100
# Ниже этой уверенности бот не называет класс уверенно и не даёт очков
CONFIDENCE_THRESHOLD = 0.6

photo_limiter = RateLimiter(interval=3)
gemini_limiter = RateLimiter(interval=15, daily_limit=30)

CLASS_INFO = {
    "cardboard": ("📦 Картон", "Сдайте в макулатуру. Сложите коробку — так она займёт меньше места."),
    "glass":     ("🍾 Стекло", "Сдайте в контейнер для стекла. Крышки и пробки снимите."),
    "metal":     ("🥫 Металл", ("Банки ополосните от остатков еды и сомните — их принимают в контейнеры "
                              "для металла или смешанной переработки. Крупный металл — в пункт приёма металлолома.")),
    "paper":     ("📄 Бумага", "Сдайте в макулатуру. Уберите скотч, скрепки и пластиковые обложки."),
    "plastic":   ("🧴 Пластик", "Сдайте в контейнер для пластика. Сполосните и сомните бутылку."),
    "trash":     ("🗑 Прочий мусор", "К сожалению, не перерабатывается. Выбрасывайте в общий контейнер."),
}

# ==== ТЕКСТЫ КНОПОК ====
BTN_RECOGNIZE = "📸 Распознать мусор"
BTN_ASK_AI = "🤖 Спросить ИИ"
BTN_TOP = "📊 Топ"
BTN_PROFILE = "⚙️ Профиль"
BTN_RENAME = "✏️ Сменить имя в профиле"
BTN_ANON = "🕵️ Скрыть/Показать меня в топе"
BTN_BACK = "🔙 Назад в главное меню"
MENU_BUTTONS = [BTN_RECOGNIZE, BTN_ASK_AI, BTN_TOP, BTN_PROFILE, BTN_RENAME, BTN_ANON, BTN_BACK]

# Запасные модели Gemini, если основная перегружена
GEMINI_FALLBACK_MODELS = ["gemini-flash-lite-latest", "gemini-flash-latest"]

GEMINI_PHOTO_PROMPT = (
    "На фото мусор, который нейросеть-классификатор отнесла к классу '{cls}'. "
    "Если класс явно неверный, сначала коротко поправь. "
    "Дай креативный и практичный совет: как его можно переработать, "
    "сдать или сделать из него что-то полезное (upcycling). "
    "Ответ должен быть коротким (до 5 предложений) и на русском языке."
)
GEMINI_SYSTEM_PROMPT = (
    "Ты эко-помощник в Telegram-боте. Отвечай коротко (до 6 предложений), по-русски, "
    "только на вопросы о сортировке, переработке мусора и экологии. "
    "На вопросы на другие темы вежливо откажись."
)

bot = telebot.TeleBot(SETTINGS["BOT_TOKEN"])
classifier = None  # загружается в main()
gemini = (
    genai.Client(
        api_key=SETTINGS["GEMINI_API_KEY"],
        http_options=genai_types.HttpOptions(timeout=40_000, retry_options=genai_types.HttpRetryOptions(attempts=1)),
    )
    if SETTINGS["GEMINI_API_KEY"]
    else None
)


# ==== КЛАВИАТУРЫ ====
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(BTN_RECOGNIZE, BTN_ASK_AI, BTN_TOP, BTN_PROFILE)
    return markup


def get_profile_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(BTN_RENAME)
    markup.add(BTN_ANON)
    markup.add(BTN_BACK)
    return markup


def get_class_choice_menu(photo_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(*[
        types.InlineKeyboardButton(CLASS_INFO[c][0], callback_data=f"fix:{photo_id}:{c}")
        for c in CLASS_NAMES
    ])
    return markup


# ==== ОБЩЕЕ ====
def ensure_user(user):
    """Добавляет пользователя в базу (или обновляет ник) при любом обращении к боту."""
    db.add_user(user.id, user.username, user.first_name)


def display_name(disp_name, username, first_name=None):
    """Имя для топа и профиля: своё имя, иначе @ник, иначе имя из Telegram."""
    if disp_name:
        return esc(disp_name)
    if username:
        return esc(f"@{username}")
    return esc(first_name or "Аноним")


def send_long(chat_id, html_text, **kwargs):
    """Отправляет HTML-текст, разбивая его на части по лимиту Telegram."""
    for part in split_message(html_text, limit=4000):
        bot.send_message(chat_id, part, parse_mode="HTML", **kwargs)


def ask_gemini(contents, system_prompt=None):
    """Возвращает ответ Gemini или None, если ответа нет (ошибка, фильтр, пустой ответ)."""
    if gemini is None:
        return None
    config = genai_types.GenerateContentConfig(system_instruction=system_prompt) if system_prompt else None
    # Если модель перегружена (503/504/429), пробуем следующую из списка
    for model in [SETTINGS["GEMINI_MODEL"], *GEMINI_FALLBACK_MODELS]:
        try:
            response = gemini.models.generate_content(model=model, contents=contents, config=config)
            return (response.text or "").strip() or None
        except genai_errors.APIError as e:
            log.warning("Gemini %s: %s %s", model, e.code, e.status)
            if e.code not in (429, 500, 503, 504):
                return None
        except Exception:
            log.exception("Ошибка Gemini")
            return None
    return None


def redirect_if_other_action(message):
    """Если в режиме ожидания ввода пришла кнопка меню или фото — выполняет их. True, если перенаправили."""
    if message.content_type == "photo":
        handle_photo(message)
        return True
    if message.text in MENU_BUTTONS:
        handle_menu(message)
        return True
    if message.text and message.text.startswith("/"):
        send_welcome(message)
        return True
    return False


# ==== КОМАНДЫ ====
@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    bot.clear_step_handler_by_chat_id(message.chat.id)
    ensure_user(message.from_user)
    name = esc(message.from_user.first_name or "друг")
    bot.send_message(
        message.chat.id,
        f"👋 <b>Привет, {name}! Я твой эко-помощник.</b>\n\n"
        "Я помогаю правильно сортировать мусор, чтобы больше отходов попадало "
        "на переработку, а не на свалку. 🌍\n\n"
        "<b>Что я умею:</b>\n"
        "📸 <b>Распознаю мусор по фото</b> — картон, стекло, металл, бумагу, пластик "
        "и прочий мусор.\n"
        "♻️ <b>Подскажу, куда его сдать</b> и как подготовить: снять крышку, "
        "ополоснуть, сложить.\n"
        "🤖 <b>Спрошу ИИ</b> — нейросеть Gemini даст совет по конкретному предмету "
        "или ответит на твой вопрос, например «Куда сдать батарейки?».\n"
        "📊 <b>Топ эко-активистов</b> — соревнуйся с другими по эко-очкам.\n"
        "⚙️ <b>Профиль</b> — свои очки, имя в топе, можно скрыться из топа.\n\n"
        "<b>Как получить эко-очки:</b>\n"
        "1. Сфотографируй предмет крупно, целиком, лучше на однотонном фоне.\n"
        "2. Отправь фото мне.\n"
        f"3. Если я распознал верно, нажми «👍 Верно» — получишь <b>{POINTS_PER_PHOTO} очков</b>.\n"
        "4. Если ошибся, нажми «👎 Неверно» и выбери правильный вариант — "
        "так я научусь распознавать лучше.\n\n"
        f"<i>Очки дают один раз за каждое фото, не больше {DAILY_POINTS_LIMIT} в сутки.</i>\n\n"
        "Пользуйся кнопками меню внизу 👇 или просто отправь фото!",
        parse_mode="HTML",
        reply_markup=get_main_menu(),
    )


# ==== 1. ОБРАБОТКА ФОТО ====
@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    bot.clear_step_handler_by_chat_id(message.chat.id)
    ensure_user(message.from_user)

    wait = photo_limiter.check(message.from_user.id)
    if wait:
        bot.reply_to(message, f"⏳ Не так быстро! Подожди {wait:.0f} с и отправь фото снова.")
        return

    status_msg = bot.reply_to(message, "🔍 Анализирую фото...")
    photo = message.photo[-1]
    try:
        file_info = bot.get_file(photo.file_id)
        image = Image.open(io.BytesIO(bot.download_file(file_info.file_path)))
        result = classifier.predict(image)
    except UnidentifiedImageError:
        bot.edit_message_text("⚠️ Не получилось открыть это изображение. Попробуй другое фото.",
                              message.chat.id, status_msg.message_id)
        return
    except Exception:
        log.exception("Ошибка распознавания")
        bot.edit_message_text("⚠️ Не получилось распознать фото. Попробуй ещё раз чуть позже.",
                              message.chat.id, status_msg.message_id)
        return

    class_name = result["predicted_class"]
    confidence = result["confidence"]
    photo_id = db.add_photo(message.from_user.id, message.chat.id, photo.file_id,
                            photo.file_unique_id, class_name, confidence)
    title, _ = CLASS_INFO[class_name]

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("♻️ Что с мусором делать?", callback_data=f"what:{photo_id}"),
        types.InlineKeyboardButton("🤖 Спросить ИИ", callback_data=f"ai:{photo_id}"),
    )

    if confidence >= CONFIDENCE_THRESHOLD:
        markup.add(
            types.InlineKeyboardButton(f"👍 Верно (+{POINTS_PER_PHOTO} очков)", callback_data=f"ok:{photo_id}"),
            types.InlineKeyboardButton("👎 Неверно", callback_data=f"bad:{photo_id}"),
        )
        response = (
            f"<b>{title}</b>\n"
            f"Уверенность: <b>{confidence * 100:.1f}%</b>\n\n"
            "⚠️ <i>ИИ может ошибаться, проверяйте маркировку.</i>"
        )
    else:
        # Модель не уверена: показываем варианты и просим фото получше, очки не даём
        top = sorted(result["probabilities"].items(), key=lambda kv: kv[1], reverse=True)[:3]
        variants = "\n".join(f"• {CLASS_INFO[c][0]} — {p * 100:.0f}%" for c, p in top)
        markup.add(types.InlineKeyboardButton("👎 Неверно, указать класс", callback_data=f"bad:{photo_id}"))
        response = (
            "🤔 <b>Не уверен, что это.</b> Возможные варианты:\n"
            f"{variants}\n\n"
            "Сфотографируй предмет крупнее, целиком и на однотонном фоне."
        )

    bot.edit_message_text(response, message.chat.id, status_msg.message_id,
                          parse_mode="HTML", reply_markup=markup)


# ==== 2. ОБРАБОТКА ИНЛАЙН-КНОПОК (ПОД ФОТО) ====
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    action, _, arg = call.data.partition(":")

    if action == "none":
        bot.answer_callback_query(call.id)
        return

    photo_id, _, extra = arg.partition(":")
    photo = db.get_photo(int(photo_id)) if photo_id.isdigit() else None
    if photo is None:
        bot.answer_callback_query(call.id, "Это сообщение устарело. Отправь фото ещё раз.", show_alert=True)
        return

    # Что делать с мусором
    if action == "what":
        bot.answer_callback_query(call.id)
        _, advice = CLASS_INFO[photo["predicted_class"]]
        bot.send_message(chat_id, f"💡 <b>Совет:</b> {advice}", parse_mode="HTML")

    # Спросить ИИ (Gemini) про это фото
    elif action == "ai":
        if gemini is None:
            bot.answer_callback_query(call.id, "ИИ-помощник не настроен.", show_alert=True)
            return
        wait = gemini_limiter.check(user_id)
        if wait:
            bot.answer_callback_query(call.id, f"Слишком часто. Подожди {wait:.0f} с.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "Отправляю фото в Gemini...")
        bot.send_chat_action(chat_id, "typing")
        try:
            file_info = bot.get_file(photo["file_id"])
            image = Image.open(io.BytesIO(bot.download_file(file_info.file_path)))
        except Exception:
            log.exception("Не удалось скачать фото %s", photo["id"])
            bot.send_message(chat_id, "⚠️ Фото больше недоступно. Отправь его ещё раз.")
            return
        answer = ask_gemini([GEMINI_PHOTO_PROMPT.format(cls=photo["predicted_class"]), image])
        if answer is None:
            bot.send_message(chat_id, "⚠️ ИИ сейчас не смог ответить. Попробуй позже.")
            return
        send_long(chat_id, f"🤖 <b>Gemini говорит:</b>\n\n{markdown_to_html(answer)}")

    # Кнопка "Верно": очки за подтверждённое распознавание
    elif action == "ok":
        status, points = db.rate_photo(photo["id"], user_id, True, POINTS_PER_PHOTO, DAILY_POINTS_LIMIT)
        if status == "foreign":
            bot.answer_callback_query(call.id, "Оценить можно только своё фото.", show_alert=True)
            return
        if status == "already":
            bot.answer_callback_query(call.id, "Ты уже оценил это фото.")
        elif status == "duplicate":
            bot.answer_callback_query(call.id, "Спасибо! За это фото очки уже начислялись.", show_alert=True)
        elif status == "limit":
            bot.answer_callback_query(call.id, f"Спасибо! Дневной лимит {DAILY_POINTS_LIMIT} очков исчерпан.", show_alert=True)
        else:
            bot.answer_callback_query(call.id, f"Спасибо! Тебе начислено {points} очков! 🌿")
        label = f"✅ +{points} очков" if points else "✅ Отзыв учтён"
        replace_rating_buttons(call, label)

    # Кнопка "Неверно": запоминаем отзыв и спрашиваем правильный класс
    elif action == "bad":
        status, _ = db.rate_photo(photo["id"], user_id, False, 0, DAILY_POINTS_LIMIT)
        if status == "foreign":
            bot.answer_callback_query(call.id, "Оценить можно только своё фото.", show_alert=True)
            return
        if status == "already":
            bot.answer_callback_query(call.id, "Ты уже оценил это фото.")
            return
        bot.answer_callback_query(call.id)
        replace_rating_buttons(call, "📝 Отмечено как неверное")
        bot.send_message(chat_id, "Спасибо! А что это на самом деле?",
                         reply_markup=get_class_choice_menu(photo["id"]))

    # Выбор правильного класса после "Неверно"
    elif action == "fix" and extra in CLASS_INFO:
        if db.set_correct_class(photo["id"], user_id, extra):
            bot.answer_callback_query(call.id, "Записал! Это поможет улучшить распознавание.")
            _, advice = CLASS_INFO[extra]
            try:
                bot.edit_message_text(
                    f"Записал: <b>{CLASS_INFO[extra][0]}</b>\n💡 <b>Совет:</b> {advice}",
                    chat_id, call.message.message_id, parse_mode="HTML",
                )
            except telebot.apihelper.ApiTelegramException:
                log.warning("Не удалось изменить сообщение %s", call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "Указать класс может только автор фото.", show_alert=True)

    else:
        bot.answer_callback_query(call.id)


def replace_rating_buttons(call, label):
    """Оставляет под результатом «Что делать» и «Спросить ИИ», а кнопки оценки заменяет надписью."""
    markup = types.InlineKeyboardMarkup()
    # У старых сообщений (InaccessibleMessage) нет reply_markup
    old_markup = getattr(call.message, "reply_markup", None)
    rows = old_markup.keyboard if old_markup else []
    if rows:
        markup.row(*rows[0])
    markup.add(types.InlineKeyboardButton(label, callback_data="none"))
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
    except telebot.apihelper.ApiTelegramException:
        # Telegram не даёт менять старые сообщения — очки при этом уже учтены в базе
        log.warning("Не удалось изменить кнопки сообщения %s", call.message.message_id)


# ==== 3. ОБРАБОТКА КНОПОК МЕНЮ ====
@bot.message_handler(func=lambda msg: msg.text in MENU_BUTTONS)
def handle_menu(message):
    text = message.text
    user_id = message.from_user.id
    ensure_user(message.from_user)

    # Сбрасываем ожидание ввода, если пользователь нажал на другую кнопку
    bot.clear_step_handler_by_chat_id(message.chat.id)

    if text == BTN_RECOGNIZE:
        bot.reply_to(message, "Просто отправь мне фотографию мусора!")

    elif text == BTN_ASK_AI:
        if gemini is None:
            bot.reply_to(message, "ИИ-помощник сейчас не настроен.")
            return
        msg = bot.reply_to(
            message,
            "Напиши свой вопрос о сортировке или переработке мусора "
            "(например: «Куда сдать батарейки?»).\n"
            "А чтобы ИИ посмотрел на конкретный предмет — отправь фото и нажми под ним «🤖 Спросить ИИ».",
        )
        bot.register_next_step_handler(msg, process_ai_question)

    elif text == BTN_TOP:
        top_users = db.get_top_users()
        if not top_users:
            bot.reply_to(message, "Пока в топе никого нет. Стань первым!", reply_markup=get_main_menu())
            return
        top_text = "🏆 <b>Топ эко-активистов:</b>\n\n"
        for i, (disp_name, username, first_name, score) in enumerate(top_users, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "👤"
            top_text += f"{medal} {i}. {display_name(disp_name, username, first_name)} — {score} очков\n"
        bot.reply_to(message, top_text, parse_mode="HTML", reply_markup=get_main_menu())

    elif text == BTN_PROFILE:
        username, disp_name, first_name, score, is_anon = db.get_user_profile(user_id)
        status = "Скрыт из топа 🕵️" if is_anon else "Виден в топе 👁"
        profile_text = (
            "👤 <b>Твой профиль</b>\n\n"
            f"Имя: {display_name(disp_name, username, first_name)}\n"
            f"Эко-очки: <b>{score}</b>\n"
            f"Статус: {status}"
        )
        bot.reply_to(message, profile_text, parse_mode="HTML", reply_markup=get_profile_menu())

    elif text == BTN_BACK:
        bot.reply_to(message, "Главное меню:", reply_markup=get_main_menu())

    elif text == BTN_ANON:
        if db.toggle_anonymity(user_id):
            bot.reply_to(message, "✅ Готово! Теперь ты не отображаешься в топе.", reply_markup=get_profile_menu())
        else:
            bot.reply_to(message, "✅ Готово! Теперь ты снова виден в топе.", reply_markup=get_profile_menu())

    elif text == BTN_RENAME:
        ask_new_name(message, "Напиши, как тебя показывать в топе (например, «Иван» или «Эко-Воин»).\n"
                              "Или нажми «Назад», чтобы отменить.")


# ==== 4. ВВОД НОВОГО ИМЕНИ ====
def ask_new_name(message, prompt):
    msg = bot.reply_to(message, prompt, reply_markup=get_profile_menu())
    bot.register_next_step_handler(msg, process_new_name)


def process_new_name(message):
    # Если пользователь передумал и нажал кнопку меню или отправил фото — выполняем это
    if redirect_if_other_action(message):
        return

    new_name, error = validate_display_name(message.text)
    if error:
        ask_new_name(message, f"{error} Попробуй ещё раз или нажми «Назад».")
        return

    if not db.set_display_name(message.from_user.id, new_name):
        ask_new_name(message, "Это имя уже занято. Придумай другое или нажми «Назад».")
        return

    bot.reply_to(message, f"✅ Отлично! Теперь в топе ты будешь как: <b>{esc(new_name)}</b>",
                 parse_mode="HTML", reply_markup=get_profile_menu())


# ==== 5. ВОПРОС ИИ ТЕКСТОМ ====
def process_ai_question(message):
    if redirect_if_other_action(message):
        return
    if not message.text:
        bot.reply_to(message, "Напиши вопрос текстом.", reply_markup=get_main_menu())
        return

    wait = gemini_limiter.check(message.from_user.id)
    if wait:
        bot.reply_to(message, f"⏳ Слишком часто. Подожди {wait:.0f} с.", reply_markup=get_main_menu())
        return

    bot.send_chat_action(message.chat.id, "typing")
    answer = ask_gemini(message.text[:1000], system_prompt=GEMINI_SYSTEM_PROMPT)
    if answer is None:
        bot.reply_to(message, "⚠️ ИИ сейчас не смог ответить. Попробуй позже.", reply_markup=get_main_menu())
        return
    send_long(message.chat.id, f"🤖 <b>Gemini говорит:</b>\n\n{markdown_to_html(answer)}",
              reply_markup=get_main_menu())


# ==== 6. ЛОВУШКА ДЛЯ ВСЕГО ОСТАЛЬНОГО ====
# ВАЖНО: Этот обработчик должен быть САМЫМ ПОСЛЕДНИМ в файле!
@bot.message_handler(content_types=["text", "document", "sticker", "video", "voice", "audio"])
def echo_all(message):
    if message.content_type == "document" and (message.document.mime_type or "").startswith("image/"):
        bot.reply_to(message, "Отправь картинку как фото (не файлом), чтобы я её распознал.")
        return
    bot.reply_to(message, "Я тебя не понял. Пользуйся кнопками в меню ниже 👇", reply_markup=get_main_menu())


def main():
    global classifier
    log.info("Эко-бот %s. Загружаю модель...", __version__)
    classifier = GarbageClassifier(SETTINGS["MODEL_PATH"])
    log.info("Модель готова. Устройство: %s", classifier.device)

    db.init_db()
    if gemini is None:
        log.warning("GEMINI_API_KEY не задан — ИИ-помощник отключён")

    log.info("Бот запущен. Ctrl+C для остановки.")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    main()
