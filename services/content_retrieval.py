from __future__ import annotations

import re
from utils.text import normalize_arabic

DEFAULT_CHUNK_CHARS = 6000
DEFAULT_OVERLAP_CHARS = 300


def _tokens(text: str) -> list[str]:
    normalized = normalize_arabic(text or '')
    return [x for x in re.findall(r'[a-z0-9+#.\-]+|[\u0600-\u06ff]+', normalized) if len(x) > 1]


def chunk_text(text: str, chunk_chars: int = DEFAULT_CHUNK_CHARS, overlap_chars: int = DEFAULT_OVERLAP_CHARS) -> list[str]:
    text = str(text or '').strip()
    if not text:
        return []
    chunk_chars = max(1000, int(chunk_chars))
    overlap_chars = max(0, min(int(overlap_chars), chunk_chars // 3))
    chunks = []
    start = 0
    length = len(text)
    while start < length:
        end = min(length, start + chunk_chars)
        if end < length:
            boundary = max(text.rfind('\n', start + chunk_chars // 2, end), text.rfind(' ', start + chunk_chars // 2, end))
            if boundary > start:
                end = boundary
        part = text[start:end].strip()
        if part:
            chunks.append(part)
        if end >= length:
            break
        next_start = end - overlap_chars
        if next_start <= start:
            next_start = end
        start = next_start
    return chunks


def retrieve_context(text: str, query: str | None = None, max_chars: int = 60000, *, chunk_chars: int = DEFAULT_CHUNK_CHARS) -> str:
    """Return bounded context while preserving coverage of long material.

    Short material is returned intact. Long material is split into chunks; a
    query retrieves the highest-overlap chunks, while non-query tasks sample
    chunks across the whole document instead of always truncating the beginning.
    """
    text = str(text or '').strip()
    max_chars = max(1000, int(max_chars))
    if len(text) <= max_chars:
        return text

    chunks = chunk_text(text, chunk_chars=chunk_chars)
    if not chunks:
        return text[:max_chars]

    selected: list[int]
    if query and query.strip():
        q_tokens = set(_tokens(query))
        q_norm = normalize_arabic(query)
        scored = []
        for idx, chunk in enumerate(chunks):
            norm = normalize_arabic(chunk)
            overlap = sum(1 for token in q_tokens if token in norm)
            phrase = 4 if q_norm and q_norm in norm else 0
            scored.append((overlap * 3 + phrase, idx))
        scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        budget = max(1, max_chars // max(1000, chunk_chars))
        selected = [idx for score, idx in scored[:budget] if score > 0]
        if not selected:
            selected = [0]
    else:
        count = max(1, max_chars // max(1000, chunk_chars))
        if count >= len(chunks):
            selected = list(range(len(chunks)))
        else:
            # Evenly sample the document so summaries/exams can see later sections.
            selected = sorted({round(i * (len(chunks) - 1) / max(1, count - 1)) for i in range(count)})

    selected_chunks = [chunks[i] for i in selected]
    out = []
    used = 0
    for idx, part in zip(selected, selected_chunks):
        marker = f'\n--- مقطع {idx + 1} ---\n'
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(marker) + len(part) > remaining:
            part = part[:max(0, remaining - len(marker))]
        if not part:
            continue
        out.append(marker + part)
        used += len(marker) + len(part)
    return ''.join(out).strip()[:max_chars]
