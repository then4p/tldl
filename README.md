# tldl

**too long; didn't listen.** A self-hosted bot that transcribes voice messages you forward to it on Telegram, Signal or WhatsApp. Demo page: [`docs/index.html`](docs/index.html).

## Setup

```sh
cp config.example.yaml config.yaml   # engines, messengers, allowed senders (all options documented there)
cp .env.example .env                 # tokens
docker compose up -d                 # add --profile signal / --profile whatsapp for those bridges
```

Everyone gets a welcome message with their ID on their first message. Add IDs to a messenger's `vips` (`id: name`) for unlimited use with `vip_engine`. With `access.public: true` everyone else can use the bot too, within `daily_limit` per person and `daily_total` for all of them (default 5 min and 2 h a day); `/info` shows what's left. Set `health.alert` to get a message when a messenger stops working; status is also at `:8000/healthz` and `:8000/metrics`.

- **Telegram:** create a bot with @BotFather, put the token in `.env`.
- **Signal:** register the bot's own number in signal-cli-rest-api (a registered number doesn't depend on a phone). Keep the image updated: Signal blocks clients older than about 90 days.
- **WhatsApp** (WAHA, unofficial, so use a spare number): scan the QR code at `http://localhost:3000`. The bot's phone must open WhatsApp at least every 14 days, or WhatsApp logs the bot out. `webhook_token` must match `?token=` in WAHA's hook URL.

## Usage

Forward a voice message to the bot. Commands: `/info`, `/lang <code|auto>`, `/details on|off`, `/help`, and for VIPs `/engines` and `/engine <name>`.

Engines: `onnx` runs local models on the CPU: **Parakeet v3** (fast, 25 European languages, detects the language) or **Canary 1B v2** (more accurate; a small Whisper model detects the language first). `openai` uses any OpenAI-compatible transcription API. Compare them with `docker compose exec tldl tldl transcribe audio.ogg -e parakeet -e canary`.

| Engine | 1 min message | 5 min message |
|---|---|---|
| Parakeet v3 | 3.0 s | 14.8 s |
| Canary 1B v2 | 7.8 s | 39.7 s |

Measured on an Intel i5-12400 (6 cores, no GPU) with int8 models and German audio.

## Development

```sh
uv venv && uv pip install -e '.[dev]' && .venv/bin/pytest
```

Plugins: set `type: package.module:Class` and subclass `tldl.engines.Engine` or `tldl.messengers.base.Messenger`.

## Credits

Models are downloaded at runtime, not shipped. [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) and [Canary 1B v2](https://huggingface.co/nvidia/canary-1b-v2) by NVIDIA, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); used as int8 ONNX conversions by [istupakov](https://huggingface.co/istupakov) (CC BY 4.0). [Silero VAD](https://github.com/snakers4/silero-vad), [onnx-asr](https://github.com/istupakov/onnx-asr) and [ONNX Runtime](https://github.com/microsoft/onnxruntime): MIT. The Docker image runs Debian's [FFmpeg](https://ffmpeg.org) (GPL) as a separate program. Bridges run in their own containers: [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) (MIT), [signal-cli](https://github.com/AsamK/signal-cli) (GPL-3.0), [WAHA](https://github.com/devlikeapro/waha) (Apache-2.0).

## License

tldl is licensed under the [GNU Affero General Public License v3.0](LICENSE) or later. If you run a modified version for other people, offer them its source code; the bot's `/help` links to the source.
