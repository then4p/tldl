# tldl

**too long; didn't listen.** A self-hosted bot that transcribes voice messages. Forward a voice message to it on
**Telegram**, **Signal** or **WhatsApp** and it replies with the transcript.

Messengers and transcription engines are both plugins, so you can mix and match,
and switch engines per chat to compare models.

```
 Telegram ─┐                          ┌─ onnx: local models on ONNX Runtime (CPU)
 Signal ───┼─▶ core: auth, commands ─▶┤    NVIDIA Parakeet v3, Canary 1B v2, ...
 WhatsApp ─┘   per-chat engine/lang   └─ openai: any OpenAI-compatible API (Groq, OpenAI, a GPU box, ...)
```

## Quick start

```sh
cp config.example.yaml config.yaml   # edit: engines, messengers, allowed_senders
cp .env.example .env                 # fill in tokens
docker compose up -d                 # Telegram only
docker compose --profile signal --profile whatsapp up -d   # plus the bridges
```

Without Docker (needs Python 3.14+ and `ffmpeg`):

```sh
uv venv && uv pip install -e .
.venv/bin/tldl -c config.yaml run
```

The first time someone who isn't in `allowed_senders` messages the bot, it
replies with their sender id. Add that id to the config and restart.

After code changes, rebuild with `docker compose up -d --build`. After changes
to `config.yaml` or `.env`, `docker compose restart tldl` is enough.

## Status updates

When a voice message arrives, the bot replies right away with a status message
and edits it as the work progresses:

`📥 Received` → `⏳ Loading model…` / `⏳ Waiting for a free slot…` (only when that happens) → `✍️ Transcribing…` → the transcript

