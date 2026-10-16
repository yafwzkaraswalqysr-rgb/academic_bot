from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')

def _int(name: str, default: int = 0) -> int:
    try: return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError): return default

def _ints(name: str) -> list[int]:
    out=[]
    for x in os.getenv(name,'').split(','):
        try:
            if x.strip(): out.append(int(x.strip()))
        except ValueError: pass
    return out

BOT_TOKEN=os.getenv('BOT_TOKEN','').strip()
OWNER_ID=_int('OWNER_ID')
ADMIN_IDS=_ints('ADMIN_IDS')
if OWNER_ID and OWNER_ID not in ADMIN_IDS: ADMIN_IDS.insert(0, OWNER_ID)

TELEGRAM_API_ID=_int('TELEGRAM_API_ID')
TELEGRAM_API_HASH=os.getenv('TELEGRAM_API_HASH','').strip()
TELEGRAM_SESSION=os.getenv('TELEGRAM_SESSION', os.getenv('TELEGRAM_SESSION_STRING','')).strip()

DATABASE_URL=os.getenv('DATABASE_URL',f"sqlite+aiosqlite:///{(BASE_DIR / 'bot.db').as_posix()}").strip()
if DATABASE_URL.startswith('sqlite:///') and '+aiosqlite' not in DATABASE_URL:
    DATABASE_URL=DATABASE_URL.replace('sqlite:///', 'sqlite+aiosqlite:///', 1)
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL=DATABASE_URL.replace('postgres://','postgresql+asyncpg://',1)
elif DATABASE_URL.startswith('postgresql://') and '+asyncpg' not in DATABASE_URL:
    DATABASE_URL=DATABASE_URL.replace('postgresql://','postgresql+asyncpg://',1)

GEMINI_API_KEY=os.getenv('GEMINI_API_KEY','').strip()
# gemini-2.5-flash is restricted for some newly created API projects.
# Use the current stable production model by default and transparently
# migrate the old 2.5 setting so stale .env files cannot break the bot.
GEMINI_DEFAULT_MODEL='gemini-3.6-flash'
_raw_gemini_model=os.getenv('GEMINI_MODEL','').strip()
if not _raw_gemini_model or _raw_gemini_model in {'gemini-2.5-flash','gemini-2.5-flash-lite','gemini-2.5-pro'}:
    GEMINI_MODEL=GEMINI_DEFAULT_MODEL
else:
    GEMINI_MODEL=_raw_gemini_model
SEARCH_TIMEOUT=max(1, _int('SEARCH_TIMEOUT',15))
HTTP_TIMEOUT=max(1, _int('HTTP_TIMEOUT',15))
MAX_WEB_RESPONSE_BYTES=max(256*1024, _int('MAX_WEB_RESPONSE_BYTES',5*1024*1024))
MAX_FILE_SIZE=max(1, _int('MAX_FILE_SIZE',50*1024*1024))
RATE_LIMIT_PER_MINUTE=max(1, _int('RATE_LIMIT_PER_MINUTE',10))
SEARCH_CONCURRENCY=max(1, _int('SEARCH_CONCURRENCY',8))
# Internal safety budget only; never shown as a user-facing limit.
INTERNAL_RESULT_LIMIT=max(10, _int('INTERNAL_RESULT_LIMIT',200))
RESULTS_PER_PAGE=max(1, _int('RESULTS_PER_PAGE',10))
UPLOADS_DIR=Path(os.getenv('UPLOADS_DIR',str(BASE_DIR/'uploads')))
TEMP_DIR=Path(os.getenv('TEMP_DIR',str(BASE_DIR/'temp')))
UPLOADS_DIR.mkdir(parents=True, exist_ok=True); TEMP_DIR.mkdir(parents=True, exist_ok=True)
SUPPORTED_EXTENSIONS={'.pdf','.docx','.pptx','.txt'}
STUDY_LEVELS=['أولى','ثانية','ثالثة','رابعة','خامسة','سادسة','دراسات عليا','غير محدد']
SEARCH_SCOPES=['عالمية','عربية','عراقية']
DATE_RANGES=['كل الفترات','آخر سنة','آخر 3 سنوات','آخر 5 سنوات']
# User-facing source count choices. Custom values are silently bounded by INTERNAL_RESULT_LIMIT.
RESULT_LIMITS=[10,20,50]
SEARCH_MODES={'⚡ سريع':'fast','🔬 عميق':'deep'}
REFERENCE_STYLES=['APA','MLA','Chicago','IEEE']
MAX_QUERY_LENGTH=500
MAX_STUDY_TEXT_CHARS=max(10000, _int('MAX_STUDY_TEXT_CHARS',10_000_000))

AI_TIMEOUT = max(1, _int('AI_TIMEOUT', 60))
AI_QUIZ_BATCH_SIZE = max(1, _int('AI_QUIZ_BATCH_SIZE', 20))
AI_SOURCE_CHARS = max(1000, _int('AI_SOURCE_CHARS', 60000))
AI_ASK_CHARS = max(1000, _int('AI_ASK_CHARS', 60000))
# Internal safety budget only; user requests may exceed it but are handled up to this budget.
INTERNAL_AI_ITEM_LIMIT=max(10, _int('INTERNAL_AI_ITEM_LIMIT',200))
FILE_PROCESS_TIMEOUT=max(30, _int('FILE_PROCESS_TIMEOUT',180))
CACHE_MAX_ENTRIES=max(50, _int('CACHE_MAX_ENTRIES',500))
CACHE_TTL_SECONDS=max(1, _int('CACHE_TTL_SECONDS',600))
LOG_LEVEL=os.getenv('LOG_LEVEL','INFO').upper()
