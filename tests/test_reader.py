"""Pure-code tests for the Reader (receipts.reader).

Patches urllib.request.urlopen and the file system — no real network, no
real image files. Covers Spec §6, §7, §8, §9, §12, §14.
"""
import base64
import json
from unittest.mock import patch, mock_open

import pytest
from urllib.error import URLError

from receipts.checker import Receipt
from receipts.reader import ReadError, read


# ---------- fixtures & helpers ----------

class FakeResponse:
    """Mimics urllib's addinfourl: context manager with .read() returning bytes."""

    def __init__(self, status: int, body: str | bytes):
        self.status = status
        self._body = body.encode() if isinstance(body, str) else body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def envelope(content: str) -> str:
    """Wrap model output in an OpenRouter chat-completions response envelope."""
    return json.dumps({"choices": [{"message": {"content": content}}]})


VALID_CONTENT = json.dumps({
    "merchant": "Test Cafe",
    "date": "2026-10-01",
    "items": [{"name": "Coffee", "quantity": 1, "unit_price": 5.00}],
    "subtotal": 5.00,
    "discount": 0.0,
    "tax": 0.0,
    "total": 5.00,
})


@pytest.fixture
def fake_image(monkeypatch):
    """Mock the file system so the Reader sees a small fake PNG."""
    fake_bytes = b"\x89PNG\r\n\x1a\nfake-receipt-content"
    monkeypatch.setattr("builtins.open", mock_open(read_data=fake_bytes))
    return fake_bytes


@pytest.fixture
def env(monkeypatch):
    """Default test env: key set, no model/reasoning overrides."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("CHAT_MODEL", raising=False)
    monkeypatch.delenv("CHAT_REASONING", raising=False)


def captured_urlopen(response_status: int, response_body: str):
    """Return a (captured_dict, patch_ctx) pair: the ctx installs urlopen, the
    dict records the Request body that was sent. Tests inspect captured_dict."""
    captured = {}

    def fake_urlopen(req, **kwargs):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode())
        return FakeResponse(response_status, response_body)

    return captured, patch("receipts.reader.urlopen", side_effect=fake_urlopen)


# ---------- happy path ----------

def test_read_success_returns_receipt(env, fake_image):
    """A valid OpenRouter response with valid JSON content → Receipt."""
    captured, ctx = captured_urlopen(200, envelope(VALID_CONTENT))
    with ctx:
        result = read("fake.png")

    assert isinstance(result, Receipt)
    assert result.merchant == "Test Cafe"
    assert result.date == "2026-10-01"
    assert len(result.items) == 1
    assert result.items[0].name == "Coffee"
    assert result.total == 5.00


def test_read_sends_image_as_base64_data_url(env, fake_image):
    """The Reader base64-encodes the image bytes and includes them in the request."""
    captured, ctx = captured_urlopen(200, envelope(VALID_CONTENT))
    with ctx:
        read("fake.png")

    # The OpenRouter request body should contain a user message with a list content
    # that includes an image_url whose url starts with "data:image/...;base64,".
    content = captured["body"]["messages"][1]["content"]  # messages[1] = user
    image_part = next(p for p in content if p.get("type") == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    payload = image_part["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(payload) == fake_image


def test_read_sends_to_openrouter_endpoint(env, fake_image):
    """The POST goes to https://openrouter.ai/api/v1/chat/completions."""
    captured, ctx = captured_urlopen(200, envelope(VALID_CONTENT))
    with ctx:
        read("fake.png")
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"


# ---------- Spec §9: missing API key ----------

