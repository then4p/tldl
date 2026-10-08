"""YAML config loading with ``${ENV_VAR}`` / ``${ENV_VAR:-default}`` interpolation."""

import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .registry import DEFAULT_ENGINE_TYPE

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

_RENAMED = {"transcribers": "engines", "default_transcriber": "default_engine"}


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ValueError(f"environment variable {name} is not set (referenced in config)")

        return _ENV_RE.sub(sub, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


@dataclass
class HttpConfig:
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class HealthConfig:
    interval: float = 300  # seconds between checks
    alert: dict[str, Any] | None = None  # {"messenger": name, "chat_id": id}; None = only log
    remind_every: float | None = 86400  # repeat the alert while still failing; None = once
    confirm: int = 2  # consecutive failed checks before alerting (ignores short blips)


@dataclass
class Config:
    engines: dict[str, dict[str, Any]]
    messengers: dict[str, dict[str, Any]] = field(default_factory=dict)
    default_engine: str | None = None
    default_language: str | None = None
    show_details: bool = False  # default for new chats; toggled per chat with /details
    status_updates: bool = True  # progress message that turns into the transcript
    max_duration: float | None = None  # seconds; longer audio is rejected
    preload: bool = False  # load the default engine at startup
    idle_unload: float | None = None  # default for every engine; engines can override
    state_file: str | None = None
    http: HttpConfig = field(default_factory=HttpConfig)
    health: HealthConfig = field(default_factory=HealthConfig)

    def __post_init__(self) -> None:
        if not self.engines:
            raise ValueError("config must define at least one engine")
        for section in self.engines.values():
            section.setdefault("type", DEFAULT_ENGINE_TYPE)
        for name, section in self.messengers.items():
            if "type" not in section:
                raise ValueError(f"messengers.{name} needs a 'type'")
        if self.default_engine is None:
            self.default_engine = next(iter(self.engines))
        if self.default_engine not in self.engines:
            raise ValueError(f"default_engine {self.default_engine!r} is not defined in engines")
        alert = self.health.alert
        if alert and (alert.get("messenger") not in self.messengers or "chat_id" not in alert):
            raise ValueError("health.alert needs a configured 'messenger' and a 'chat_id'")


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    for old, new in _RENAMED.items():
        if old in raw:
            raise ValueError(f"config: '{old}' was renamed to '{new}'")
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"config: unknown keys {sorted(unknown)}; known: {sorted(known)}")

    # Only expand enabled messengers, so a disabled one's missing env vars don't matter.
    messengers = {}
    for name, section in (raw.pop("messengers", None) or {}).items():
        if section and section.pop("enabled", True):
            messengers[name] = section
    raw = _expand(raw)
    raw["messengers"] = _expand(messengers)
    raw["engines"] = {name: section or {} for name, section in (raw.get("engines") or {}).items()}
    raw["http"] = HttpConfig(**(raw.get("http") or {}))
    raw["http"].port = int(raw["http"].port)
    raw["health"] = HealthConfig(**(raw.get("health") or {}))
    return Config(**raw)


def split_type(section: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    options = dict(section)
    return options.pop("type"), options
