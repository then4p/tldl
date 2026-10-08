"""Local models on ONNX Runtime via onnx-asr: NVIDIA Parakeet and Canary, and more.

Models download from Hugging Face on first use (into ``HF_HOME``).
"""

import asyncio
from typing import Any

import numpy as np

from ..audio import SAMPLE_RATE, decode
from ..models import Audio, Transcript
from .base import Engine, UnsupportedLanguage

# Encoder-decoder models silently drop everything after their ~30 s input
# window, so their audio is always split into VAD segments (max 20 s).
_SHORT_WINDOW_MODELS = ("canary", "whisper")

#: Languages Canary 1B v2 (and Parakeet v3) transcribe.
EU_LANGUAGES = frozenset("bg cs da de el en es et fi fr hr hu it lt lv mt nl pl pt ro ru sk sl sv uk".split())


def pick_language(scores: dict[str, float], supported: frozenset[str]) -> str:
    """Whisper's language scores -> best supported language.

    Raises UnsupportedLanguage when the overall best guess isn't supported.
    """
    top = max(scores, key=scores.__getitem__)
    if top not in supported:
        raise UnsupportedLanguage(top)
    return max(supported & scores.keys(), key=scores.__getitem__)


class OnnxEngine(Engine):
    def __init__(
        self,
        name: str,
        *,
        model: str = "nemo-parakeet-tdt-0.6b-v3",
        path: str | None = None,
        quantization: str | None = "int8",
        providers: list[str] | None = None,
        threads: int = 0,
        memory_arena: bool = False,
        prepack: bool = False,
        language: str | None = None,
        detect_language: str | None = None,
        vad: bool | str = "auto",
        vad_above: float | None = None,
        vad_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, **kwargs)
        self.model_name = model
        self.path = path
        self.quantization = quantization
        self.providers = providers
        self.threads = threads  # ONNX Runtime intra-op threads, 0 = one per physical core
        # ONNX Runtime's memory arena keeps every request's peak allocation
        # forever (Parakeet: 3.4 GB after a 5 min message instead of 1 GB) and
        # raises the peak itself; without it, no measurable slowdown.
        self.memory_arena = memory_arena
        # Prepacked weights speed up some kernels but cost ~400-550 MB extra.
        # Free for Parakeet; Canary is ~10% slower without.
        self.prepack = prepack
        # Parakeet detects the language itself. Canary can't: without a
        # language it *translates* to English, so it uses the chat's /lang,
        # falling back to this engine's `language`.
        self.accepts_language = "canary" in model.lower()
        self.language = language
        # A Whisper model (e.g. onnx-community/whisper-base) that only detects
        # the language when the chat hasn't set one with /lang (~0.3 s, ~300 MB).
        self.detect_language = detect_language if self.accepts_language else None
        self._detector = None
        # VAD splits audio at pauses into segments of at most 20 s. "auto"
        # splits audio longer than `vad_above` seconds: always for short-window
        # models, above 2 min for Parakeet (which is more accurate unsplit but
        # fails somewhere past 5 min and needs ~2 GB RAM per 5 min).
        self.vad = vad
        short_window = any(k in model.lower() for k in _SHORT_WINDOW_MODELS)
        self.vad_above = vad_above if vad_above is not None else (0 if short_window else 120)
        # Generous padding: silero's defaults clip word edges.
        self.vad_options = {"speech_pad_ms": 300, "min_silence_duration_ms": 500, **(vad_options or {})}
        self._model = self._vad_model = self._detector = None

    def _load(self) -> None:
        import onnx_asr
        import onnxruntime as ort

        sess_options = ort.SessionOptions()
        sess_options.enable_cpu_mem_arena = self.memory_arena
        if not self.prepack:
            sess_options.add_session_config_entry("session.disable_prepacking", "1")
        if self.threads:
            sess_options.intra_op_num_threads = self.threads
        self._model = onnx_asr.load_model(
            self.model_name,
            self.path,
            quantization=self.quantization,
            providers=self.providers,
            sess_options=sess_options,
        )
        if self.vad:
            vad = onnx_asr.load_vad("silero", providers=self.providers)
            self._vad_model = self._model.with_vad(vad, **self.vad_options)
        if self.detect_language:
            self._detector = onnx_asr.load_model(
                self.detect_language, quantization="int8", providers=self.providers, sess_options=sess_options
            ).asr

    def _detect(self, samples) -> str:
        # Encoder + one decoder step after <|startoftranscript|>: the scores of
        # the language tokens. (onnx-asr's Whisper does this internally but only
        # returns the transcript, so this uses its building blocks.)
        whisper = self._detector
        clip = samples[: 30 * SAMPLE_RATE][None, :]
        encoded = whisper._encode(clip, np.array([clip.shape[1]]))
        logits, _ = whisper._decode(np.array([[whisper._bos_token_id]]), whisper._create_state(), encoded)
        scores = {
            token[2:-2]: float(logits[0, -1, token_id])
            for token, token_id in whisper._tokens.items()
            if token.startswith("<|") and token.endswith("|>") and 2 <= len(token) - 4 <= 3 and token[2:-2].isalpha() and token[2:-2].islower()
        }
        return pick_language(scores, EU_LANGUAGES)

    async def load(self) -> None:
        await asyncio.to_thread(self._load)

    def use_vad(self, seconds: float) -> bool:
        if self.vad == "auto":
            return seconds > self.vad_above
        return bool(self.vad)

    def _run(self, samples, language: str | None) -> str:
        kwargs = {"language": language} if language and self.accepts_language else {}
        if self._vad_model is not None and self.use_vad(len(samples) / SAMPLE_RATE):
            segments = self._vad_model.recognize(samples, sample_rate=SAMPLE_RATE, **kwargs)
            return " ".join(s.text.strip() for s in segments if s.text.strip())
        return self._model.recognize(samples, sample_rate=SAMPLE_RATE, **kwargs)

    async def _transcribe(self, audio: Audio, language: str | None) -> Transcript:
        samples = await decode(audio)
        if not self.accepts_language:
            language = None
        elif not language and self._detector is not None:
            language = await asyncio.to_thread(self._detect, samples)
        else:
            language = language or self.language
        text = await asyncio.to_thread(self._run, samples, language)
        return Transcript(text=text, language=language, duration=len(samples) / SAMPLE_RATE)

    async def close(self) -> None:
        self._model = self._vad_model = self._detector = None