def test_read_no_api_key_returns_read_error(monkeypatch, fake_image):
    """OPENROUTER_API_KEY unset → ReadError('no_api_key'), no HTTP call."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with patch("receipts.reader.urlopen") as mock_urlopen:
        result = read("fake.png")
    assert isinstance(result, ReadError)
    assert result.reason == "no_api_key"
    mock_urlopen.assert_not_called()


# ---------- Spec §6: not JSON ----------

def test_read_not_json_returns_read_error(env, fake_image):
    """Model reply that is not JSON → ReadError('not_json'), not a crash."""
    captured, ctx = captured_urlopen(200, envelope("this is not JSON at all"))
    with ctx:
        result = read("fake.png")
    assert isinstance(result, ReadError)
    assert result.reason == "not_json"
    assert "not JSON" in (result.detail or "")


def test_read_markdown_fenced_json_parses_cleanly(env, fake_image):
    """If the model wraps JSON in ```json fences, the Reader strips them."""
    fenced = "```json\n" + VALID_CONTENT + "\n```"
    captured, ctx = captured_urlopen(200, envelope(fenced))
    with ctx:
        result = read("fake.png")
    assert isinstance(result, Receipt), f"expected Receipt, got {result!r}"


# ---------- Spec §7: empty / refusal ----------

def test_read_empty_content_returns_read_error(env, fake_image):
    """choices[0].message.content is empty string → ReadError('empty')."""
    captured, ctx = captured_urlopen(200, envelope(""))
    with ctx:
        result = read("fake.png")
    assert isinstance(result, ReadError)
    assert result.reason == "empty"


def test_read_refusal_returns_read_error(env, fake_image):
    """Content starting with a refusal marker → ReadError('refusal')."""
    captured, ctx = captured_urlopen(200, envelope("I'm sorry, I can't read that image."))
    with ctx:
        result = read("fake.png")
    assert isinstance(result, ReadError)
    assert result.reason == "refusal"


# ---------- Spec §8: HTTP / network ----------

def test_read_http_error_returns_read_error(env, fake_image):
    """Non-200 status → ReadError('http', status, body excerpt)."""
    captured, ctx = captured_urlopen(401, "Unauthorized")
    with ctx:
        result = read("fake.png")
    assert isinstance(result, ReadError)
    assert result.reason == "http"
    assert "401" in (result.detail or "")


def test_read_network_error_returns_read_error(env, fake_image):
    """URLError from urlopen → ReadError('network')."""
    with patch("receipts.reader.urlopen", side_effect=URLError("connection refused")):
        result = read("fake.png")
    assert isinstance(result, ReadError)
    assert result.reason == "network"
    assert "connection refused" in (result.detail or "")


# ---------- Spec §5: missing field ----------

def test_read_missing_merchant_returns_read_error(env, fake_image):
    """JSON missing merchant → ReadError('missing_field', 'merchant')."""
    no_merchant = json.dumps({
        "date": "2026-10-01",
        "items": [{"name": "x", "quantity": 1, "unit_price": 5.00}],
        "subtotal": 5.00,
        "discount": 0.0,
        "tax": 0.0,
        "total": 5.00,
    })
    captured, ctx = captured_urlopen(200, envelope(no_merchant))
    with ctx:
        result = read("fake.png")
    assert isinstance(result, ReadError)
    assert result.reason == "missing_field"
    assert result.detail == "merchant"


# ---------- Spec §12: null date ----------

def test_read_null_date_accepted(env, fake_image):
    """date: null → Receipt with date=None."""
    no_date = json.loads(VALID_CONTENT)
    no_date["date"] = None
    captured, ctx = captured_urlopen(200, envelope(json.dumps(no_date)))
    with ctx:
        result = read("fake.png")
    assert isinstance(result, Receipt)
    assert result.date is None


# ---------- Spec §14: model + reasoning selection ----------

def test_read_uses_default_model_when_chat_model_unset(env, fake_image):
    """No CHAT_MODEL env var → request body uses 'xiaomi/mimo-v2.6-flash' (the cheapest-passing default per Spec §14)."""
    captured, ctx = captured_urlopen(200, envelope(VALID_CONTENT))
    with ctx:
        read("fake.png")
    assert captured["body"]["model"] == "xiaomi/mimo-v2.6-flash"


def test_read_honors_chat_model_override(monkeypatch, fake_image):
    """CHAT_MODEL=xiaomi/mimo-v2.6-flash → request body uses that model."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("CHAT_MODEL", "xiaomi/mimo-v2.6-flash")
    captured, ctx = captured_urlopen(200, envelope(VALID_CONTENT))
    with ctx:
        read("fake.png")
    assert captured["body"]["model"] == "xiaomi/mimo-v2.6-flash"


def test_read_minimax_reasoning_enabled_by_default(monkeypatch, env, fake_image):
    """CHAT_MODEL=minimax/minimax-m3 → reasoning.enabled = True (per-model table default)."""
    monkeypatch.setenv("CHAT_MODEL", "minimax/minimax-m3")
    captured, ctx = captured_urlopen(200, envelope(VALID_CONTENT))
    with ctx:
        read("fake.png")
    assert captured["body"]["model"] == "minimax/minimax-m3"
    assert captured["body"]["reasoning"]["enabled"] is True


def test_read_default_xiaomi_has_reasoning_disabled(env, fake_image):
    """Default model is now xiaomi, which has reasoning disabled in the per-model table."""
    captured, ctx = captured_urlopen(200, envelope(VALID_CONTENT))
    with ctx:
        read("fake.png")
    assert captured["body"]["model"] == "xiaomi/mimo-v2.6-flash"
    assert captured["body"]["reasoning"]["enabled"] is False


def test_read_chat_reasoning_off_disables_minimax_reasoning(monkeypatch, fake_image):
    """CHAT_REASONING=off + model=minimax → reasoning.enabled = False."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("CHAT_MODEL", "minimax/minimax-m3")
    monkeypatch.setenv("CHAT_REASONING", "off")
    captured, ctx = captured_urlopen(200, envelope(VALID_CONTENT))
    with ctx:
        read("fake.png")
    assert captured["body"]["model"] == "minimax/minimax-m3"
    assert captured["body"]["reasoning"]["enabled"] is False


def test_read_xiaomi_reasoning_disabled_always(monkeypatch, fake_image):
    """Model=xiaomi (with or without CHAT_REASONING) → reasoning.enabled = False
    even if the user tries to enable it. (Forcing reasoning on xiaomi breaks it.)"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("CHAT_MODEL", "xiaomi/mimo-v2.6-flash")
    monkeypatch.setenv("CHAT_REASONING", "on")  # user tries to enable
    captured, ctx = captured_urlopen(200, envelope(VALID_CONTENT))
    with ctx:
        read("fake.png")
    assert captured["body"]["model"] == "xiaomi/mimo-v2.6-flash"
    assert captured["body"]["reasoning"]["enabled"] is False
