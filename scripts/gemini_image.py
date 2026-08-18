#!/usr/bin/env python3
"""Shared Gemini image-generation machinery — the single call path.

Factored out of generate_banners.py so the banner, scene and object generators
all issue requests through one function. Adding a fourth generator must mean
importing this, never copying a payload builder.

What lives here:
  - the aspect-ratio table Gemini's imageConfig accepts, and nearest_aspect()
  - generate(), which takes N reference images (0 = pure text-to-image) and
    returns PNG bytes
  - re-exports of the retry shell and budget counter from
    generate_owner_images, so callers need exactly one import

The retry shell itself stays in generate_owner_images.py — it is the original
home and other scripts already import it from there; moving it would churn
call sites for no gain.
"""

import base64
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_owner_images import (  # noqa: E402,F401  -- re-exported on purpose
    _post_with_retries, bump_budget, load_budget,
)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3-pro-image"

# Values Gemini's imageConfig.aspectRatio accepts. A generation that omits this
# defaults to square, so callers should always pass one explicitly.
SUPPORTED_ASPECTS = {
    "1:1": 1.0, "3:2": 1.5, "2:3": 2 / 3, "3:4": 0.75, "4:3": 4 / 3,
    "4:5": 0.8, "5:4": 1.25, "9:16": 9 / 16, "16:9": 16 / 9, "21:9": 21 / 9,
}

# Character-consistency reference slots by model. Exceeding them is a 400, so
# callers check before spending a call.
MAX_REFS = {"gemini-3-pro-image": 5, "gemini-3.1-flash-image": 4}


def nearest_aspect(w: int, h: int) -> str:
    """Closest supported aspect string to a source image's own ratio."""
    r = w / h
    return min(SUPPORTED_ASPECTS, key=lambda k: abs(SUPPORTED_ASPECTS[k] - r))


def ref_limit(model: str) -> int:
    return MAX_REFS.get(model, 4)


def generate(api_key: str, model: str, refs, prompt: str, aspect: str,
             timeout: int = 420) -> bytes:
    """Return PNG bytes for one image.

    refs: list of (bytes, mime_type) reference images, in order. Order is
    load-bearing — prompts address references by index ("reference image #1"),
    because the model has no way to map a name onto an image. Pass [] for a
    pure text-to-image plate.
    """
    if aspect not in SUPPORTED_ASPECTS:
        raise ValueError(f"unsupported aspect {aspect!r}; "
                         f"pick one of {', '.join(SUPPORTED_ASPECTS)}")
    limit = ref_limit(model)
    if len(refs) > limit:
        raise ValueError(f"{len(refs)} references exceeds the {limit} character "
                         f"slots on {model}")

    parts = [{"inline_data": {"mime_type": m,
                              "data": base64.b64encode(b).decode()}}
             for b, m in refs]
    parts.append({"text": prompt})
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": aspect},
        },
    }
    resp = _post_with_retries(lambda: requests.post(
        f"{GEMINI_BASE}/{model}:generateContent", json=payload,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        timeout=timeout))
    data = resp.json()
    try:
        rparts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        # promptFeedback carries the block reason when a prompt is refused;
        # surfacing it beats a bare KeyError at the call site.
        raise RuntimeError(f"No candidate. promptFeedback={data.get('promptFeedback')}")
    for part in rparts:
        blob = part.get("inlineData") or part.get("inline_data")
        if blob and blob.get("data"):
            return base64.b64decode(blob["data"])
    texts = " ".join(p.get("text", "") for p in rparts).strip()
    raise RuntimeError(f"No image in response. Model said: {texts[:400]}")
