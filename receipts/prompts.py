"""Prompts and request-body construction for the OpenRouter vision call.

The system prompt encodes the JSON schema and the date-as-null rule (Spec §12).
The user prompt is short — the schema does the heavy lifting.
"""
from __future__ import annotations

import base64


SYSTEM_PROMPT = """\
You are a receipt parser. Extract the receipt image as JSON with EXACTLY this shape:

{
  "merchant": string,
  "date": string or null,                 // ISO "YYYY-MM-DD"; null if the year is not printed
  "items": [
    {"name": string, "quantity": integer, "unit_price": number}
  ],
  "subtotal": number,
  "discount": number,                     // 0 if not present on the receipt
  "tax": number,                          // 0 if not present on the receipt
  "total": number
}

Rules:
- If the receipt does NOT print a year, set "date" to null. Do NOT guess a year.
- If a field is not present on the receipt, use 0 for numbers or [] for items.
- Return ONLY the JSON object — no prose, no markdown, no code fences.
"""


USER_PROMPT = "Extract the receipt from this image as JSON."


def build_request_body(image_bytes: bytes, model: str, reasoning_enabled: bool) -> dict:
    """Construct the JSON body for POST /v1/chat/completions."""
    image_b64 = base64.b64encode(image_bytes).decode()
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": USER_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    },
                ],
            },
        ],
        "reasoning": {"enabled": reasoning_enabled},
    }
