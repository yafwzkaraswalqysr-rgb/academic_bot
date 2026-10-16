import re
import unicodedata

ARABIC_DIACRITICS = re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670]')
ARABIC_NORMALIZATION = str.maketrans({'أ':'ا','إ':'ا','آ':'ا','ى':'ي','ة':'ه','ؤ':'و','ئ':'ي','ـ':''})


def normalize_arabic(text):
    if not text:
        return ''
    text = ARABIC_DIACRITICS.sub('', unicodedata.normalize('NFKC', str(text))).translate(ARABIC_NORMALIZATION)
    return ' '.join(text.split()).lower().strip()


def normalize_query(q):
    return ' '.join(unicodedata.normalize('NFKC', str(q or '')).split()).strip()


def extract_year(text):
    m = re.search(r'\b(?:19|20)\d{2}\b', str(text or ''))
    return m.group(0) if m else None


def is_arabic(text):
    if not text:
        return False
    n = sum('\u0600' <= c <= '\u06ff' for c in text)
    return n / max(1, len(text)) > .3


def clean_ai_text(text: str) -> str:
    """Convert common LaTeX/Markdown artifacts to Telegram-friendly plain text."""
    t = str(text or '').strip()
    if not t:
        return ''
    # Remove code fences but preserve their contents.
    t = re.sub(r'^\s*```(?:json|text|markdown)?\s*', '', t, flags=re.I)
    t = re.sub(r'\s*```\s*$', '', t)
    # Repeatedly simplify common LaTeX wrappers before symbol conversion.
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r'\\(?:text|mathrm|mathbf|mathit)\s*\{([^{}]*)\}', r'\1', t)
    # Repeatedly simplify simple fractions: \frac{a}{b} -> a ÷ b.
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r'\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}', r'\1 ÷ \2', t)
    replacements = {
        r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ', r'\delta': 'δ',
        r'\theta': 'θ', r'\lambda': 'λ', r'\mu': 'μ', r'\pi': 'π',
        r'\sigma': 'σ', r'\rho': 'ρ', r'\phi': 'φ', r'\omega': 'ω',
        r'\Delta': 'Δ', r'\Sigma': 'Σ', r'\Omega': 'Ω',
        r'\infty': '∞', r'\degree': '°', r'\circ': '°',
        r'\times': ' × ', r'\cdot': ' × ', r'\div': ' ÷ ', r'\pm': ' ± ',
        r'\leq': ' ≤ ', r'\geq': ' ≥ ', r'\neq': ' ≠ ', r'\approx': ' ≈ ',
        r'\rightarrow': ' → ', r'\left': '', r'\right': '', r'\,': ' ',
        r'\;': ' ', r'\!': '',
    }
    for pattern, repl in replacements.items():
        t = t.replace(pattern, repl)
    t = re.sub(r'\$\$?', '', t)
    t = re.sub(r'\\sqrt\s*\{([^{}]+)\}', r'√(\1)', t)
    # Common lightweight Markdown that clashes with Telegram HTML mode.
    t = t.replace('**', '').replace('__', '')
    t = re.sub(r'`([^`]+)`', r'\1', t)
    t = re.sub(r'^\s*#{1,6}\s*', '', t, flags=re.M)
    t = t.replace('\\_', '_')
    t = re.sub(r'[ \t]{2,}', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()
