"""OpenAI Whisper on CTranslate2 via faster-whisper (``pip install tldl[whisper]``).

Slower than Parakeet, but better on colloquial speech, which makes it a good
second pass (``vip_refine``). Models download from Hugging Face on first use.
"""

import asyncio

from ..audio import SAMPLE_RATE, decode
from ..models import Audio, Transcript
from .base import Engine


class WhisperEngine(Engine):
    def __init__(
        self,
        name: str,
        *,
        model: str = "large-v3-turbo",
        compute_type: str = "int8",
        threads: int = 0,
        beam_size: int = 5,
        language: str | None = None,
        vad: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(name, **kwargs)
        self.model_name = model
        self.compute_type = compute_type
        self.threads = threads
        self.beam_size = beam_size
        self.language = language  # fallback when the chat hasn't set one; else detected
        # Cuts silence and packs the speech into Whisper's 30 s windows: fewer
        # windows to decode, and no made-up text for silent stretches.
        self.vad = vad
        self._model = None

    def _load(self) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self.model_name, device="cpu", compute_type=self.compute_type, cpu_threads=self.threads
        )

    async def load(self) -> None:
        await asyncio.to_thread(self._load)

    async def close(self) -> None:
        self._model = None

    def _run(self, samples, language: str | None) -> tuple[str, str]:
        segments, info = self._model.transcribe(
            samples,
            language=language,
            beam_size=self.beam_size,
            # Each window on its own: one misheard window can't derail the rest.
            condition_on_previous_text=False,
            vad_filter=self.vad,
            vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300},
        )
        return " ".join(s.text.strip() for s in segments), info.language

    async def _transcribe(self, audio: Audio, language: str | None) -> Transcript:
        samples = await decode(audio)
        text, language = await asyncio.to_thread(self._run, samples, language or self.language)
        return Transcript(text=text, language=language, duration=len(samples) / SAMPLE_RATE)
