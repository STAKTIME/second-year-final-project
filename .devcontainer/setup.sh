#!/bin/bash
set -e

echo "=== Создание venv ==="
python -m venv .venv
source .venv/bin/activate

echo "=== Обновление pip ==="
pip install --upgrade pip

echo "=== Установка зависимостей (PyTorch для CPU) ==="
pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

if [ ! -f config.py ]; then
    cp config.example.py config.py
    echo "Создан config.py — впишите в него BOT_TOKEN и GEMINI_API_KEY"
fi

echo "=== Готово ==="
echo "Активируй venv командой: source .venv/bin/activate, запуск: python main.py"
