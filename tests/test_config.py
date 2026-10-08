import pytest

from tldl.config import load_config


def write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text)
    return p


def test_env_defaults_and_disabled_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_TOKEN", "secret")
    c = load_config(write(tmp_path, """
engines:
  parakeet: {}
  canary: {model: "${CANARY:-nemo-canary-1b-v2}", language: de}
messengers:
  tg: {type: telegram, token: "${TG_TOKEN}"}
  wa: {type: whatsapp-waha, enabled: false, api_key: "${UNSET_VAR}"}
http: {port: "${PORT:-9000}"}
"""))
    assert c.default_engine == "parakeet"
    assert c.engines["parakeet"] == {"type": "onnx"}  # type defaults to onnx
    assert c.engines["canary"]["model"] == "nemo-canary-1b-v2"
    assert c.messengers == {"tg": {"type": "telegram", "token": "secret"}}
    assert c.http.port == 9000


def test_missing_env_var(tmp_path):
    with pytest.raises(ValueError, match="DEFINITELY_UNSET_123"):
        load_config(write(tmp_path, "engines:\n  a: {type: openai, api_key: '${DEFINITELY_UNSET_123}'}\n"))


def test_old_keys_are_explained(tmp_path):
    with pytest.raises(ValueError, match="'transcribers' was renamed to 'engines'"):
        load_config(write(tmp_path, "transcribers:\n  a: {}\n"))


def test_unknown_keys_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown keys"):
        load_config(write(tmp_path, "engines:\n  a: {}\ntypo_key: 1\n"))


def test_example_config_parses(monkeypatch):
    for var in ("TELEGRAM_BOT_TOKEN", "SIGNAL_NUMBER", "WAHA_API_KEY", "WEBHOOK_TOKEN"):
        monkeypatch.setenv(var, "x")
    c = load_config("config.example.yaml")
    assert c.default_engine in c.engines


def test_global_idle_unload(tmp_path):
    from tldl.core import build_engines

    c = load_config(write(tmp_path, """
idle_unload: 600
engines:
  a: {}
  b: {idle_unload: 60}
  c: {idle_unload: null}
"""))
    engines = build_engines(c)
    assert [engines[n].idle_unload for n in "abc"] == [600, 60, None]


def test_health_alert_must_name_a_messenger(tmp_path):
    with pytest.raises(ValueError, match="health.alert"):
        load_config(write(tmp_path, """
engines: {a: {}}
messengers: {tg: {type: telegram, token: x}}
health: {alert: {messenger: telgram, chat_id: "1"}}
"""))
    c = load_config(write(tmp_path, """
engines: {a: {}}
messengers: {tg: {type: telegram, token: x}}
health: {interval: 60, alert: {messenger: tg, chat_id: 1}}
"""))
    assert c.health.interval == 60 and c.health.alert["messenger"] == "tg"
