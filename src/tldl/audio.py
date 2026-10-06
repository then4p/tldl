"""Audio decoding via the ffmpeg CLI."""

import asyncio

import numpy as np

from .models import Audio

SAMPLE_RATE = 16_000


class AudioDecodeError(RuntimeError):
    pass


async def decode(audio: Audio) -> np.ndarray:
    """Decode any format ffmpeg understands to 16 kHz mono float32 samples."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", "pipe:0", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "f32le", "-c:a", "pcm_f32le", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(audio.data)
    if proc.returncode != 0:
        raise AudioDecodeError(err.decode(errors="replace").strip() or "ffmpeg failed")
    return np.frombuffer(out, dtype=np.float32)
