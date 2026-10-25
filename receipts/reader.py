"""The Reader component: vision model call + JSON parse.

Calls OpenRouter's chat-completions endpoint with a base64-encoded image,
parses the reply into a Receipt, and maps every failure mode to a typed
ReadError. This is the only component that touches the network or a model.

Failure-mode mapping (Spec §6-§9, §7, §8, §12, §14):
    no_api_key   — OPENROUTER_API_KEY not set
    network      — URLError, TimeoutError, OSError
    http         — non-200 HTTP status
    empty        — choices[0].message.content is empty / whitespace
    refusal      — content starts with a refusal marker ("I can't", "I'm sorry", ...)
    not_json     — content is not parseable JSON (after stripping markdown fences)
    missing_field — JSON is valid but is missing merchant / items / subtotal / total
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from receipts.checker import Item, Receipt
from receipts.prompts import build_request_body


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "xiaomi/mimo-v2.6-flash"   # cheapest-passing per Spec §14 / Cost-estimate
REQUEST_TIMEOUT_SECONDS = 30

# Per-model reasoning defaults. CHAT_REASONING=off can suppress reasoning where
# it does not break the model; it CANNOT enable reasoning on xiaomi (which
# would exhaust its output budget — see intent, Constraints, Model).
_REASONING_TABLE: dict[str, bool] = {
    "minimax/minimax-m3": True,
    "xiaomi/mimo-v2.6-flash": False,
}

REFUSAL_MARKERS = (
    "i'm sorry",
    "i cant",
    "i cannot",
    "i apologize",
    "i won't",
    "as an ai",
    "sorry,",
)


@dataclass(frozen=True)
class ReadError:
    reason: str  # no_api_key | network | http | empty | not_json | missing_field | refusal
    detail: str | None = None


# ---------- env / model helpers ----------

def _resolve_model() -> str:
    return os.environ.get("CHAT_MODEL", DEFAULT_MODEL)


def _resolve_reasoning(model: str) -> bool:
    """Per-model reasoning flag.

    - Per-model table sets the baseline (minimax → enabled, xiaomi → disabled).
    - CHAT_REASONING=off can DISABLE reasoning (safe on minimax, no-op on xiaomi).
    - CHAT_REASONING=on does NOT override — it cannot enable reasoning on xiaomi
      because doing so would break that model. Unknown models default to enabled.
    """
    base = _REASONING_TABLE.get(model, True)
    env_value = os.environ.get("CHAT_REASONING", "").lower().strip()
    if env_value == "off":
        return False
    return base


# ---------- response post-processing ----------

def _strip_markdown_fence(text: str) -> str:
    """If the model wraps JSON in ```json ... ```, return the inner content."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    first_newline = text.find("\n")
    if first_newline == -1:
        return text
    end = text.rfind("```")
    if end <= first_newline:
        return text
    return text[first_newline + 1:end].strip()


def _is_refusal(content: str) -> bool:
    lowered = content.strip().lower()
    if not lowered:
        return False
    return any(lowered.startswith(m) for m in REFUSAL_MARKERS)


def _parse_receipt_json(content: str) -> Receipt | ReadError:
    text = _strip_markdown_fence(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ReadError("not_json", detail=text[:200])

    if not isinstance(data, dict):
        return ReadError("not_json", detail=f"expected object, got {type(data).__name__}")

    for field in ("merchant", "items", "subtotal", "total"):
        if field not in data or data[field] is None:
            return ReadError("missing_field", detail=field)

    discount = data.get("discount") or 0.0
    tax = data.get("tax") or 0.0

    items = [
        Item(
            name=str(i["name"]),
            quantity=int(i["quantity"]),
            unit_price=float(i["unit_price"]),
        )
        for i in data["items"]
    ]

    return Receipt(
        merchant=str(data["merchant"]),
        date=data.get("date"),  # may be null per Spec §12
        items=items,
        subtotal=float(data["subtotal"]),
        discount=float(discount),
        tax=float(tax),
        total=float(data["total"]),
    )


# ---------- main entry point ----------

def read(image_path: str) -> Receipt | ReadError:
    """Read a receipt image and return a Receipt or a structured ReadError."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return ReadError("no_api_key")

    model = _resolve_model()
    reasoning_enabled = _resolve_reasoning(model)

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    body = build_request_body(image_bytes, model, reasoning_enabled)
    body_bytes = json.dumps(body).encode()

    req = Request(
        OPENROUTER_URL,
        data=body_bytes,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    # --- HTTP ---
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            status = resp.status
            response_bytes = resp.read()
    except HTTPError as e:
        body_excerpt = ""
        try:
            body_excerpt = e.read()[:200].decode(errors="replace")
        except Exception:
            pass
        return ReadError("http", detail=f"status {e.code}: {body_excerpt}")
    except URLError as e:
        return ReadError("network", detail=str(e.reason))
    except (TimeoutError, OSError) as e:
        return ReadError("network", detail=str(e))

    if status != 200:
        return ReadError("http", detail=f"status {status}: {response_bytes[:200].decode(errors='replace')}")

    # --- envelope -> content ---
    try:
        envelope = json.loads(response_bytes.decode())
        content = envelope["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return ReadError("not_json", detail="OpenRouter response not in expected shape")

    if content is None or not str(content).strip():
        return ReadError("empty", detail="model returned empty content")

    content_str = str(content)

    # --- refusal detection ---
    if _is_refusal(content_str):
        return ReadError("refusal", detail=content_str[:200])

    # --- JSON parse + Receipt build ---
    return _parse_receipt_json(content_str)
