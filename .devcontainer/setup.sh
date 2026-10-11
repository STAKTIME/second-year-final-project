#!/bin/bash
set -e

echo "=== Установка системных библиотек ==="
sudo apt-get update
sudo apt-get install -y libgl1 libglib2.0-0

echo "=== Создание venv ==="
python -m venv .venv
source .venv/bin/activate

echo "=== Обновление pip ==="
pip install --upgrade pip

echo "=== Установка зависимостей ==="
if [ -f requirements.txt ]; then
    pip install -r requirements.txt
fi

echo "=== Готово ==="
echo "Активируй venv командой: source .venv/bin/activate"
