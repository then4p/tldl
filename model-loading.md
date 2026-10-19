# Model loading on the homelab

Measured 2026-10-10 on the homelab: Intel i5-12400 (6 cores, 12 threads, no GPU),
bot container limited to 5 CPUs (`TLDL_CPUS=5`), engines with `threads: 5`,
onnxruntime 1.31, onnx-asr 0.12, int8 models.

## Summary

Loading was slow because of how the CPU limit and ONNX Runtime interacted, not
because of the hardware. ONNX Runtime's worker threads busy-wait ("spin") between
work items. Docker's `cpus: 5` gives the container a CPU-time budget per 100 ms
period and pauses it once the budget is used up. The spinning threads burned that
budget doing nothing, so the container was paused over and over.

The engine now turns spinning off by default (`spinning: false`). Under the same
5-CPU limit:

| | Before | After |
|---|---|---|
| Parakeet load | 2.3 s | **1.5 s** |
| Canary + language detector load | 6.3 s | **1.5 s** |
| Parakeet, 30 s of audio | 4.3 s | **1.2 s** |

Transcription got faster too, which is the bigger win in daily use. The CPU limit
now costs almost nothing: without any limit the numbers are the same (1.5 s and
1.2 s).

## Transcription speed

Synthetic German speech, through `tldl transcribe` in the bot container (5-CPU
limit, `threads: 5`, spinning off), two runs each with near-identical results:

| | 1 min of speech | 5 min of speech |
|---|---|---|
| **Parakeet** (free users) | 3.6 s | 12.7 s |
| **Canary** (VIPs, incl. language detection) | 11.0 s | 40.5 s |

Canary is about 3x slower than Parakeet. Where Canary's time goes, measured
without the CPU limit on the 1-minute clip:

| Canary setup | Time |
|---|---|
| 6 threads, language detection | 9.7 s |
| 6 threads, language fixed (no detection) | 9.2 s |
| 5 threads, language fixed | 10.5 s |

So language detection costs about 0.5 s, and 5 instead of 6 threads about 13%.
A week earlier, Canary measured 7.8 s for 57 s of speech with 6 threads; the
remaining ~1.5 s difference isn't fully explained (different clip, onnxruntime
1.30 then vs 1.31 now).

Spinning doesn't matter for transcription itself: without the limit, Canary took
10.9 s with spinning and 11.1 s without. Under the limit, spinning makes it slower
(13.6 s), so off is the right default for both models.

Two jobs sharing the 5-CPU budget slow each other down a lot: a 140 s voice
message took 234 s while a benchmark ran a second Canary in the same container.
Benchmark in a separate container (see below), not in the running bot.

## What loading consists of

Each run is a fresh Python process, as after an idle unload (`idle_unload: 60`).
The model files were in the OS page cache, as they are in normal operation; the
first load after a reboot also has to read them from disk.

| Step | Parakeet | Canary + detector |
|---|---|---|
| Python imports | 0.26 s | 0.26 s |
| Hugging Face update check | 0–0.01 s | 0.01–0.09 s |
| ONNX sessions | encoder, decoder, Silero VAD, preprocessors | Canary encoder and decoder, Silero VAD, Whisper-base encoder and decoder (language detection), preprocessors |
| Model files | 640 MB | 982 MB + 75 MB (Whisper) |

Before the fix, under the CPU limit, the time per session varied wildly: the 2 MB
Silero VAD took 0.1 s without the limit and up to 1.4 s with it, and the small
Whisper detector took longer than all of Canary. That pattern, small models taking
long at random, is what pointed at throttling rather than at model size.

## Experiments

All under the 5-CPU limit unless noted. Load times in seconds; "30 s" is
Parakeet transcribing 30 s of audio (random noise, so only relative numbers
matter).

| Setup | Parakeet load | Canary load | 30 s |
|---|---|---|---|
| Limit, spinning on (old default) | 2.3 | 6.3 | 4.5 |
| No limit, spinning on | 1.6 | 2.9 | 2.3 |
| Limit, graph optimization `extended` | 2.0 | 6.4 | 5.0 |
| Limit, graph optimization `basic` | 2.2 | 6.1 | 5.0 |
| Limit, graph optimization off | 2.2 | 6.0 | 5.4 |
| Pinned to CPUs 0–4 instead of a limit | 1.8 | 4.9 | 5.5 |
| **Limit, spinning off (new default)** | **1.5** | **1.5** | **1.2–1.3** |
| No limit, spinning off | 1.5 | 1.5 | 1.2 |

What didn't help:

- **Skipping the Hugging Face check** (`HF_HUB_OFFLINE=1`): it costs at most
  0.09 s, so it isn't worth the risk of missing model fixes.
- **Lower graph optimization levels**: loading stays as slow, and transcription
  gets 10–20% slower.
- **Pinning to CPUs 0–4**: Linux numbers the 12400's hyperthreads in pairs (CPUs 0
  and 1 are both core 0), so CPUs 0–4 are only 2.5 physical cores. Pinning to
  0, 2, 4, 6, 8 would use 5 cores, but turning spinning off makes it unnecessary.

## Settings

- `spinning` (engine option, default `false`): ONNX Runtime's busy-waiting. Leave
  it off; it was never faster here, with or without a CPU limit. The same session
  options now also apply to Silero VAD and the Whisper language detector, which
  previously used ONNX Runtime's defaults.
- `TLDL_CPUS` in `.env`: the container's CPU limit (`0` = none).
- `threads` (engine option): set to match `TLDL_CPUS`.

## Reproducing

`scripts/bench_load.py` measures one load per run. Copy it to `data/` (mounted in
the container) and run it in a fresh process each time:

```sh
cp scripts/bench_load.py data/
docker compose exec -T tldl python /app/data/bench_load.py parakeet          # current settings (competes with the bot for CPU)
docker compose exec -T tldl python /app/data/bench_load.py canary nospin     # or: all, extended, basic, none
```

To compare CPU limits without touching the running bot, start a throwaway
container from the same image:

```sh
docker run --rm --cpus=5 -e HF_HOME=/app/models/hf \
  -v "$PWD/models:/app/models" -v "$PWD/data:/app/data" \
  tldl-tldl python /app/data/bench_load.py parakeet
```
