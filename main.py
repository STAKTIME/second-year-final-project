import os
import tempfile
import telebot
from telebot import types
import google.generativeai as genai
from PIL import Image

# Импортируем наши секретные ключи и базу данных
import config
import db

from source.predict import load_model_for_inference, predict_image

# ==== НАСТРОЙКА ====
BOT_TOKEN = config.BOT_TOKEN
GEMINI_API_KEY = config.GEMINI_API_KEY

# Настройка Gemini
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel('gemini-1.5-flash')

CLASS_INFO = {
    "cardboard": ("📦 Картон", "Сдайте в макулатуру. Сложите коробку — так она займёт меньше места."),
    "glass":     ("🍾 Стекло", "Сдайте в контейнер для стекла. Крышки и пробки снимите."),
    "metal":     ("🥫 Металл", "Сдайте в пункт приёма металлолома. Банки мыть необязательно."),
    "paper":     ("📄 Бумага", "Сдайте в макулатуру. Уберите скотч, скрепки и пластиковые обложки."),
    "plastic":   ("🧴 Пластик", "Сдайте в контейнер для пластика. Сполосните и сомните бутылку."),
    "trash":     ("🗑 Прочий мусор", "К сожалению, не перерабатывается. Выбрасывайте в общий контейнер."),
}

# Загружаем модель (один раз при старте)
print("⏳ Загружаю модель...")
MODEL, DEVICE, TRANSFORM = load_model_for_inference()
print(f"✅ Модель готова. Устройство: {DEVICE}")

# Инициализируем БД
db.init_db()

bot = telebot.TeleBot(BOT_TOKEN)
# Кэш для хранения путей к фото (чтобы Gemini мог их потом прочитать)
user_photo_cache = {}

# ==== КЛАВИАТУРЫ ====
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("📸 Распознать мусор", "🤖 Спросить ИИ", "📊 Топ", "⚙️ Профиль")
    return markup

def get_profile_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add("✏️ Сменить имя в профиле")
    markup.add("🕵️ Скрыть/Показать меня в топе")
    markup.add("🔙 Назад в главное меню")
    return markup

# ==== КОМАНДЫ ====
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    username = message.from_user.username or message.from_user.first_name or "Аноним"
    db.add_user(message.from_user.id, username)
    
    bot.send_message(
        message.chat.id,
        "👋 Привет! Я твой эко-помощник.\n\n"
        "Отправь мне фото мусора, и я скажу, что это. За правильные ответы даю очки!",
        reply_markup=get_main_menu()
    )

# ==== 1. ОБРАБОТКА ФОТО ====
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    status_msg = bot.reply_to(message, "🔍 Анализирую фото...")

    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded = bot.download_file(file_info.file_path)

        # Сохраняем во временный файл
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(downloaded)
            temp_path = f.name

        # Предсказание
        result = predict_image(temp_path, model=MODEL, transform=TRANSFORM, device=DEVICE)
        class_name = result["predicted_class"]
        confidence = result["confidence"] * 100

        title, _ = CLASS_INFO.get(class_name, (class_name, ""))
        
        # Инлайн-кнопки под результатом
        markup = types.InlineKeyboardMarkup()
        btn_what = types.InlineKeyboardButton("♻️ Что с мусором делать?", callback_data=f"what_{class_name}")
        btn_ai = types.InlineKeyboardButton("🤖 Спросить ИИ", callback_data=f"ai_{class_name}")
        btn_up = types.InlineKeyboardButton("👍 Верно (+10 очков)", callback_data=f"fb_up_{class_name}")
        btn_down = types.InlineKeyboardButton("👎 Неверно", callback_data="fb_down")
        
        markup.add(btn_what, btn_ai)
        markup.add(btn_up, btn_down)

        response = (
            f"*{title}*\n"
            f"Уверенность: *{confidence:.1f}%*\n\n"
            f"⚠️ _ИИ может ошибаться, проверяйте маркировку._"
        )

        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text=response,
            parse_mode="Markdown",
            reply_markup=markup
        )
        
        # Сохраняем путь к фото для Gemini
        user_photo_cache[message.chat.id] = temp_path

    except Exception as e:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text=f"⚠️ Ошибка при анализе: `{e}`",
            parse_mode="Markdown"
        )

# ==== 2. ОБРАБОТКА ИНЛАЙН-КНОПОК (ПОД ФОТО) ====
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    
    # Что делать с мусором
    if call.data.startswith("what_"):
        class_name = call.data.replace("what_", "")
        _, advice = CLASS_INFO.get(class_name, ("", "Совет не найден."))
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, f"💡 *Совет:* {advice}", parse_mode="Markdown")
        
    # Спросить ИИ (Gemini)
    elif call.data.startswith("ai_"):
        class_name = call.data.replace("ai_", "")
        bot.answer_callback_query(call.id, "Отправляю фото в Gemini...")
        
        photo_path = user_photo_cache.get(chat_id)
        if not photo_path or not os.path.exists(photo_path):
            bot.send_message(chat_id, "⚠️ Фото не найдено. Отправьте его еще раз.")
            return

        try:
            img = Image.open(photo_path)
            prompt = (
                f"На фото мусор, который классифицировали как '{class_name}'. "
                "Дай креативный и практичный совет: как его можно переработать, "
                "сдать или сделать из него что-то полезное (upcycling). "
                "Ответ должен быть коротким (до 5 предложений) и на русском языке."
            )
            response = gemini_model.generate_content([prompt, img])
            # Используем HTML, чтобы избежать ошибок разметки от нейросети
            bot.send_message(chat_id, f"🤖 <b>Gemini говорит:</b>\n\n{response.text}", parse_mode="HTML")
            
            # Удаляем фото из кэша после использования
            try:
                os.unlink(photo_path)
            except:
                pass
            user_photo_cache.pop(chat_id, None)
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ Ошибка Gemini: {e}")

    # Кнопка "Верно" (+10 очков)
    elif call.data.startswith("fb_up_"):
        bot.answer_callback_query(call.id, "Спасибо! Вам начислено 10 очков! 🌿")
        db.add_points(user_id, 10)
        
        # Убираем кнопки, чтобы нельзя было нажать дважды
        new_markup = types.InlineKeyboardMarkup()
        new_markup.add(types.InlineKeyboardButton("✅ Очки получены", callback_data="none"))
        try:
            bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=new_markup)
        except:
            pass # Если сообщение слишком старое, Telegram не даст изменить кнопки

    # Кнопка "Неверно"
    elif call.data.startswith("fb_down"):
        bot.answer_callback_query(call.id, "Спасибо за feedback! Мы обучаем модель дальше.")

