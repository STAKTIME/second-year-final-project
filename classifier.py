"""Классификатор мусора по фото (ResNet18, 6 классов).

Модель и веса взяты из проекта NeoLafuente/garbage_classifier (лицензия MIT),
см. THIRD_PARTY_NOTICES.md. Здесь чекпоинт загружается напрямую через
torch/torchvision, без пакета garbage_classifier и его тяжёлых зависимостей
(TensorFlow, Gradio, CUDA).
"""

import logging
import threading
import urllib.request
from pathlib import Path

import torch
from PIL import Image
from torch import nn
from torchvision import models

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
MODEL_FILENAME = "model_resnet18_garbage.ckpt"
DEFAULT_MODEL_PATH = BASE_DIR / "models" / "best" / MODEL_FILENAME
MODEL_URL = (
    "https://github.com/NeoLafuente/garbage_classifier/"
    f"releases/download/v0.1.0/{MODEL_FILENAME}"
)

# Порядок классов совпадает с порядком выходов модели
CLASS_NAMES = ["cardboard", "glass", "metal", "paper", "plastic", "trash"]


def ensure_model(path: Path = DEFAULT_MODEL_PATH) -> Path:
    """Скачивает чекпоинт, если его ещё нет."""
    path = Path(path)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    log.info("Скачиваю модель %s -> %s", MODEL_URL, path)
    urllib.request.urlretrieve(MODEL_URL, tmp)
    tmp.replace(path)
    return path


class GarbageClassifier:
    def __init__(self, model_path: Path = DEFAULT_MODEL_PATH, device: str | None = None):
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))

        checkpoint = torch.load(ensure_model(model_path), map_location="cpu", weights_only=False)
        # Ключи в чекпоинте Lightning имеют вид "model.conv1.weight"
        state = {k.removeprefix("model."): v for k, v in checkpoint["state_dict"].items()}

        net = models.resnet18(weights=None)
        net.fc = nn.Linear(net.fc.in_features, len(CLASS_NAMES))
        net.load_state_dict(state)
        self.model = net.to(self.device).eval()
        self.transform = models.ResNet18_Weights.IMAGENET1K_V1.transforms()
        # Бот обрабатывает сообщения в нескольких потоках
        self._lock = threading.Lock()

    def predict(self, image: Image.Image) -> dict:
        """Возвращает {"predicted_class", "confidence", "probabilities"}."""
        tensor = self.transform(image.convert("RGB")).unsqueeze(0).to(self.device)
        with self._lock, torch.no_grad():
            probs = torch.softmax(self.model(tensor), dim=1)[0].cpu()
        idx = int(probs.argmax())
        return {
            "predicted_class": CLASS_NAMES[idx],
            "confidence": float(probs[idx]),
            "probabilities": {name: float(p) for name, p in zip(CLASS_NAMES, probs)},
        }
