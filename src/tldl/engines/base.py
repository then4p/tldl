"""Engine plugin interface."""

import asyncio
import ctypes
import gc
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from ..models import Audio, Transcript

StageCallback = Callable[[str], Awaitable[None]]


class UnsupportedLanguage(Exception):
    """The audio is in a language the engine can't transcribe."""

    def __init__(self, language: str) -> None:
        super().__init__(language)
        self.language = language

log = logging.getLogger(__name__)

try:
    _malloc_trim = ctypes.CDLL(None).malloc_trim  # glibc only
except (OSError, AttributeError):
    _malloc_trim = None


def trim_memory() -> None:
    """Return freed heap memory to the OS.

    glibc keeps freed memory mapped, so without this an unloaded model's
    ~1 GB stays in the process's RSS.
    """
    if _malloc_trim is not None:
        _malloc_trim(0)


class Engine(ABC):
    """Base class for transcription engines.

    Subclasses implement :meth:`_transcribe` (and optionally :meth:`load` /
    :meth:`close`). All keys of the engine's config section except ``type``,
    ``concurrency``, ``description`` and ``idle_unload`` are passed to
    ``__init__`` as keyword arguments, so a subclass just declares the options
    it understands. :meth:`close` must release everything :meth:`load`
    created: it is also used to unload idle engines.

    Blocking work should run via ``asyncio.to_thread``.
    """

    def __init__(
        self,
        name: str,
        *,
        concurrency: int = 1,
        description: str | None = None,
        idle_unload: float | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.idle_unload = idle_unload  # seconds without use before the model is unloaded
        self._semaphore = asyncio.Semaphore(concurrency)
        self._load_lock = asyncio.Lock()
        self._loaded = False
        self._active = 0
        self._unload_timer: asyncio.TimerHandle | None = None
        self._unload_task: asyncio.Task | None = None

    @property
    def loaded(self) -> bool:
        return self._loaded

    async def ensure_loaded(self) -> None:
        async with self._load_lock:
            if not self._loaded:
                await self.load()
                self._loaded = True

    async def load(self) -> None:
        """Load models / open clients. Called once, lazily, before first use."""

    async def close(self) -> None:
        """Release resources."""

    async def transcribe(
        self, audio: Audio, language: str | None = None, on_stage: StageCallback | None = None
    ) -> Transcript:
        """Transcribe, reporting progress via ``on_stage``: "loading" (model not
        loaded yet), "queued" (all slots busy), then "transcribing"."""
        # Count as active before the first await, so an idle unload can't
        # slip in between loading and transcribing.
        self._active += 1
        if self._unload_timer:
            self._unload_timer.cancel()
        try:
            if on_stage and not self._loaded:
                await on_stage("loading")
            await self.ensure_loaded()
            if on_stage and self._semaphore.locked():
                await on_stage("queued")
            async with self._semaphore:
                if on_stage:
                    await on_stage("transcribing")
                start = time.perf_counter()
                result = await self._transcribe(audio, language)
                result.elapsed = time.perf_counter() - start
        finally:
            self._active -= 1
            if self._active == 0:
                await asyncio.to_thread(trim_memory)
                self.schedule_unload()
        result.engine = self.name
        result.text = result.text.strip()
        return result

    def schedule_unload(self) -> None:
        """(Re)start the idle timer, if ``idle_unload`` is set."""
        if self._unload_timer:
            self._unload_timer.cancel()
        if self.idle_unload:
            self._unload_timer = asyncio.get_running_loop().call_later(self.idle_unload, self._start_unload)

    def _start_unload(self) -> None:
        self._unload_task = asyncio.create_task(self.unload())

    async def unload(self) -> None:
        """Free the model (it's loaded again on next use)."""
        async with self._load_lock:
            if self._active or not self._loaded:
                return
            await self.close()
            self._loaded = False
        gc.collect()
        await asyncio.to_thread(trim_memory)
        log.info("unloaded idle engine %s", self.name)

    @abstractmethod
    async def _transcribe(self, audio: Audio, language: str | None) -> Transcript:
        """Transcribe ``audio``. ``language`` is an ISO code or None for auto-detect."""
