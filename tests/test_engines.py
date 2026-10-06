import shutil
import subprocess

import pytest

from tldl.audio import SAMPLE_RATE, AudioDecodeError, decode
from tldl.engines.onnx import OnnxEngine
from tldl.models import Audio

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@needs_ffmpeg
async def test_decode():
    ogg = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:a", "libopus", "-f", "ogg", "pipe:1"],
        capture_output=True, check=True,
    ).stdout
    samples = await decode(Audio(ogg, "audio/ogg"))
    assert abs(len(samples) / SAMPLE_RATE - 2.0) < 0.1
    assert samples.dtype.name == "float32" and 0.1 < abs(samples).max() <= 1.0


@needs_ffmpeg
async def test_decode_garbage():
    with pytest.raises(AudioDecodeError):
        await decode(Audio(b"not audio", "audio/ogg"))


def test_parakeet_defaults():
    e = OnnxEngine("p")
    assert e.model_name == "nemo-parakeet-tdt-0.6b-v3" and e.quantization == "int8"
    assert not e.accepts_language
    assert not e.use_vad(60) and e.use_vad(121)


def test_canary_always_split_and_takes_language():
    e = OnnxEngine("c", model="nemo-canary-1b-v2", language="de")
    assert e.accepts_language and e.use_vad(5)


def test_vad_overrides():
    assert OnnxEngine("p", vad=True).use_vad(5)
    assert not OnnxEngine("p", vad=False).use_vad(3600)
    assert not OnnxEngine("c", model="nemo-canary-1b-v2", vad=False).use_vad(5)
    custom = OnnxEngine("p", vad_options={"speech_pad_ms": 100})
    assert custom.vad_options == {"speech_pad_ms": 100, "min_silence_duration_ms": 500}
