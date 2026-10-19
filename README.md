# tldl

**too long; didn't listen.** A self-hosted bot that transcribes voice messages you forward to it on Telegram, Signal or WhatsApp. Demo page: [`docs/index.html`](docs/index.html).

## Setup

```sh
cp config.example.yaml config.yaml   # messengers and health alerts (all options documented there)
cp .env.example .env                 # tokens
docker compose up -d                 # add --profile signal / --profile whatsapp for those bridges
```

Anyone who can message the bot can use it. Set `health.alert` to get a message when a messenger stops working; status is also at `:8000/healthz` and `:8000/metrics`.

- **Telegram:** create a bot with @BotFather, put the token in `.env`.
- **Signal:** register the bot's own number in signal-cli-rest-api (a registered number doesn't depend on a phone). Keep the image updated: Signal blocks clients older than about 90 days.
- **WhatsApp** (WAHA, unofficial, so use a spare number): scan the QR code at `http://localhost:3000`. The bot's phone must open WhatsApp at least every 14 days, or WhatsApp logs the bot out. `webhook_token` must match `?token=` in WAHA's hook URL.

## Usage

Forward a voice message to the bot. Every message gets two passes:

1. **NVIDIA Parakeet v3** sends a quick transcript within seconds (25 European languages).
2. **Whisper large-v3-turbo** (faster-whisper) then replaces it with a more accurate one, especially on colloquial speech. It's loaded for each message and freed afterwards, so it only takes RAM (about 2 GB) while it works.

| Pass | 1 min message | 5 min message |
|---|---|---|
| Parakeet v3 | 3.5 s | 12.8 s |
| Whisper large-v3-turbo | 13.7 s | 48.8 s |

Measured on an Intel i5-12400 (6 cores, no GPU) with int8 models and German audio. Run both on your own files with `docker compose exec tldl tldl transcribe audio.ogg`.

## Development

```sh
uv venv && uv pip install -e '.[dev]' && .venv/bin/pytest
```

## Credits

Models are downloaded at runtime, not shipped. [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) by NVIDIA, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); used as an int8 ONNX conversion by [istupakov](https://huggingface.co/istupakov) (CC BY 4.0). [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) by OpenAI (MIT), as a CTranslate2 conversion by [Mobius Labs](https://huggingface.co/mobiuslabsgmbh) (MIT), run with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and [CTranslate2](https://github.com/OpenNMT/CTranslate2) (MIT). [Silero VAD](https://github.com/snakers4/silero-vad), [onnx-asr](https://github.com/istupakov/onnx-asr) and [ONNX Runtime](https://github.com/microsoft/onnxruntime): MIT. The Docker image runs Debian's [FFmpeg](https://ffmpeg.org) (GPL) as a separate program. Bridges run in their own containers: [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) (MIT), [signal-cli](https://github.com/AsamK/signal-cli) (GPL-3.0), [WAHA](https://github.com/devlikeapro/waha) (Apache-2.0).

## License

tldl is licensed under the [GNU Affero General Public License v3.0](LICENSE) or later. If you run a modified version for other people, offer them its source code; the bot's `/help` links to the source.
