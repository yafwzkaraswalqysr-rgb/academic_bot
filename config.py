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
GEMINI_MODEL=os.getenv('GEMINI_MODEL','gemini-2.5-flash').strip()
SEARCH_TIMEOUT=_int('SEARCH_TIMEOUT',15)
HTTP_TIMEOUT=_int('HTTP_TIMEOUT',15)
MAX_FILE_SIZE=_int('MAX_FILE_SIZE',50*1024*1024)
RATE_LIMIT_PER_MINUTE=_int('RATE_LIMIT_PER_MINUTE',10)
SEARCH_CONCURRENCY=_int('SEARCH_CONCURRENCY',8)
RESULTS_PER_PAGE=_int('RESULTS_PER_PAGE',10)
UPLOADS_DIR=Path(os.getenv('UPLOADS_DIR',str(BASE_DIR/'uploads')))
TEMP_DIR=Path(os.getenv('TEMP_DIR',str(BASE_DIR/'temp')))
UPLOADS_DIR.mkdir(parents=True, exist_ok=True); TEMP_DIR.mkdir(parents=True, exist_ok=True)
SUPPORTED_EXTENSIONS={'.pdf','.docx','.pptx','.txt'}
STUDY_LEVELS=['أولى','ثانية','ثالثة','رابعة','خامسة','سادسة','دراسات عليا','غير محدد']
SEARCH_SCOPES=['عالمية','عربية','عراقية']
DATE_RANGES=['كل الفترات','آخر سنة','آخر 3 سنوات','آخر 5 سنوات']
# 0 = غير محدود (كل النتائج التي يعيدها المصدر ضمن حدوده التقنية)
RESULT_LIMITS=[5,10,20,50,0]
SEARCH_MODES={'⚡ سريع':'fast','🔬 عميق':'deep'}
REFERENCE_STYLES=['APA','MLA','Chicago','IEEE']
MAX_QUERY_LENGTH=500

AI_TIMEOUT = _int('AI_TIMEOUT', 60)
AI_QUIZ_BATCH_SIZE = _int('AI_QUIZ_BATCH_SIZE', 20)
AI_SOURCE_CHARS = _int('AI_SOURCE_CHARS', 60000)
AI_ASK_CHARS = _int('AI_ASK_CHARS', 60000)
CACHE_TTL_SECONDS=_int('CACHE_TTL_SECONDS',600)
LOG_LEVEL=os.getenv('LOG_LEVEL','INFO').upper()