Telegram, Signal and WhatsApp all support editing. If a messenger can't edit
(or an edit fails, e.g. WhatsApp's 15-minute edit window has passed), the
transcript is sent as a new message instead. Set `status_updates: false` to
send only the transcript.

## Chat commands

| Command | |
|---|---|
| `/engines` | list configured engines (▶ marks the current one) |
| `/engine <name>` | use another engine in this chat |
| `/lang <code>` / `/lang auto` | set the spoken language (used by Canary; Parakeet detects it) |
| `/details on` / `/details off` | show engine, language, audio length and processing time under each transcript (off by default) |
| `/help` | usage |

Per-chat choices are saved in `state_file`.

## Messengers

### Telegram
Create a bot with [@BotFather](https://t.me/BotFather) and set `TELEGRAM_BOT_TOKEN`.
It uses long polling, so you don't need a public URL. It handles voice messages,
audio files and round video messages. The Bot API only lets bots download files
up to 20 MB.

### Signal: [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api)
The bridge must run in `MODE=json-rpc` (the compose file does this). There are
two ways to set it up:

- **Dedicated number:** register a second number through the REST API and
  forward voice notes to it.
- **Your own account:** open `http://localhost:8080/v1/qrcodelink?device_name=tldl`,
  scan it under *Settings → Linked devices*, and set `note_to_self: true`. Then
  forward voice notes to *Note to Self* and the transcript shows up there as a reply.

Set `SIGNAL_NUMBER` to the account's number. `allowed_senders` takes phone numbers or Signal UUIDs.

### WhatsApp: [WAHA](https://waha.devlike.pro)
WAHA links to WhatsApp the way WhatsApp Web does and sends incoming messages to
the bot's webhook (`/webhook/<messenger name>`). Open the WAHA dashboard at
`http://localhost:3000`, start the `default` session and scan the QR code. There
are two ways to set it up:

- **Dedicated number:** link a second WhatsApp account and forward voice messages to it.
- **Your own account:** set `self_chat: true` and `WHATSAPP_HOOK_EVENTS=message.any`,
  then forward voice messages to *Message yourself*.

`webhook_token` must match the `?token=` in WAHA's hook URL.
`allowed_senders` takes phone numbers without `+`.

> WhatsApp doesn't officially allow unofficial clients like WAHA, so there is a
> small risk the linked number gets banned. A dedicated number keeps that risk
> away from your main account.

## Engines

Define any number of engines under `engines:`. Each has a name you choose and a
`type`, which defaults to `onnx`. Every engine also accepts `description`,
`concurrency` (parallel jobs, default 1) and `idle_unload` (seconds without use
before the model is freed; it loads again on the next message, in about a
second). The top-level `idle_unload` sets the default for all engines; set an
engine's `idle_unload: null` to keep that one loaded. Models load on
first use, or at startup for the default engine with `preload: true`.

### `onnx`: local models on ONNX Runtime

This engine runs models through [onnx-asr](https://github.com/istupakov/onnx-asr)
on ONNX Runtime, on the CPU, without PyTorch. Models download from Hugging Face
on first use and are cached in `./models`.

| option | default | |
|---|---|---|
| `model` | `nemo-parakeet-tdt-0.6b-v3` | any onnx-asr model name or Hugging Face repo |
| `quantization` | `int8` | `null` for full precision (needs a plain model folder via `path`) |
| `path` | – | load the model from a local folder instead of Hugging Face |
| `language` | – | fallback language for models that need one (Canary) |
| `threads` | `0` | ONNX Runtime threads per job; 0 = one per physical core |
| `prepack` | `false` | prepacked weights: ~10% faster Canary, +400–550 MB RAM |
| `memory_arena` | `false` | ONNX Runtime's memory arena (keeps peak memory allocated) |
| `vad` / `vad_above` | `auto` / model-dependent | split audio at pauses (Silero VAD, max 20 s per piece) |

**Parakeet TDT 0.6B v3** (`nemo-parakeet-tdt-0.6b-v3`) covers 25 European
languages. It detects the language itself and ignores `/lang`, and adds
punctuation. It's very fast on CPU: on German test audio it was 2–3× faster
than Whisper large-v3-turbo (int8), with the same accuracy. It's more accurate
when it gets the whole recording in one pass, but it fails somewhere past 5
minutes, so recordings longer than `vad_above` (default 120 s) are split.

**Canary 1B v2** (`nemo-canary-1b-v2`) covers the same languages. It's bigger,
usually more accurate, and about 2× slower than Parakeet. It **can't detect the
language**: it uses the chat's `/lang`, or else the engine's `language` option.
Without either, it *translates* the speech into English. It also silently drops
audio beyond its ~30 s window, so the engine always splits audio for it.

### `openai`: OpenAI-compatible API

This engine sends audio to any `/v1/audio/transcriptions` endpoint: Groq,
OpenAI, speaches, LocalAI, vLLM, or a GPU machine on your network. Options:
`base_url`, `model`, `api_key` or `api_key_env`, `prompt`, `temperature` and
`timeout`.

### Comparing engines

```sh
docker compose exec tldl tldl transcribe /app/data/sample.ogg -e parakeet -e canary -l de
```

This prints each transcript with its wall time and real-time factor. Model
loading isn't counted in the timings.

## Writing a plugin

Set `type: my_package.module:MyClass` to load your own class. You don't need to
change this repo.

**Engine.** Subclass `Engine` and implement `_transcribe`. Config keys arrive as
keyword arguments:

```python
import asyncio

from tldl.audio import SAMPLE_RATE, decode   # ffmpeg → 16 kHz mono float32 numpy array
from tldl.engines import Engine
from tldl.models import Audio, Transcript

class MyEngine(Engine):
    def __init__(self, name, *, model="x", **kwargs):
        super().__init__(name, **kwargs)
        self.model_name = model

    async def load(self):            # optional, called once before first use
        ...

    async def _transcribe(self, audio: Audio, language: str | None) -> Transcript:
        samples = await decode(audio)
        text = await asyncio.to_thread(run_my_model, samples)   # keep the event loop free
        return Transcript(text=text, language=language, duration=len(samples) / SAMPLE_RATE)
```

**Messenger.** Subclass `tldl.messengers.base.Messenger`. You can either
implement `run()` as a receive loop, or return aiohttp routes from `routes()` for
webhooks. For each incoming message, build an `IncomingMessage` with an
`AudioRef` whose `fetch()` downloads the audio lazily, and call
`await self.dispatch(msg)`. Then implement `reply(msg, text)`, returning the sent
message's id. Set `supports_edit = True` and implement `edit(msg, handle, text)`
if the platform can edit messages, so status updates are edited in place. The
core takes care of authorization, commands, engine selection and splitting long
replies.

To add a type to the built-in short names, register it in
`src/tldl/registry.py`.

## Performance notes

The image runs on Python 3.14. Measured on German test audio with Parakeet and
Canary, Python 3.12, 3.14 and free-threaded 3.14t performed the same, within
±4%. That holds even with several messages in parallel: the work happens in
ONNX Runtime's C++ code, which already runs outside the GIL. To try 3.14t
anyway, set `PYTHON: "3.14t"` in `docker-compose.yml`. All dependencies have
free-threaded wheels, and the GIL stays off.

What does help when several messages arrive at once is `concurrency` combined
with `threads`. Four jobs that share the CPU finish in about half the time of
four jobs run one after another:

```yaml
parakeet:
  concurrency: 4   # jobs in parallel, sharing one loaded model
  threads: 4       # CPU threads per job: roughly cores / concurrency
```

A single message then gets fewer threads and takes a bit longer. With one or
two users, the defaults (`concurrency: 1`, `threads: 0`) give the fastest
replies.

### Memory

Memory measured with int8 models:

| | Parakeet | Canary |
|---|---|---|
| Loaded, and after any message | ~0.8 GB | ~1.2 GB |
| Peak during a 5 min message | ~2.0 GB | ~2.8 GB |
| All engines unloaded (`idle_unload`) | ~0.15 GB for the whole process | |

Two ONNX Runtime defaults are switched off. The memory arena kept each
request's peak allocated forever: 3.4 GB (Parakeet) and 5.5 GB (Canary) after
one 5-minute message. Weight prepacking cost 400–550 MB. After each message
and each unload, the engine calls glibc's `malloc_trim`, so freed memory goes
back to the OS (~50 ms).

## Development

```sh
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest
```
