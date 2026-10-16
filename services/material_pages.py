from __future__ import annotations

import re

_PAGE_RE = re.compile(r"(?m)^\s*---\s*الصفحة\s+(\d+)\s*---\s*$")
_ARABIC_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')


def page_markers(text: str) -> list[tuple[int, int, int]]:
    """Return (page_number, start, end) marker offsets in a material text."""
    return [(int(m.group(1)), m.start(), m.end()) for m in _PAGE_RE.finditer(str(text or ''))]


def page_count(text: str) -> int:
    markers = page_markers(text)
    return max((p for p, _, _ in markers), default=0)


def has_pages(text: str) -> bool:
    return bool(page_markers(text))


def parse_page_range(value: str, max_page: int | None = None) -> tuple[int, int] | None:
    """Parse user input such as '5-12', '5 إلى 12', or '5 الى 12'."""
    raw = str(value or '').translate(_ARABIC_DIGITS).strip()
    raw = re.sub(r'\s+', ' ', raw)
    match = re.fullmatch(r'(\d+)\s*(?:-|–|—|إلى|الى)\s*(\d+)', raw, flags=re.IGNORECASE)
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if start < 1 or end < start:
        return None
    if max_page is not None and end > max_page:
        return None
    return start, end


def slice_pages(text: str, start: int, end: int) -> str:
    """Return only material between inclusive PDF page markers."""
    source = str(text or '').strip()
    markers = page_markers(source)
    if not markers:
        return source
    pieces: list[str] = []
    for index, (page_no, marker_start, marker_end) in enumerate(markers):
        if not (start <= page_no <= end):
            continue
        next_start = markers[index + 1][1] if index + 1 < len(markers) else len(source)
        body = source[marker_end:next_start].strip()
        if body:
            pieces.append(f'--- الصفحة {page_no} ---\n{body}')
    return '\n\n'.join(pieces).strip()


def describe_range(text: str) -> str:
    count = page_count(text)
    return f'1–{count}' if count else 'كامل المادة'
