"""Daily transcription budget for non-VIP users: per user and shared by all of them."""

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import AccessConfig


def clock(seconds: float) -> str:
    """1:05 for 65 seconds."""
    s = math.ceil(max(seconds, 0))
    return f"{s // 60}:{s % 60:02d}"


class Budget:
    """Seconds of audio used today, reset at midnight in the configured timezone.

    ``state`` is the dict persisted in the bot's state file; it's updated in place.
    """

    def __init__(self, config: AccessConfig, state: dict) -> None:
        self.config = config
        self.tz = ZoneInfo(config.timezone)
        self.state = state
        self._roll()

    @property
    def reset_label(self) -> str:
        return f"midnight ({self.config.timezone.rsplit('/', 1)[-1].replace('_', ' ')} time)"

    def _roll(self) -> None:
        today = datetime.now(self.tz).date().isoformat()
        if self.state.get("date") != today:
            self.state.clear()
            self.state.update(date=today, users={}, total=0.0)

    def user_left(self, user: str) -> float:
        self._roll()
        return max(self.config.daily_limit - self.state["users"].get(user, 0.0), 0.0)

    def pool_left(self) -> float:
        self._roll()
        return max(self.config.daily_total - self.state["total"], 0.0)

    def reserve(self, user: str, seconds: float) -> str | None:
        """Book ``seconds`` for ``user``. Returns None, or a message saying why not."""
        user_left, pool_left = self.user_left(user), self.pool_left()
        if seconds <= min(user_left, pool_left):
            self.state["users"][user] = self.state["users"].get(user, 0.0) + seconds
            self.state["total"] += seconds
            return None
        if pool_left < user_left:
            if pool_left < 1:
                return f"⏳ Today's free minutes are used up for everyone. They're back at {self.reset_label}."
            return (f"⏳ This voice message is {clock(seconds)} long, but only {clock(pool_left)} of today's "
                    f"shared free minutes are left. They're back at {self.reset_label}.")
        if user_left < 1:
            return (f"⏳ You've used your {clock(self.config.daily_limit)} free minutes for today. "
                    f"They're back at {self.reset_label}.")
        return (f"⏳ This voice message is {clock(seconds)} long, but you have {clock(user_left)} of your "
                f"{clock(self.config.daily_limit)} free minutes left today. They're back at {self.reset_label}.")

    def refund(self, user: str, seconds: float) -> None:
        """Give back a reservation (e.g. the transcription failed)."""
        self._roll()
        if user in self.state["users"]:
            self.state["users"][user] = max(self.state["users"][user] - seconds, 0.0)
            self.state["total"] = max(self.state["total"] - seconds, 0.0)
