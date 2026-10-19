"""Two passes: NVIDIA Parakeet for a fast transcript, then Whisper for a better one.

Parakeet (onnx-asr, int8) takes ~3.5 s per minute of audio and stays loaded
(~0.7 GB). Whisper large-v3-turbo (faster-whisper, int8) is ~4x slower but
clearly better on colloquial speech. It needs ~2 GB, so it's loaded for each
message (~1.5 s) and freed when no message is waiting for it.
Models download from Hugging Face on first use (into ``HF_HOME``).
"""

import asyncio
import ctypes
import gc
import logging

import numpy as np

from .audio import SAMPLE_RATE

log = logging.getLogger(__name__)

PARAKEET = "nemo-parakeet-tdt-0.6b-v3"
WHISPER = "large-v3-turbo"
# Parakeet is more accurate on unsplit audio, but fails somewhere past 5 min
# and needs ~2 GB RAM per 5 min, so longer audio is split at pauses.
SPLIT_ABOVE = 120  # seconds
VAD_OPTIONS = {"min_silence_duration_ms": 500, "speech_pad_ms": 300}  # silero's defaults clip word edges

try:
    _malloc_trim = ctypes.CDLL(None).malloc_trim  # glibc only
except (OSError, AttributeError):
    _malloc_trim = None


def trim_memory() -> None:
    """Return freed heap memory to the OS; glibc otherwise keeps a freed model's RSS."""
    if _malloc_trim is not None:
        _malloc_trim(0)


class Transcriber:
    def __init__(self, threads: int = 0) -> None:
        self.threads = threads  # 0 = one per physical core
        self._parakeet = self._parakeet_vad = None
        self._whisper = None
        self._whisper_users = 0  # messages that want Whisper loaded
        self._fast_lock = asyncio.Lock()  # one job per model at a time
        self._better_lock = asyncio.Lock()

    def _load_parakeet(self) -> None:
        import onnx_asr
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.enable_cpu_mem_arena = False  # the arena keeps peak memory allocated forever
        opts.add_session_config_entry("session.disable_prepacking", "1")  # ~5% slower, ~0.5 GB less
        # Busy-waiting threads burn a Docker CPU quota without doing work.
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
        if self.threads:
            opts.intra_op_num_threads = self.threads
        self._parakeet = onnx_asr.load_model(PARAKEET, quantization="int8", sess_options=opts)
        vad = onnx_asr.load_vad("silero", sess_options=opts)
        self._parakeet_vad = self._parakeet.with_vad(vad, **VAD_OPTIONS)

    async def load(self) -> None:
        """Load Parakeet (Whisper is loaded per message)."""
        async with self._fast_lock:
            if self._parakeet is None:
                await asyncio.to_thread(self._load_parakeet)

    def _fast(self, samples: np.ndarray) -> str:
        if len(samples) / SAMPLE_RATE > SPLIT_ABOVE:
            return " ".join(s.text.strip() for s in self._parakeet_vad.recognize(samples, sample_rate=SAMPLE_RATE))
        return self._parakeet.recognize(samples, sample_rate=SAMPLE_RATE)

    async def fast(self, samples: np.ndarray) -> str:
        await self.load()
        async with self._fast_lock:
            text = await asyncio.to_thread(self._fast, samples)
            await asyncio.to_thread(trim_memory)  # long audio temporarily needs a lot
            return text.strip()

    def _better(self, samples: np.ndarray) -> str:
        if self._whisper is None:
            from faster_whisper import WhisperModel

            self._whisper = WhisperModel(WHISPER, device="cpu", compute_type="int8", cpu_threads=self.threads)
        segments, _ = self._whisper.transcribe(
            samples,
            beam_size=5,
            # Each 30 s window on its own: one misheard window can't derail the rest.
            condition_on_previous_text=False,
            # Cuts silence and packs speech into Whisper's 30 s windows: fewer
            # windows to decode, and no made-up text for silent stretches.
            vad_filter=True,
            vad_parameters=VAD_OPTIONS,
        )
        return " ".join(s.text.strip() for s in segments)

    async def better(self, samples: np.ndarray) -> str:
        self._whisper_users += 1
        try:
            async with self._better_lock:
                return (await asyncio.to_thread(self._better, samples)).strip()
        finally:
            self._whisper_users -= 1
            if not self._whisper_users:
                self._whisper = None
                gc.collect()
                await asyncio.to_thread(trim_memory)
