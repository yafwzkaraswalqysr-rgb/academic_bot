try:
    from aiogram.types import ReplyKeyboardMarkup,KeyboardButton,InlineKeyboardMarkup,InlineKeyboardButton
except ModuleNotFoundError:
    class _Missing:
        def __init__(self,*a,**k): raise RuntimeError('aiogram is required to run the Telegram bot; install requirements.txt')
    ReplyKeyboardMarkup=KeyboardButton=InlineKeyboardMarkup=InlineKeyboardButton=_Missing
from config import STUDY_LEVELS,SEARCH_SCOPES,DATE_RANGES,RESULT_LIMITS,SEARCH_MODES,ADMIN_IDS,OWNER_ID
from utils.admin_access import is_admin
from student_system import load_buttons

def get_main_menu(user_id=None):
    rows=[]
    enabled=[b for b in load_buttons() if b.get("enabled", True)]
    for i in range(0,len(enabled),2):
        rows.append([KeyboardButton(text=str(enabled[i]["label"]))] + ([KeyboardButton(text=str(enabled[i+1]["label"]))] if i+1<len(enabled) else []))
    if user_id is not None and is_admin(user_id):
        rows.append([KeyboardButton(text='🛠 لوحة المالك')])
    return ReplyKeyboardMarkup(keyboard=rows,resize_keyboard=True)

def get_navigation_keyboard(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🏠 الرئيسية',callback_data='main_menu'),InlineKeyboardButton(text='❌ إلغاء',callback_data='cancel')]])
def get_study_level_keyboard(): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x)] for x in STUDY_LEVELS],resize_keyboard=True,one_time_keyboard=True)
def get_search_type_keyboard(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📌 بحث عن موضوع',callback_data='search_topic')],[InlineKeyboardButton(text='🌐 بحث عام',callback_data='search_general')]])
def get_scope_keyboard(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=x,callback_data=f'scope_{i}') for i,x in enumerate(SEARCH_SCOPES)]])
def get_date_keyboard(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=x,callback_data=f'date_{i}') for i,x in enumerate(DATE_RANGES)]])
def get_limit_keyboard():
    labels=[('غير محدود' if x==0 else str(x)) for x in RESULT_LIMITS]
    rows=[]
    current=[]
    for x,label in zip(RESULT_LIMITS,labels):
        current.append(InlineKeyboardButton(text=label,callback_data=f'limit_{x}'))
        if len(current)==3:
            rows.append(current); current=[]
    if current: rows.append(current)
    rows.append([InlineKeyboardButton(text='✏️ عدد مخصص',callback_data='limit_custom')])
    rows.append([InlineKeyboardButton(text='🏠 الرئيسية',callback_data='main_menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)
def get_mode_keyboard(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=k,callback_data=f'mode_{v}')] for k,v in SEARCH_MODES.items()])
def get_pagination_keyboard(page,total,prefix='page'):
    row=[]
    if page>0: row.append(InlineKeyboardButton(text='⬅️ السابق',callback_data=f'{prefix}_{page-1}'))
    row.append(InlineKeyboardButton(text=f'📄 {page+1} من {max(1,total)}',callback_data='noop'))
    if page<total-1: row.append(InlineKeyboardButton(text='التالي ➡️',callback_data=f'{prefix}_{page+1}'))
    return InlineKeyboardMarkup(inline_keyboard=[row])
def get_result_actions_keyboard(rid,downloadable=False):
    rows=[[InlineKeyboardButton(text='⭐ حفظ',callback_data=f'save_{rid}'),InlineKeyboardButton(text='🤖 اسأل AI',callback_data=f'ask_{rid}')],[InlineKeyboardButton(text='📖 المرجع',callback_data=f'ref_{rid}')]]
    if downloadable: rows.append([InlineKeyboardButton(text='📥 إرسال الملف',callback_data=f'download_{rid}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)
def format_file_size(n):
    if not n:return 'غير متوفر'
    for u in ['B','KB','MB','GB']:
        if n<1024:return f'{n:.1f} {u}'
        n/=1024
    return f'{n:.1f} TB'
def truncate(s,n=500):
    s=str(s or ''); return s if len(s)<=n else s[:n-1]+'…'


async def get_main_menu_for_user(user_id=None):
    """Return a two-column reply-keyboard menu tailored to the saved path.

    Built-in buttons are controlled by buttons.json, so the owner can enable,
    disable, and manage the visible menu without changing Python code.
    """
    path = None
    if user_id is not None:
        try:
            from sqlalchemy import select
            from database import async_session_maker
            from models import StudentProfile
            async with async_session_maker() as s:
                db_user_id = await _user_db_id(s, user_id)
                if db_user_id > 0:
                    p = await s.scalar(select(StudentProfile).where(StudentProfile.user_id == db_user_id))
                    path = p.path if p else None
        except Exception:
            path = None

    buttons = {str(b.get('key')): b for b in load_buttons() if b.get('enabled', True)}
    custom = [b for b in load_buttons() if b.get('enabled', True) and str(b.get('action','')).startswith('custom:')]

    if path == 'school':
        keys = ['school', 'search', 'ai', 'account', 'help', 'delete_account', 'home']
    elif path == 'university':
        keys = ['university', 'search', 'graduation', 'ai', 'study', 'library', 'saved', 'account', 'help', 'delete_account', 'home']
    elif path == 'free':
        keys = ['search', 'ai', 'study', 'library', 'account', 'help', 'delete_account', 'home']
    else:
        # New users get only the registration entry point and safe account/help
        # controls. They cannot accidentally enter a university/school path.
        return get_main_menu(user_id)

    labels = []
    for key in keys:
        b = buttons.get(key)
        if b:
            labels.append(str(b.get('label', key)))
    labels.extend(str(b.get('label','')) for b in custom if b.get('label'))

    rows = []
    for i in range(0, len(labels), 2):
        rows.append([KeyboardButton(text=labels[i])] + ([KeyboardButton(text=labels[i+1])] if i+1 < len(labels) else []))
    if user_id is not None and is_admin(user_id):
        rows.append([KeyboardButton(text='🛠 لوحة المالك')])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

async def _user_db_id(session, telegram_id):
    from sqlalchemy import select
    from models import User
    u = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    return u.id if u else -1


def chunk_text(text, max_chars=3500):
    """Split long Telegram text while keeping normal paragraph boundaries when possible."""
    text = str(text or '')
    if len(text) <= max_chars:
        return [text]
    parts = []
    current = ''
    for block in text.split('\n\n'):
        block = block.strip()
        if not block:
            continue
        while len(block) > max_chars:
            cut = block.rfind('\n', 0, max_chars + 1)
            if cut < max_chars // 2:
                cut = block.rfind(' ', 0, max_chars + 1)
            if cut < max_chars // 2:
                cut = max_chars
            if current:
                parts.append(current)
                current = ''
            parts.append(block[:cut].rstrip())
            block = block[cut:].lstrip()
        candidate = (current + '\n\n' + block).strip() if current else block
        if len(candidate) > max_chars and current:
            parts.append(current)
            current = block
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts
