from __future__ import annotations
import json
import re


def clean_json(text):
    """Extract a JSON object/array even when Gemini surrounds it with prose/fences."""
    if not text:
        return None
    t = str(text).strip()
    t = re.sub(r'^\s*```(?:json)?\s*', '', t, flags=re.I)
    t = re.sub(r'\s*```\s*$', '', t)
    try:
        return json.loads(t)
    except Exception:
        pass
    decoder = json.JSONDecoder()
    # Scan for the first valid object/array; malformed earlier text is ignored.
    for i, ch in enumerate(t):
        if ch not in '[{':
            continue
        try:
            obj, end = decoder.raw_decode(t[i:])
            if isinstance(obj, (dict, list)):
                return obj
        except Exception:
            continue
    return None
