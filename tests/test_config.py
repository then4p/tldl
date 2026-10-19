import pytest

from tldl.config import load_config


def write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text)
    return p


def test_env_defaults_and_disabled_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_TOKEN", "secret")
    c = load_config(write(tmp_path, """
threads: "${THREADS:-6}"
messengers:
  tg: {type: telegram, token: "${TG_TOKEN}"}
  wa: {type: whatsapp-waha, enabled: false, api_key: "${UNSET_VAR}"}
http: {port: "${PORT:-9000}"}
"""))
    assert c.messengers == {"tg": {"type": "telegram", "token": "secret"}}
    assert c.http.port == 9000 and c.threads == "6"


def test_missing_env_var(tmp_path):
    with pytest.raises(ValueError, match="DEFINITELY_UNSET_123"):
        load_config(write(tmp_path, "messengers:\n  tg: {type: telegram, token: '${DEFINITELY_UNSET_123}'}\n"))


def test_unknown_keys_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown keys"):
        load_config(write(tmp_path, "engines:\n  a: {}\n"))


def test_example_config_parses(monkeypatch):
    for var in ("TELEGRAM_BOT_TOKEN", "SIGNAL_NUMBER", "WAHA_API_KEY", "WEBHOOK_TOKEN"):
        monkeypatch.setenv(var, "x")
    c = load_config("config.example.yaml")
    assert "telegram" in c.messengers


def test_health_alert_must_name_a_messenger(tmp_path):
    with pytest.raises(ValueError, match="health.alert"):
        load_config(write(tmp_path, """
messengers: {tg: {type: telegram, token: x}}
health: {alert: {messenger: telgram, chat_id: "1"}}
"""))
    c = load_config(write(tmp_path, """
messengers: {tg: {type: telegram, token: x}}
health: {interval: 60, alert: {messenger: tg, chat_id: 1}}
"""))
    assert c.health.interval == 60 and c.health.alert["messenger"] == "tg"