# ==== 3. ОБРАБОТКА КНОПОК МЕНЮ ====
@bot.message_handler(func=lambda msg: msg.text in [
    "📸 Распознать мусор", "🤖 Спросить ИИ", "📊 Топ", "⚙️ Профиль",
    "🔙 Назад в главное меню", "✏️ Сменить имя в профиле", "🕵️ Скрыть/Показать меня в топе"
])
def handle_menu(message):
    text = message.text
    user_id = message.from_user.id
    
    # Сбрасываем ожидание ввода имени, если пользователь нажал на другую кнопку
    bot.clear_step_handler_by_chat_id(message.chat.id)

    if text == "📸 Распознать мусор":
        bot.reply_to(message, "Просто отправь мне фотографию мусора!")
        
    elif text == "🤖 Спросить ИИ":
        bot.reply_to(message, "Отправь фото мусора, и я спрошу у нейросети, что с ним делать!")
        
    elif text == "📊 Топ":
        top_users = db.get_top_users()
        if not top_users:
            bot.reply_to(message, "Пока в топе никого нет. Стань первым!", reply_markup=get_main_menu())
            return
        top_text = "🏆 *Топ эко-активистов:*\n\n"
        for i, (disp_name, username, score) in enumerate(top_users, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "👤"
            name_to_show = disp_name if disp_name else f"@{username}"
            top_text += f"{medal} {i}. {name_to_show} — {score} очков\n"
        bot.reply_to(message, top_text, parse_mode="Markdown", reply_markup=get_main_menu())
        
    elif text == "⚙️ Профиль":
        user_data = db.get_user_profile(user_id)
        if user_data:
            username, display_name, score, is_anon = user_data
            name_to_show = display_name if display_name else f"@{username}"
            status = "Скрыт из топа 🕵️" if is_anon else "Виден в топе 👁"
            
            profile_text = (
                f"👤 *Твой профиль*\n\n"
                f"Имя: {name_to_show}\n"
                f"Эко-очки: *{score}*\n"
                f"Статус: {status}"
            )
            bot.reply_to(message, profile_text, parse_mode="Markdown", reply_markup=get_profile_menu())
            
    elif text == "🔙 Назад в главное меню":
        bot.reply_to(message, "Главное меню:", reply_markup=get_main_menu())
        
    elif text == "🕵️ Скрыть/Показать меня в топе":
        user_data = db.get_user_profile(user_id)
        if user_data:
            current_anon = user_data[3]
            new_anon = not current_anon
            db.toggle_anonymity(user_id, new_anon)
            
            if new_anon:
                bot.reply_to(message, "✅ Готово! Теперь ты не отображаешься в топе.", reply_markup=get_profile_menu())
            else:
                bot.reply_to(message, "✅ Готово! Теперь ты снова виден в топе.", reply_markup=get_profile_menu())

    elif text == "✏️ Сменить имя в профиле":
        msg = bot.reply_to(message, "Напиши мне, как тебя показывать в топе (например, 'Иван' или 'Эко-Воин').\nИли нажми 'Назад', чтобы отменить.")
        bot.register_next_step_handler(msg, process_new_name)

# ==== 4. ОБРАБОТКА ВВОДА НОВОГО ИМЕНИ ====
def process_new_name(message):
    # Если пользователь передумал и нажал кнопку меню, просто выходим
    if message.text in ["📸 Распознать мусор", "🤖 Спросить ИИ", "📊 Топ", "⚙️ Профиль", "🔙 Назад в главное меню"]:
        handle_menu(message)
        return

    new_name = message.text.strip()
    if len(new_name) > 30:
        bot.reply_to(message, "Слишком длинное имя (макс 30 символов). Попробуй еще раз.", reply_markup=get_profile_menu())
        return
        
    db.set_display_name(message.from_user.id, new_name)
    bot.reply_to(message, f"✅ Отлично! Теперь в топе ты будешь как: *{new_name}*", parse_mode="Markdown", reply_markup=get_profile_menu())

# ==== 5. ЛОВУШКА ДЛЯ ВСЕГО ОСТАЛЬНОГО ТЕКСТА ====
# ВАЖНО: Этот обработчик должен быть САМЫМ ПОСЛЕДНИМ в файле!
# И он ловит только текст, чтобы не мешать фото.
@bot.message_handler(content_types=['text'])
def echo_all(message):
    bot.reply_to(message, "Я тебя не понял. Пользуйся кнопками в меню ниже 👇", reply_markup=get_main_menu())

if __name__ == "__main__":
    print("🤖 Бот запущен. Ctrl+C для остановки.")
    bot.infinity_polling()