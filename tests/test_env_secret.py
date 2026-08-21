import os

from bot import env_secret


def test_env_secret_rejects_placeholders(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
    monkeypatch.setenv("UPSTOX_API_KEY", "YOUR_API_KEY")
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "YOUR_ACCESS_TOKEN")
    assert env_secret("TELEGRAM_TOKEN") == ""
    assert env_secret("TELEGRAM_CHAT_ID") == ""
    assert env_secret("UPSTOX_API_KEY") == ""
    assert env_secret("UPSTOX_ACCESS_TOKEN") == ""


def test_env_secret_accepts_real_looking_values(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123456:ABC-real-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    assert env_secret("TELEGRAM_TOKEN") == "123456:ABC-real-token"
    assert env_secret("TELEGRAM_CHAT_ID") == "987654321"


def test_env_secret_strips_quotes_and_space(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", '  "123456:ABC"  ')
    assert env_secret("TELEGRAM_TOKEN") == "123456:ABC"


def test_env_secret_empty(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    assert env_secret("MISSING_KEY") == ""
    monkeypatch.setenv("MISSING_KEY", "   ")
    assert env_secret("MISSING_KEY") == ""
