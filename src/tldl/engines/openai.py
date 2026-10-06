"""Any OpenAI-compatible ``/v1/audio/transcriptions`` endpoint.

Works with OpenAI, Groq, speaches (faster-whisper-server), LocalAI, vLLM, ...
"""

import os
from typing import Any

import aiohttp

from ..models import Audio, Transcript
from .base import Engine


class OpenAIEngine(Engine):
    def __init__(
        self,
        name: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        model: str = "whisper-1",
        api_key: str | None = None,
        api_key_env: str | None = "OPENAI_API_KEY",
        prompt: str | None = None,
        temperature: float | None = None,
        timeout: float = 300,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, **kwargs)
        self.url = base_url.rstrip("/") + "/audio/transcriptions"
        self.model = model
        self.api_key = api_key or (os.environ.get(api_key_env) if api_key_env else None)
        self.prompt = prompt
        self.temperature = temperature
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def load(self) -> None:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        self._session = aiohttp.ClientSession(headers=headers, timeout=self.timeout)

    async def _transcribe(self, audio: Audio, language: str | None) -> Transcript:
        form = aiohttp.FormData()
        form.add_field("file", audio.data, filename=f"audio{audio.suffix}", content_type=audio.mime_type)
        form.add_field("model", self.model)
        form.add_field("response_format", "json")
        if language:
            form.add_field("language", language)
        if self.prompt:
            form.add_field("prompt", self.prompt)
        if self.temperature is not None:
            form.add_field("temperature", str(self.temperature))
        async with self._session.post(self.url, data=form) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"{self.url} returned {resp.status}: {(await resp.text())[:500]}")
            body = await resp.json(content_type=None)
        return Transcript(
            text=body.get("text", ""),
            language=body.get("language"),
            duration=body.get("duration"),
        )

    async def close(self) -> None:
        if self._session:
            await self._session.close()
