"""Helpers for media endpoints (images/audio).

The active MCP model returns text; artifacts may be URLs (e.g. pub.* URLs) or
Hyperagent-style placeholders [[IMAGE_xxx]] / [[AUDIO_xxx]]. The gateway extracts
fetchable URLs from the model reply and surfaces them as OpenAI-shaped image /
audio responses.

Ported from hyperagent-openai-gateway media.py.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any, Optional

URL_RE = re.compile(r'https?://[^\s)\]}"\'<>]+')
ARTIFACT_RE = re.compile(r'\[\[([A-Z]+)_([A-Za-z0-9]+)\]\]')

# Directive suffix appended to media prompts so the model returns a fetchable URL
# instead of an auth-gated artifact token.
PUBLISH_HINT = ("Then publish the file publicly and reply with ONLY the public "
                "https URL — no markdown, no artifact placeholder, no other text.")


def extract_urls(text: str) -> list[str]:
    return URL_RE.findall(text or "")


def extract_artifacts(text: str) -> list[str]:
    return [f"{t}_{i}" for t, i in ARTIFACT_RE.findall(text or "")]


def first_url(text: str) -> Optional[str]:
    urls = extract_urls(text)
    return urls[0] if urls else None


def images_response(urls: list[str], revised_prompt: Optional[str] = None,
                    n: int = 1) -> dict[str, Any]:
    data: list[dict[str, Any]] = []
    for u in urls[:n]:
        item: dict[str, Any] = {"url": u}
        if revised_prompt:
            item["revised_prompt"] = revised_prompt
        data.append(item)
    return {"created": int(time.time()), "data": data}


def transcription_response(text: str) -> dict[str, Any]:
    return {"text": text}


def speech_url_response(url: str, model: str) -> dict[str, Any]:
    # Documented deviation: OpenAI returns raw audio bytes; when the upstream
    # yields an artifact URL that cannot be byte-fetched, we return JSON with the
    # artifact URL instead. Real, fetchable URLs are streamed as bytes (see worker).
    return {
        "object": "audio.speech",
        "model": model,
        "url": url,
        "id": "speech_" + uuid.uuid4().hex[:12],
        "note": "Active model returned an artifact URL; fetch it to obtain bytes.",
    }
