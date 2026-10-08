"""Periodic messenger health checks, alerts, and the /healthz and /metrics endpoints."""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from aiohttp import web

from .config import HealthConfig
from .messengers.base import Messenger
from .models import Health

log = logging.getLogger(__name__)


@dataclass
class State:
    ok: bool | None = None  # None: not checked yet
    detail: str = ""
    since: float = 0.0  # when ok last changed
    failures: int = 0  # consecutive failed checks
    alerted: bool = False
    last_alert: float = 0.0


class HealthMonitor:
    def __init__(self, messengers: dict[str, Messenger], config: HealthConfig) -> None:
        self.messengers = messengers
        self.config = config
        self.state = {name: State() for name in messengers}
        self._wake = asyncio.Event()
        for m in messengers.values():
            m.on_health_change = self._wake.set

    async def run(self) -> None:
        while True:
            await self.check_all()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), self.config.interval)
            except TimeoutError:
                pass

    async def check_all(self) -> None:
        for name, messenger in self.messengers.items():
            try:
                health = await asyncio.wait_for(messenger.check_health(), 30)
            except Exception as exc:
                health = Health(False, f"Health check failed: {exc!r}")
            await self.update(name, health)

    async def update(self, name: str, health: Health) -> None:
        s, now = self.state[name], time.time()
        if health.ok != s.ok:
            s.since = now
            log.log(logging.INFO if health.ok else logging.WARNING, "health: %s %s: %s", name, "ok" if health.ok else "FAILING", health.detail)
        s.ok, s.detail = health.ok, health.detail
        if health.ok:
            s.failures = 0
            if s.alerted:
                s.alerted = False
                await self.alert(f"✅ {name} is working again.")
            return
        s.failures += 1
        remind = self.config.remind_every
        if (not s.alerted and s.failures >= self.config.confirm) or (s.alerted and remind and now - s.last_alert >= remind):
            s.alerted, s.last_alert = True, now
            await self.alert(f"⚠️ {name} is not working: {health.detail}")

    async def alert(self, text: str) -> None:
        target = self.config.alert
        if not target:
            return
        try:
            await self.messengers[target["messenger"]].send(str(target["chat_id"]), text)
        except Exception:
            log.exception("health: could not send alert via %s", target["messenger"])

    # ---- HTTP -----------------------------------------------------------------

    def routes(self) -> list[web.RouteDef]:
        return [web.get("/healthz", self.healthz), web.get("/metrics", self.metrics)]

    async def healthz(self, _: web.Request) -> web.Response:
        body = {
            name: {
                "ok": s.ok,
                "detail": s.detail,
                "since": datetime.fromtimestamp(s.since, UTC).isoformat() if s.since else None,
            }
            for name, s in self.state.items()
        }
        healthy = all(s.ok is not False for s in self.state.values())
        return web.json_response({"ok": healthy, "messengers": body}, status=200 if healthy else 503)

    async def metrics(self, _: web.Request) -> web.Response:
        lines = [
            "# HELP tldl_messenger_up Whether the messenger passed its last health check (1) or not (0).",
            "# TYPE tldl_messenger_up gauge",
        ]
        lines += [f'tldl_messenger_up{{messenger="{name}"}} {int(bool(s.ok))}' for name, s in self.state.items() if s.ok is not None]
        return web.Response(text="\n".join(lines) + "\n", content_type="text/plain", headers={"Cache-Control": "no-store"})
