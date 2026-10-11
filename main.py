# мне на заметку
# 1. После каждой строки домавить коментарии от себя чтобы легче защищать проект 
# 2. Добавить статистику об праильности ответов и чтото с ней делать и дабавку что иии может ошибаться
# 3. Инлайн кнопки 
# 4. Привязать апи гемени либо другой хорошей ии чтобы подсказать что делать с несовсем мусором 
# 5. Добавить чтобы пользователь говорил город и в зависимости от него говорить что можно делать с мусором, 
#собирать статистику пользователей городов 
# 6. Надо интерфейс бота распаисать на бумаге 
# 7. админ панель и возможность чтото делать в терминале  

import os
import tempfile

import telebot

from source.predict import load_model_for_inference, predict_image

# ==== НАСТРОЙКА ====
BOT_TOKEN = '8678674003:AAGCQ-RU7VjpO2JQWAVP9v1f6yXyvcr2VUc'

# Советы по утилизации по каждому классу
CLASS_INFO = {
    "cardboard": (
        "📦 Картон",
        "Сдайте в макулатуру. Сложите коробку — так она займёт меньше места."
    ),
    "glass": (
        "🍾 Стекло",
        "Сдайте в контейнер для стекла. Крышки и пробки снимите."
    ),
    "metal": (
        "🥫 Металл",
        "Сдайте в пункт приёма металлолома. Банки мыть необязательно."
    ),
    "paper": (
        "📄 Бумага",
        "Сдайте в макулатуру. Уберите скотч, скрепки и пластиковые обложки."
    ),
    "plastic": (
        "🧴 Пластик",
        "Сдайте в контейнер для пластика. Сполосните и сомните бутылку."
    ),
    "trash": (
        "🗑 Прочий мусор",
        "К сожалению, не перерабатывается. Выбрасывайте в общий контейнер."
    ),
}

# ==== ЗАГРУЖАЕМ МОДЕЛЬ ОДИН РАЗ ПРИ СТАРТЕ ====
print("⏳ Загружаю модель...")
MODEL, DEVICE, TRANSFORM = load_model_for_inference()
print(f"✅ Модель готова. Устройство: {DEVICE}")

# ==== БОТ ====
bot = telebot.TeleBot(BOT_TOKEN)


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    bot.reply_to(
        message,
        "👋 Привет! Я бот-эколог.\n\n"
        "📸 Пришли мне фото мусора — я скажу, что это за материал "
        "и как его правильно утилизировать.\n\n"
        "Классы: картон, стекло, металл, бумага, пластик, прочий мусор."
    )


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    status_msg = bot.reply_to(message, "🔍 Анализирую...")

    # Скачиваем фото в максимальном доступном качестве
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded = bot.download_file(file_info.file_path)

    # Сохраняем во временный файл (predict_image принимает путь)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(downloaded)
        temp_path = f.name

    try:
        result = predict_image(
            temp_path,
            model=MODEL,
            transform=TRANSFORM,
            device=DEVICE,
            track_carbon=False,
        )

        class_name = result["predicted_class"]
        confidence = result["confidence"] * 100
        probs = result["probabilities"]

        title, advice = CLASS_INFO.get(
            class_name, (class_name, "Информация о классе отсутствует.")
        )

        # Топ-3 альтернативы
        top3 = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:3]
        top3_lines = "\n".join(
            f"   • {name}: {p*100:.1f}%" for name, p in top3
        )

        response = (
            f"*{title}*\n"
            f"Уверенность: *{confidence:.1f}%*\n\n"
            f"💡 {advice}\n\n"
            f"_Топ-3 варианта:_\n{top3_lines}"
        )

        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text=response,
            parse_mode="Markdown",
        )
    except Exception as e:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text=f"⚠️ Ошибка при распознавании: `{e}`",
            parse_mode="Markdown",
        )
    finally:
        os.unlink(temp_path)


@bot.message_handler(func=lambda m: True)
def fallback(message):
    bot.reply_to(message, "📸 Пришли мне фотографию мусора, и я её распознаю.")


if __name__ == "__main__":
    print("🤖 Бот запущен. Нажми Ctrl+C для остановки.")
    bot.infinity_polling()