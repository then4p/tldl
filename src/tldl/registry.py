"""Plugin lookup.

Built-in plugins are referenced by short name and imported lazily, so their
dependencies are only needed when actually configured. Any other ``type`` is
treated as an import path ``package.module:ClassName``, which is how you plug
in your own messenger or engine without touching this package.
"""

import importlib
from typing import Any

ENGINES = {
    "onnx": "tldl.engines.onnx:OnnxEngine",
    "openai": "tldl.engines.openai:OpenAIEngine",
}
DEFAULT_ENGINE_TYPE = "onnx"

MESSENGERS = {
    "telegram": "tldl.messengers.telegram:TelegramMessenger",
    "signal": "tldl.messengers.signal:SignalMessenger",
    "whatsapp-waha": "tldl.messengers.whatsapp_waha:WahaMessenger",
}


def load_class(kind: str, builtins: dict[str, str]) -> Any:
    path = builtins.get(kind, kind)
    if ":" not in path:
        raise ValueError(
            f"Unknown plugin type {kind!r}. Built-in: {', '.join(builtins)}; "
            "or use 'package.module:ClassName'."
        )
    module_name, class_name = path.split(":", 1)
    return getattr(importlib.import_module(module_name), class_name)
