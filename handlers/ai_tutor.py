from __future__ import annotations
import asyncio
import random
import unicodedata
import weakref
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from services.ai_service import ai_service
from services.ai_parsing import clean_json
from services.error_service import log_error
from services.study_session import save_session, update_session_mode, clear_session, resolve_current_material
from utils.helpers import get_main_menu_for_user, truncate, chunk_text
from utils.security import safe_html_escape
from utils.text import clean_ai_text, normalize_arabic
from config import AI_TIMEOUT, AI_QUIZ_BATCH_SIZE, AI_SOURCE_CHARS, AI_ASK_CHARS, MAX_QUERY_LENGTH, INTERNAL_AI_ITEM_LIMIT, MAX_STUDY_TEXT_CHARS
from services.usage_service import record_event
from services.content_retrieval import retrieve_context
from services.material_pages import has_pages, parse_page_range, slice_pages
from utils.rate_limiter import rate_limiter
from models import User, StudentProfile, SchoolPlacement

router = Router(name='ai_tutor')

_QUIZ_LOCKS = weakref.WeakValueDictionary()

def _quiz_lock(user_id):
    lock = _QUIZ_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _QUIZ_LOCKS[user_id] = lock
    return lock

class AITutorStates(StatesGroup):
    source = State()
    mode = State()
    count = State()
    quiz = State()
    ask = State()
    generated = State()
    page_range = State()

MODES = {
    'ai_ask': '❓ سؤال وجواب',
    'ai_mcq': '📝 اختيار من متعدد',
    'ai_tf': '☑️ صح أو خطأ',
    'ai_short': '✍️ أسئلة قصيرة',
    'ai_fill': '🧩 أكمل الفراغ',
    'ai_match': '🔗 مطابقة',
    'ai_mixed': '🎯 امتحان شامل',
    'ai_flash': '🃏 بطاقات مراجعة',
    'ai_explain': '💡 شرح الدرس',
    'ai_summary': '📚 تلخيص ومراجعة',
}
COUNT_MODES = {'ai_ask','ai_mcq','ai_tf','ai_short','ai_fill','ai_match','ai_mixed','ai_flash'}
QUIZ_MODES = {'ai_mcq','ai_tf','ai_short','ai_fill','ai_mixed'}

NATURAL_MODE_ALIASES = {
    'امتحني': 'ai_mixed', 'اختبرني': 'ai_mixed', 'أمتحني': 'ai_mixed', 'أختبرني': 'ai_mixed', 'إختبرني': 'ai_mixed',
    'سويلي امتحان': 'ai_mixed', 'سوي امتحان': 'ai_mixed', 'اريد امتحان': 'ai_mixed', 'أريد امتحان': 'ai_mixed',
    'اريد اسئلة': 'ai_ask', 'أريد أسئلة': 'ai_ask', 'امتحان': 'ai_mixed', 'الامتحان': 'ai_mixed',
    'اي نوع من الامتحانات': 'ai_mixed', 'أي نوع من الامتحانات': 'ai_mixed',
    'اسألني': 'ai_ask', 'أسألني': 'ai_ask', 'اسالني': 'ai_ask', 'اسألني عن المادة': 'ai_ask', 'اسالني عن المادة': 'ai_ask',
    'سؤال وجواب': 'ai_ask', 'اسئلة': 'ai_ask', 'اسئله': 'ai_ask',
    'اعطني اسئلة': 'ai_ask', 'أعطني أسئلة': 'ai_ask', 'اعطني اسئله': 'ai_ask', 'اسئله مقاليه': 'ai_short', 'اسئلة مقالية': 'ai_short', 'أسئلة مقالية': 'ai_short',
    'لخص': 'ai_summary', 'تلخيص': 'ai_summary', 'لخصلي': 'ai_summary', 'لخص المادة': 'ai_summary', 'لخّص المادة': 'ai_summary',
    'اشرح': 'ai_explain', 'شرح': 'ai_explain', 'اشرحلي': 'ai_explain', 'اشرح المادة': 'ai_explain',
    'بطاقات': 'ai_flash', 'مراجعة': 'ai_flash', 'بطاقات مراجعة': 'ai_flash',
    'صح وخطا': 'ai_tf', 'صح وخطأ': 'ai_tf', 'اعطني صح وخطا': 'ai_tf', 'اعطني صح وخطأ': 'ai_tf', 'صح او خطأ': 'ai_tf',
    'اختيار من متعدد': 'ai_mcq', 'mcq': 'ai_mcq', 'اعطني mcq': 'ai_mcq', 'أعطني MCQ': 'ai_mcq',
    'اكمل الفراغ': 'ai_fill', 'أكمل الفراغ': 'ai_fill',
}
NATURAL_MODE_ALIASES_NORMALIZED = {normalize_arabic(key): value for key, value in NATURAL_MODE_ALIASES.items()}
NATURAL_MODE_FILTER_VALUES = tuple(set(NATURAL_MODE_ALIASES) | set(NATURAL_MODE_ALIASES_NORMALIZED))

def resolve_natural_mode(text):
    normalized = normalize_arabic(text or '').strip()
    exact = NATURAL_MODE_ALIASES_NORMALIZED.get(normalized)
    if exact:
        return exact
    # Accept common natural variations such as "اسألني عن المادة" without
    # swallowing unrelated questions. Only match when an alias is a clear
    # leading command or the complete text after punctuation cleanup.
    compact = normalized.strip(' .،,!?؟:؛-')
    for alias, mode in sorted(NATURAL_MODE_ALIASES_NORMALIZED.items(), key=lambda x: len(x[0]), reverse=True):
        if compact == alias or compact.startswith(alias + ' '):
            return mode
    return None


def mode_keyboard(session_id=None):
    """Build service buttons bound to the exact active StudySession.

    Binding callbacks to a session id prevents the next click from having to
    guess which material the user meant, while ownership is re-verified in the
    handler. Buttons without an id remain valid for post-restart/recovery flows.
    """
    items = list(MODES.items())
    rows = []
    for i in range(0, len(items), 2):
        row = [InlineKeyboardButton(text=items[i][1], callback_data=f'ai_mode:{items[i][0]}:{session_id}' if session_id else items[i][0])]
        if i + 1 < len(items):
            row.append(InlineKeyboardButton(text=items[i+1][1], callback_data=f'ai_mode:{items[i+1][0]}:{session_id}' if session_id else items[i+1][0]))
        rows.append(row)
    rows += [
        [InlineKeyboardButton(text='🔄 تغيير المادة', callback_data='ai_change_material')],
        [InlineKeyboardButton(text='❌ إنهاء الجلسة', callback_data='ai_end_session')],
        [InlineKeyboardButton(text='🏠 الرئيسية', callback_data='ai_home')],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def exam_type_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📝 اختيار من متعدد', callback_data='ai_exam_type:mcq'), InlineKeyboardButton(text='☑️ صح وخطأ', callback_data='ai_exam_type:tf')],
        [InlineKeyboardButton(text='❓ سؤال وجواب', callback_data='ai_exam_type:qa'), InlineKeyboardButton(text='🔀 مختلط', callback_data='ai_exam_type:mixed')],
        [InlineKeyboardButton(text='🏠 الرئيسية', callback_data='ai_home')],
    ])


def count_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔢 10', callback_data='ai_count:10'), InlineKeyboardButton(text='🔢 20', callback_data='ai_count:20')],
        [InlineKeyboardButton(text='🔢 30', callback_data='ai_count:30'), InlineKeyboardButton(text='🔢 50', callback_data='ai_count:50')],
        [InlineKeyboardButton(text='🔢 100', callback_data='ai_count:100'), InlineKeyboardButton(text='✏️ عدد مخصص', callback_data='ai_count:custom')],
        [InlineKeyboardButton(text='🏠 الرئيسية', callback_data='ai_home')],
    ])


def session_actions():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔄 تغيير نوع الخدمة', callback_data='ai_change_mode'), InlineKeyboardButton(text='🔄 تغيير المادة', callback_data='ai_change_material')],
        [InlineKeyboardButton(text='❓ سؤال جديد', callback_data='ai_new_question')],
        [InlineKeyboardButton(text='❌ إنهاء الجلسة', callback_data='ai_end_session'), InlineKeyboardButton(text='🏠 الرئيسية', callback_data='ai_home')],
    ])


def norm(v):
    v = unicodedata.normalize('NFKC', str(v or '').strip().casefold())
    v = v.replace('أ','ا').replace('إ','ا').replace('آ','ا')
    return ' '.join(v.split())


def choice_correct(selected, answer, idx):
    a, s = norm(answer), norm(selected)
    if a == s:
        return True
    if a.isdigit():
        numeric = int(a)
        # Canonical UI labels are one-based. Keep zero as a legacy alias only
        # for the first option; never accept two adjacent one-based answers.
        if numeric == idx + 1 or (numeric == 0 and idx == 0):
            return True
    if a in {'صح','صحيح','true','نعم','1'} and s in {'صح','صحيح','true','نعم','1'}:
        return True
    if a in {'خطا','خطأ','غير صحيح','false','لا','0'} and s in {'خطا','خطأ','غير صحيح','false','لا','0'}:
        return True
    return False


def quiz_kb(options):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=str(x)[:60], callback_data=f'quizans_{i}')
    ] for i, x in enumerate(options)])


def _validate_positive_count(value: str):
    try:
        n = int(str(value).strip())
        return n if n > 0 else None
    except Exception:
        return None


def _question_key(q):
    return normalize_arabic(q.get('question',''))[:500]


def _extract_questions(raw):
    # Gemini may return a JSON string nested inside another JSON field, an
    # object with an Arabic key, or a single question object. Normalize all of
    # those forms before validation instead of throwing away valid material.
    data = clean_json(raw)
    if data is None and isinstance(raw, str):
        # Some models wrap JSON in a quoted string. Try one extra decode.
        try:
            import json
            decoded = json.loads(raw.strip())
            data = clean_json(decoded) if isinstance(decoded, str) else decoded
        except Exception:
            data = None
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    keys = (
        'questions', 'question', 'items', 'cards', 'results', 'data',
        'questions_list', 'exam', 'quiz', 'أسئلة', 'الاسئلة', 'الأسئلة',
        'العناصر', 'بطاقات', 'نتائج',
    )
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_questions(value)
            if nested:
                return nested

    # A single question object is also a valid response.
    if any(k in data for k in ('question', 'prompt', 'question_text', 'السؤال', 'النص')):
        return [data]
    return []


def _dedupe_options(options):
    out = []
    seen = set()
    for item in options or []:
        text = clean_ai_text(item)
        key = norm(text)
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _normalize_pairs(raw_pairs):
    pairs = []
    if not isinstance(raw_pairs, list):
        return pairs
    for item in raw_pairs:
        left = right = ''
        if isinstance(item, dict):
            left = (item.get('left') or item.get('term') or item.get('question') or
                    item.get('from') or item.get('المصطلح') or item.get('الطرف الاول') or '')
            right = (item.get('right') or item.get('definition') or item.get('answer') or
                     item.get('to') or item.get('التعريف') or item.get('الطرف الثاني') or '')
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            left, right = item[0], item[1]
        if str(left).strip() and str(right).strip():
            pairs.append({'left': clean_ai_text(left), 'right': clean_ai_text(right)})
    unique = []
    seen = set()
    for pair in pairs:
        key = (norm(pair['left']), norm(pair['right']))
        if key not in seen:
            seen.add(key)
            unique.append(pair)
    return unique


def _normalize_question(q, kind):
    if not isinstance(q, dict):
        return None

    def pick(*keys):
        for key in keys:
            value = q.get(key)
            if value is not None and str(value).strip() != '':
                return value
        return ''

    question = clean_ai_text(pick('question', 'prompt', 'question_text', 'النص', 'السؤال'))
    raw_options = pick('options', 'choices', 'choices_list', 'الخيارات', 'الاختيارات')
    opts = _dedupe_options(raw_options if isinstance(raw_options, list) else [])
    raw_answer = pick(
        'answer', 'correct_answer', 'correctAnswer', 'correct', 'solution',
        'correct_option', 'correctOption', 'correct_index', 'correctIndex',
        'right_answer', 'rightAnswer', 'expected_answer', 'expectedAnswer',
        'الإجابة', 'الجواب', 'الإجابة الصحيحة', 'الجواب الصحيح', 'الخيار الصحيح',
    )

    typ = str(q.get('type') or q.get('question_type') or q.get('kind') or '').lower().strip()
    if kind == 'ai_mcq':
        typ = 'mcq'
    elif kind == 'ai_tf':
        typ = 'tf'
    elif kind == 'ai_short':
        typ = 'short'
    elif kind == 'ai_fill':
        typ = 'fill'
    elif kind == 'ai_mixed':
        typ = typ if typ in {'mcq','tf','short','fill'} else 'short'
    elif kind == 'ai_flash':
        typ = 'flash'
    elif kind == 'ai_match':
        typ = 'match'
    elif kind == 'ai_ask':
        typ = 'qa'

    pairs = _normalize_pairs(pick('pairs', 'matches', 'matching', 'pairings', 'الأزواج', 'المطابقة'))
    if typ == 'match' or kind == 'ai_match':
        if not question:
            question = clean_ai_text(pick('title', 'instruction', 'التعليمات')) or 'طابق كل عنصر مع ما يناسبه:'
        if len(pairs) < 2:
            return None
        shuffled_rights = [pair['right'] for pair in pairs]
        random.SystemRandom().shuffle(shuffled_rights)
        answer = ' ؛ '.join(f"{pair['left']} ← {pair['right']}" for pair in pairs)
        return {
            'type': 'match', 'question': question, 'answer': answer,
            'options': [], 'pairs': pairs, 'shuffled_rights': shuffled_rights,
            'explanation': clean_ai_text(pick('explanation', 'feedback', 'rationale', 'التفسير', 'الشرح')),
        }

    if isinstance(raw_answer, bool):
        answer = 'صح' if raw_answer else 'خطأ'
    elif isinstance(raw_answer, (int, float)) and opts:
        idx = int(raw_answer)
        if 1 <= idx <= len(opts):
            answer = opts[idx - 1]
        elif 0 <= idx < len(opts):
            answer = opts[idx]
        else:
            answer = str(raw_answer)
    else:
        answer = clean_ai_text(raw_answer)
        if isinstance(raw_answer, dict):
            answer = clean_ai_text(raw_answer.get('text') or raw_answer.get('value') or raw_answer.get('label') or raw_answer.get('name') or '')
        if opts and answer:
            letter_map = {'ا': 0, 'أ': 0, 'ب': 1, 'ج': 2, 'د': 3, 'ه': 4}
            compact_answer = norm(answer).strip().strip('.').strip(')').strip('(')
            if len(compact_answer) == 1 and compact_answer.upper() in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
                idx = ord(compact_answer.upper()) - ord('A')
                if 0 <= idx < len(opts):
                    answer = opts[idx]
            elif compact_answer in letter_map and letter_map[compact_answer] < len(opts):
                answer = opts[letter_map[compact_answer]]
            elif compact_answer.isdigit():
                idx = int(compact_answer)
                if 1 <= idx <= len(opts):
                    answer = opts[idx - 1]
                elif 0 <= idx < len(opts):
                    answer = opts[idx]

    if not question or not answer:
        return None

    if typ == 'tf':
        opts = ['صح', 'خطأ']
        if norm(answer) in {'true', 'نعم', 'صح', 'صحيح', '1'}:
            answer = 'صح'
        elif norm(answer) in {'false', 'لا', 'خطأ', 'خطا', 'غير صحيح', '0'}:
            answer = 'خطأ'
        else:
            return None

    if typ == 'mcq':
        if len(opts) < 2:
            return None
        answer_key = norm(answer)
        match_index = next((i for i, opt in enumerate(opts) if norm(opt) == answer_key), None)
        if match_index is None:
            return None
        correct = opts[match_index]
        others = [opt for i, opt in enumerate(opts) if i != match_index]
        random.SystemRandom().shuffle(others)
        opts = [correct] + others
        answer = correct

    if typ == 'fill':
        if not any(token in question for token in ('____', '___')):
            replaced = False
            for candidate in (answer, answer.strip('"“”')):
                if candidate and candidate in question:
                    question = question.replace(candidate, '____', 1)
                    replaced = True
                    break
            if not replaced:
                question = question.rstrip(' .؟?') + ' ____'

    return {
        'type': typ, 'question': question, 'answer': answer, 'options': opts,
        'explanation': clean_ai_text(pick('explanation', 'feedback', 'rationale', 'التفسير', 'الشرح')),
    }


def _generation_schema(kind, batch_count, truth_value=None):
    if kind == 'ai_tf':
        label = 'صح' if truth_value else 'خطأ'
        return f"""أنشئ حتى {batch_count} عبارات دراسية من المادة فقط. كل عبارة يجب أن تكون من النوع: {label}.
إذا كان النوع صح، يجب أن تكون العبارة صحيحة فعلاً وفق المادة وتكون answer = "صح".
إذا كان النوع خطأ، يجب أن تكون العبارة خاطئة فعلاً وفق المادة وتكون answer = "خطأ".
استخدم options = ["صح", "خطأ"] دائمًا.
أعد JSON فقط بهذا الشكل: {{"questions":[{{"type":"tf","question":"...","options":["صح","خطأ"],"answer":"{label}","explanation":""}}]}}"""
    if kind == 'ai_mcq':
        return f"""أنشئ حتى {batch_count} أسئلة اختيار من متعدد من المادة فقط. كل سؤال له 4 خيارات وإجابة صحيحة واحدة.
أعد JSON فقط بهذا الشكل: {{"questions":[{{"type":"mcq","question":"...","options":["...","...","...","..."],"answer":"نص الإجابة الصحيحة","explanation":""}}]}}"""
    if kind == 'ai_fill':
        return f"""أنشئ حتى {batch_count} أسئلة أكمل الفراغ من المادة فقط.
يجب أن يحتوي question على ____ واضح، ويجب أن تكون answer عبارة قصيرة دقيقة تكمل الفراغ.
أعد JSON فقط بهذا الشكل: {{"questions":[{{"type":"fill","question":"... ____ ...","answer":"...","explanation":""}}]}}"""
    if kind == 'ai_match':
        return f"""أنشئ حتى {batch_count} تمارين مطابقة من المادة فقط. كل تمرين يحتوي 3 إلى 5 أزواج على الأقل.
أعد JSON فقط بهذا الشكل: {{"questions":[{{"type":"match","question":"طابق العناصر التالية","pairs":[{{"left":"...","right":"..."}},{{"left":"...","right":"..."}},{{"left":"...","right":"..."}}],"answer":""}}]}}"""
    return f"""أنشئ حتى {batch_count} عناصر من المادة فقط.
أعد JSON فقط بهذا الشكل: {{"questions":[{{"type":"{kind.replace('ai_','')}","question":"...","options":[],"answer":"...","explanation":""}}]}}"""


def _extract_and_normalize(raw, kind):
    data = _extract_questions(raw)
    batch = [_normalize_question(q, kind) for q in data]
    return [q for q in batch if q]


async def _ai_question_request(prompt, *, json_mode=False):
    if json_mode and hasattr(ai_service, 'call_json'):
        result = await ai_service.call_json(prompt)
        if result:
            return result
        # Structured-output mode is preferred, but a transient SDK/model issue
        # must not make a valid study request fail when plain text still works.
        return await ai_service.call(prompt)
    return await ai_service.call(prompt)


async def generate_questions(source, kind, count, *, subject=None, title=None):
    """Generate reliable study items in batches with type-specific guarantees."""
    if not ai_service.available or count <= 0:
        return []
    count = min(int(count), INTERNAL_AI_ITEM_LIMIT)
    source_text = retrieve_context(source, max_chars=AI_SOURCE_CHARS)
    if not source_text.strip():
        await log_error('ai.generate_questions', 'Source material is empty after retrieval.', user_id=None)
        return []

    rng = random.SystemRandom()

    async def request_batch(batch_count, prompt, kind_for_parse, previous=''):
        try:
            raw = await asyncio.wait_for(_ai_question_request(prompt, json_mode=True), timeout=AI_TIMEOUT)
        except Exception as exc:
            await log_error('ai.generate_questions', f'AI request failed: {exc}', user_id=None)
            raw = None
        batch = _extract_and_normalize(raw, kind_for_parse)
        if batch:
            return batch
        retry_prompt = f"""{_generation_schema(kind_for_parse, batch_count)}
القواعد:
- لا تضع Markdown أو أي كلام خارج JSON.
- اعتمد على المادة فقط.
- لا تكرر الأسئلة السابقة.
المادة الدراسية: {title or 'غير محددة'}
التخصص/المادة: {subject or 'غير محددة'}
العناصر السابقة:
{previous[:5000]}
المادة:
{source_text[:AI_SOURCE_CHARS]}"""
        try:
            retry_raw = await asyncio.wait_for(_ai_question_request(retry_prompt, json_mode=True), timeout=AI_TIMEOUT)
        except Exception as exc:
            await log_error('ai.generate_questions.retry', f'AI retry failed: {exc}', user_id=None)
            retry_raw = None
        batch = _extract_and_normalize(retry_raw, kind_for_parse)
        if not batch:
            await log_error('ai.generate_questions.parse', f'AI returned empty/unusable output for kind={kind_for_parse}, requested={batch_count}: {str(retry_raw)[:1000]}', user_id=None)
        return batch

    if kind == 'ai_tf':
        extra_true = bool(rng.randrange(2)) if count % 2 else False
        target_true = count // 2 + (1 if extra_true else 0)
        target_false = count - target_true
        true_items, false_items, seen = [], [], set()
        attempts = 0
        while (len(true_items) < target_true or len(false_items) < target_false) and attempts < 8:
            attempts += 1
            if len(true_items) < target_true:
                n = min(AI_QUIZ_BATCH_SIZE, target_true - len(true_items))
                prompt = f"""{_generation_schema('ai_tf', n, True)}
المادة الدراسية: {title or 'غير محددة'}
التخصص/المادة: {subject or 'غير محددة'}
المادة:
{source_text[:AI_SOURCE_CHARS]}"""
                for q in await request_batch(n, prompt, 'ai_tf'):
                    if q['answer'] == 'صح':
                        key = _question_key(q)
                        if key and key not in seen:
                            seen.add(key); true_items.append(q)
                            if len(true_items) >= target_true: break
            if len(false_items) < target_false:
                n = min(AI_QUIZ_BATCH_SIZE, target_false - len(false_items))
                prompt = f"""{_generation_schema('ai_tf', n, False)}
المادة الدراسية: {title or 'غير محددة'}
التخصص/المادة: {subject or 'غير محددة'}
المادة:
{source_text[:AI_SOURCE_CHARS]}"""
                for q in await request_batch(n, prompt, 'ai_tf'):
                    if q['answer'] == 'خطأ':
                        key = _question_key(q)
                        if key and key not in seen:
                            seen.add(key); false_items.append(q)
                            if len(false_items) >= target_false: break
        items = true_items[:target_true] + false_items[:target_false]
        rng.shuffle(items)
        return items

    all_questions, seen, empty_rounds = [], set(), 0
    while len(all_questions) < count and empty_rounds < 3:
        batch_count = min(AI_QUIZ_BATCH_SIZE, count - len(all_questions))
        previous = '\n'.join(q['question'] for q in all_questions[-30:])
        prompt = f"""{_generation_schema(kind, batch_count)}
اعتمد على المادة فقط، ولا تخترع معلومات. إذا كانت المادة لا تكفي، أرجع العدد الممكن فقط.
المادة الدراسية: {title or 'غير محددة'}
التخصص/المادة: {subject or 'غير محددة'}
العناصر التي سبقت، لتجنب التكرار:
{previous[:5000]}
المادة:
{source_text[:AI_SOURCE_CHARS]}"""
        batch = await request_batch(batch_count, prompt, kind, previous)
        new_count = 0
        for q in batch:
            key = _question_key(q)
            if key and key not in seen:
                seen.add(key); all_questions.append(q); new_count += 1
                if len(all_questions) >= count: break
        empty_rounds = 0 if new_count else empty_rounds + 1

    if kind == 'ai_mcq':
        for q in all_questions:
            options = _dedupe_options(q.get('options', []))
            answer_key = norm(q.get('answer', ''))
            idx = next((i for i, opt in enumerate(options) if norm(opt) == answer_key), None)
            if idx is not None:
                correct = options[idx]
                others = options[:idx] + options[idx+1:]
                rng.shuffle(others)
                q['options'] = [correct] + others
                q['answer'] = correct
        return all_questions[:count]

    if kind == 'ai_match':
        for q in all_questions:
            rights = [pair['right'] for pair in q.get('pairs', [])]
            rng.shuffle(rights)
            q['shuffled_rights'] = rights
        return all_questions[:count]

    return all_questions[:count]


async def generate_quiz(source, count=10, kind='ai_mixed'):
    """Backward-compatible wrapper; the new UI has no fixed maximum."""
    return await generate_questions(source, kind if kind in COUNT_MODES else 'ai_mixed', int(count))


def _format_number(n):
    digits = ['0️⃣','1️⃣','2️⃣','3️⃣','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣']
    if 0 <= n < 10:
        return digits[n]
    return f'{n}.'


def format_question_set(items, heading='🤖 أسئلة وأجوبة'):
    lines = [f'<b>{safe_html_escape(heading)}</b>', '']
    for i, q in enumerate(items, 1):
        lines.append(f'{_format_number(i)} <b>السؤال:</b> {safe_html_escape(q["question"])}')
        if q.get('type') == 'match' and q.get('pairs'):
            lines.append('🔹 <b>الطرف الأول:</b>')
            for j, pair in enumerate(q['pairs'], 1):
                lines.append(f'{j}) {safe_html_escape(pair["left"])}')
            lines.append('')
            lines.append('🔸 <b>الطرف الثاني (بترتيب عشوائي):</b>')
            for j, right in enumerate(q.get('shuffled_rights') or [p['right'] for p in q['pairs']], 1):
                lines.append(f'{j}) {safe_html_escape(right)}')
            lines.append(f'✅ <b>المطابقة الصحيحة:</b> {safe_html_escape(q["answer"])}')
        elif q.get('options'):
            for j, opt in enumerate(q['options']):
                lines.append(f'{chr(65+j)}) {safe_html_escape(opt)}')
            lines.append(f'✅ <b>الجواب:</b> {safe_html_escape(q["answer"])}')
        else:
            lines.append(f'✅ <b>الجواب:</b> {safe_html_escape(q["answer"])}')
        if q.get('explanation'):
            lines.append(f'💡 <b>التوضيح:</b> {safe_html_escape(q["explanation"])}')
        lines.append('')
    return '\n'.join(lines)


async def _send_chunked(m, text, reply_markup=None):
    parts = chunk_text(text, 3500)
    for i, part in enumerate(parts):
        await m.answer(part, parse_mode='HTML', reply_markup=reply_markup if i == len(parts)-1 else None)


_FSM_FALLBACK_MAX_CHARS = min(MAX_STUDY_TEXT_CHARS, 250_000)

def _fallback_material_from_data(data):
    text = str(data.get('material_fallback_text') or '').strip()
    if not text:
        return None
    return {
        'session_id': data.get('active_session_id'),
        'title': str(data.get('material_title') or 'مادة').strip() or 'مادة',
        'text': text,
        'section': data.get('material_section') or 'university',
        'content_id': data.get('material_content_id'),
        'book_id': data.get('material_content_id'),
        'content_type': data.get('material_content_type'),
        'placement_id': data.get('material_placement_id'),
        'subject': data.get('material_subject') or '',
        'mode': data.get('material_mode'),
        'context': data.get('material_context') or {},
    }


async def _current_material(uid, state=None):
    """Resolve current material without losing the working AI path.

    Durable StudySession remains the primary source of truth. The FSM copy is
    only a bounded emergency hand-off for the same interaction, matching the
    older version that passed the extracted source directly through FSM.
    """
    data = {}
    session_id = None
    if state is not None:
        try:
            data = await state.get_data()
            session_id = data.get('active_session_id')
        except Exception:
            data = {}
            session_id = None

    material = await resolve_current_material(uid, session_id=session_id)
    if material:
        return material

    # A stale callback/session id must never hide a valid current session.
    if session_id is not None:
        material = await resolve_current_material(uid)
        if material:
            return material

    # Emergency compatibility path: use the just-uploaded/just-selected text
    # from FSM and re-create the durable row when possible. This keeps the AI
    # usable even when the bound session id became stale between clicks.
    fallback = _fallback_material_from_data(data)
    if fallback:
        try:
            saved = await save_session(
                uid,
                title=fallback['title'],
                text=fallback['text'],
                section=fallback['section'],
                content_id=fallback.get('content_id'),
                content_type=fallback.get('content_type'),
                mode=fallback.get('mode'),
                context=fallback.get('context') or {},
            )
            fallback['session_id'] = getattr(saved, 'id', None)
            return fallback
        except Exception:
            # Do not discard the in-memory material merely because persistence
            # is temporarily unavailable; the AI service can still answer from
            # the exact source that the user just supplied in this FSM session.
            return fallback

    return None


async def _save_material(uid, *, title, text, section='university', content_id=None, content_type=None, context=None):
    text = (text or '').strip()
    if not text:
        return None
    return await save_session(
        uid, title=title or 'مادة', text=text, section=section, content_id=content_id,
        content_type=content_type, mode=None, context=context or {}
    )


async def _show_mode_menu(m, uid, state=None):
    material = await _current_material(uid, state)
    if not material:
        await m.answer('📚 لا توجد مادة محفوظة حاليًا. أرسل المادة أولًا.')
        return False
    active_id = material.get('session_id')
    if state is not None:
        active_id = (await state.get_data()).get('active_session_id') or active_id
    await m.answer(
        f'📖 <b>المادة الحالية:</b> {safe_html_escape(material["title"])}\n\n'
        '✅ المادة محفوظة للجلسة. تگدر تسأل أكثر من مرة وتغيّر نوع الخدمة بدون إعادة إرسالها.\n\n'
        'اختَر الخدمة:', parse_mode='HTML', reply_markup=mode_keyboard(active_id)
    )
    return True


@router.message(F.text == '🤖 اسأل الذكاء الاصطناعي')
async def ai_start(m: Message, state: FSMContext, source=None, preferred_mode=None):
    previous = await state.get_data()
    active_session_id = previous.get('active_session_id')
    await state.clear()
    if source:
        saved = await _save_material(m.from_user.id, title='مادة', text=source)
        active_session_id = getattr(saved, 'id', None)
        await state.update_data(
            active_session_id=active_session_id,
            material_ready=True,
            material_title='مادة',
            material_fallback_text=str(source)[:_FSM_FALLBACK_MAX_CHARS],
            material_section='university',
        )
    material = await _current_material(m.from_user.id, state if active_session_id is None else None)
    if active_session_id is not None:
        material = await resolve_current_material(m.from_user.id, session_id=active_session_id)
    if material:
        await state.update_data(active_session_id=active_session_id or material.get('session_id'))
        if preferred_mode:
            await state.set_state(AITutorStates.mode)
            return await start_mode(m, state, preferred_mode)
        await state.set_state(AITutorStates.mode)
        return await _show_mode_menu(m, m.from_user.id, state)
    await state.set_state(AITutorStates.source)
    if preferred_mode:
        await state.update_data(preferred_mode=preferred_mode)
    await m.answer(
        '🤖 <b>لا توجد مادة مفتوحة حاليًا</b>\n\n'
        '📄 أرسل الآن <b>الملف الجديد</b> (PDF/DOCX/PPTX/TXT) أو الصق نص المادة.\n'
        'بعد وصول الملف سأحفظه كمادة جديدة مباشرة، حتى لو كنت قد أنهيت جلسة سابقة.\n\n'
        '🏫 وإذا تريد مادة من كتب مدرستي، اخترها من هناك.',
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🏫 اختيار مادة من مدرستي', callback_data='ai_pick_school')],
            [InlineKeyboardButton(text='❌ إلغاء', callback_data='ai_home')],
        ])
    )


@router.callback_query(F.data.startswith('study_content:'))
async def study_content(c: CallbackQuery, state: FSMContext):
    from database import async_session_maker
    from models import SchoolContent
    from sqlalchemy import select
    if not c.from_user:
        return await c.answer()
    parts = c.data.split(':')
    try:
        cid = int(parts[1])
        placement_id = int(parts[2]) if len(parts) > 2 and parts[2] else None
    except (TypeError, ValueError, IndexError):
        return await c.answer('المادة غير صالحة.', show_alert=True)
    async with async_session_maker() as s:
        item = await s.scalar(select(SchoolContent).where(SchoolContent.id == cid))
        user = await s.scalar(select(User).where(User.telegram_id == c.from_user.id))
        profile = await s.scalar(select(StudentProfile).where(StudentProfile.user_id == user.id)) if user else None
        placements = list((await s.execute(select(SchoolPlacement).where(SchoolPlacement.content_id == cid))).scalars().all())
    if not item:
        return await c.answer('المادة غير موجودة', show_alert=True)
    if not profile or profile.path != 'school':
        return await c.answer('هذه المادة ليست ضمن مسارك الدراسي.', show_alert=True)
    from handlers.school import _placement_matches
    if placement_id is not None:
        selected = next((p for p in placements if p.id == placement_id), None)
        if not selected:
            return await c.answer('مكان نشر المادة لم يعد موجودًا.', show_alert=True)
        if not _placement_matches(profile, selected, selected.subject or None):
            return await c.answer('هذه المادة ليست متاحة لمسارك الدراسي.', show_alert=True)
    else:
        selected = next((p for p in placements if _placement_matches(profile, p, None)), None)
        if not selected:
            return await c.answer('هذه المادة ليست متاحة لمسارك الدراسي.', show_alert=True)
    material_text = (item.text_content or '').strip()
    # Upgrade legacy PDF records that were stored before page markers existed.
    # This is done only for PDF files that already have a Telegram file_id, so
    # old books gain real page-range support without changing non-PDF content.
    needs_page_upgrade = bool(
        item.file_id
        and str(item.mime_type or '').lower() == 'application/pdf'
        and material_text
        and not has_pages(material_text)
    )
    if (not material_text or needs_page_upgrade) and item.file_id:
        try:
            from services.content_index import extract_telegram_document
            material_text = (await extract_telegram_document(c.bot, item.file_id, item.title or 'file', item.mime_type) or '').strip()
            if material_text:
                async with async_session_maker() as s:
                    stored = await s.scalar(select(SchoolContent).where(SchoolContent.id == item.id))
                    if stored:
                        stored.text_content = material_text
                        await s.commit()
                        item.text_content = material_text
        except Exception as exc:
            await log_error('ai.school_material.extract', str(exc), user_id=None)
            return await c.answer('تعذر استخراج نص هذه المادة حاليًا. أعد المحاولة لاحقًا.', show_alert=True)
    if not material_text:
        return await c.answer('هذه المادة لا تحتوي نصًا مستخرجًا يمكن استخدامه حاليًا.', show_alert=True)
    try:
        saved = await _save_material(
            c.from_user.id, title=item.title, text=material_text, section='school',
            content_id=item.id, content_type=item.content_type,
            context={
                'bookId': item.id, 'book_id': item.id,
                'placement_id': selected.id, 'subject': selected.subject or '',
                'content_type': item.content_type, 'stage': selected.stage or '',
                'grade': selected.grade or '', 'branch': selected.branch or '',
                'department': selected.department or '',
            },
        )
    except Exception as exc:
        await log_error('ai.school_material.save', str(exc), user_id=None)
        return await c.answer('تعذر حفظ مادة الكتاب للجلسة الدراسية. أعد المحاولة بعد التأكد من حجم النص.', show_alert=True)
    if not saved:
        await log_error('ai.school_material.save', 'StudySession was not created for an existing school book.', user_id=None)
        return await c.answer('تعذر حفظ مادة الكتاب للجلسة الدراسية. أعد المحاولة.', show_alert=True)
    await record_event(c.from_user.id, 'open_file', metadata={
        'school_content_id': item.id,
        'content_type': item.content_type,
        'title': item.title,
        'subject': selected.subject or '',
        'placement_id': selected.id,
    })
    await state.clear(); await state.update_data(active_session_id=saved.id, material_ready=True, material_title=item.title, material_fallback_text=material_text[:_FSM_FALLBACK_MAX_CHARS], material_section='school', material_content_id=item.id, material_content_type=item.content_type, material_placement_id=selected.id, material_subject=selected.subject or '', material_context={'bookId': item.id, 'book_id': item.id, 'placement_id': selected.id, 'subject': selected.subject or '', 'content_type': item.content_type, 'stage': selected.stage or '', 'grade': selected.grade or '', 'branch': selected.branch or '', 'department': selected.department or ''}); await state.set_state(AITutorStates.mode)
    await c.message.answer(f'📖 <b>تم اختيار المادة الحالية:</b> {safe_html_escape(item.title)}\n\nاختَر الخدمة:',
                            parse_mode='HTML', reply_markup=mode_keyboard(saved.id))
    await c.answer()


@router.message(AITutorStates.source, F.text)
async def ai_source(m: Message, state: FSMContext):
    d = await state.get_data()
    text = m.text.strip()
    if d.get('mode') == 'ai_ask':
        material = await _current_material(m.from_user.id)
        if not material:
            return await m.answer('انتهت المادة الحالية. أرسل المادة من جديد.')
        await answer_free_question(m, state, text, material)
        return
    if len(text) < 2 or len(text) > 10000:
        return await m.answer('اكتب نصًا بين حرفين و10000 حرف، أو ارفع ملفًا.')
    saved = await _save_material(m.from_user.id, title=text[:120], text=text)
    await state.clear(); await state.update_data(active_session_id=getattr(saved, 'id', None), material_ready=True, material_title=text[:120], material_fallback_text=text[:_FSM_FALLBACK_MAX_CHARS], material_section='university'); await state.set_state(AITutorStates.mode)
    preferred = d.get('preferred_mode')
    if preferred:
        return await start_mode(m, state, preferred)
    await _show_mode_menu(m, m.from_user.id)


async def answer_free_question(m, state, question, material):
    if not rate_limiter.is_allowed(m.from_user.id):
        return await m.answer(f'⏳ انتظر {rate_limiter.get_wait_time(m.from_user.id)} ثانية قبل طلب ذكاء اصطناعي جديد.')
    if len(question) < 2:
        return await m.answer('اكتب سؤالك عن المادة.')
    context = retrieve_context(material['text'], question, max_chars=AI_ASK_CHARS)
    prompt = f'أجب اعتمادًا على المادة فقط. إذا لم تكف المادة، صرّح بذلك. لا تخترع معلومات.\nالمادة:\n{context}\nالسؤال:\n{question[:3000]}'
    try:
        ans = await asyncio.wait_for(ai_service.call(prompt), timeout=AI_TIMEOUT)
    except Exception as exc:
        await log_error('ai.ask', str(exc), user_id=None)
        ans = None
    ans = clean_ai_text(ans or 'تعذر توليد الإجابة حاليًا.')
    await update_session_mode(m.from_user.id, 'ai_ask')
    await record_event(m.from_user.id, 'question', metadata={'mode': 'ai_ask'})
    await m.answer('🤖 <b>الإجابة</b>\n\n' + safe_html_escape(ans), parse_mode='HTML', reply_markup=session_actions())


@router.callback_query(F.data == 'ai_pick_school')
async def ai_pick_school(c, state):
    """Leave the AI FSM and open the student's school library."""
    await state.clear()
    await c.message.answer('🏫 افتح «مدرستي» واختر الكتاب الذي تريد جعله المادة الحالية.')
    await c.answer()

@router.callback_query(F.data == 'ai_home')
async def ai_home(c, state):
    await state.clear()
    await c.message.answer('🏠 الرئيسية', reply_markup=await get_main_menu_for_user(c.from_user.id))
    await c.answer()

@router.callback_query(F.data == 'ai_change_material')
async def ai_change_material(c, state):
    await state.clear()
    await clear_session(c.from_user.id)
    await state.set_state(AITutorStates.source)
    await c.message.answer('🔄 <b>تغيير المادة</b>\n\nأرسل المادة الجديدة أو ارفع ملفًا.', parse_mode='HTML')
    await c.answer()

@router.callback_query(F.data == 'ai_end_session')
async def ai_end_session(c, state):
    await state.clear(); await clear_session(c.from_user.id)
    await c.message.answer('❌ انتهت جلسة المادة.', reply_markup=await get_main_menu_for_user(c.from_user.id))
    await c.answer()

@router.callback_query(F.data == 'ai_change_mode')
async def ai_change_mode(c, state):
    data = await state.get_data()
    active_session_id = data.get('active_session_id')
    material = await _current_material(c.from_user.id, state)
    if not material:
        return await c.answer('لا توجد مادة حالية.', show_alert=True)
    await state.clear(); await state.update_data(active_session_id=active_session_id or material.get('session_id')); await state.set_state(AITutorStates.mode)
    await c.message.answer('🔄 اختر نوع الخدمة للمادة الحالية:', reply_markup=mode_keyboard(active_session_id or material.get('session_id')))
    await c.answer()

@router.callback_query(F.data == 'ai_new_question')
async def ai_new_question(c, state):
    material = await _current_material(c.from_user.id, state)
    if not material:
        return await c.answer('لا توجد مادة حالية.', show_alert=True)
    await state.clear()
    if not await _ensure_page_range(c.message, state, material, 'ai_ask'):
        await c.answer()
        return
    await state.set_state(AITutorStates.ask)
    await update_session_mode(c.from_user.id, 'ai_ask')
    await c.message.answer(f'❓ اكتب سؤالًا عن <b>{safe_html_escape(material["title"])}</b>:', parse_mode='HTML')
    await c.answer()

@router.message(StateFilter(None), F.text.in_(NATURAL_MODE_FILTER_VALUES))
async def ai_natural_command_without_state(m: Message, state: FSMContext):
    """Resume material-backed commands after a bot/process restart.

    FSM state is intentionally ephemeral; the actual material lives in
    study_sessions. Exact command matching avoids swallowing ordinary chat.
    """
    text = normalize_arabic(m.text or '').strip()
    mode = resolve_natural_mode(text)
    if not mode:
        return
    material = await _current_material(m.from_user.id, state)
    if not material:
        await state.set_state(AITutorStates.source)
        return await m.answer('📚 لا توجد مادة حالية. أرسل المادة أولًا.')
    await state.set_state(AITutorStates.mode)
    await start_mode(m, state, mode)


@router.message(AITutorStates.mode, F.text)
async def ai_mode_text(m: Message, state: FSMContext):
    """Allow natural Arabic commands such as «امتحني» after a file is uploaded."""
    material = await _current_material(m.from_user.id, state)
    if not material:
        await state.clear()
        await state.set_state(AITutorStates.source)
        return await m.answer('📚 لا توجد مادة حالية. أرسل المادة أولًا.')
    text = normalize_arabic(m.text or '').strip()
    mode = resolve_natural_mode(text)
    if not mode:
        # Treat any normal sentence as a question about the current material.
        await state.set_state(AITutorStates.ask)
        return await answer_free_question(m, state, m.text.strip(), material)
    await state.set_state(AITutorStates.mode)
    await start_mode(m, state, mode)


@router.callback_query(F.data == 'ai_pages:all')
async def ai_pages_all(c, state):
    material = await _current_material(c.from_user.id, state)
    if not material or not material.get('has_pages'):
        return await c.answer('المادة الحالية لا تحتوي أرقام صفحات قابلة للاختيار.', show_alert=True)
    await state.update_data(page_start=1, page_end=int(material['page_count']), page_selection_done=True)
    mode = (await state.get_data()).get('pending_mode')
    if not mode:
        return await c.answer('انتهت خطوة اختيار الخدمة، اختر الخدمة من جديد.', show_alert=True)
    await c.answer('📚 تم اختيار كامل المادة')
    await state.set_state(AITutorStates.mode)
    return await start_mode(c.message, state, mode)


@router.callback_query(F.data == 'ai_pages:custom')
async def ai_pages_custom(c, state):
    material = await _current_material(c.from_user.id, state)
    if not material or not material.get('has_pages'):
        return await c.answer('المادة الحالية لا تحتوي أرقام صفحات قابلة للاختيار.', show_alert=True)
    await state.set_state(AITutorStates.page_range)
    await c.message.answer(
        f'✍️ اكتب النطاق بهذا الشكل: <code>5-12</code> أو <code>5 إلى 12</code>\n'
        f'📄 المتاح: من 1 إلى {material["page_count"]}',
        parse_mode='HTML'
    )
    await c.answer()


@router.message(AITutorStates.page_range, F.text)
async def ai_page_range_text(m, state):
    material = await _current_material(m.from_user.id)
    if not material or not material.get('has_pages'):
        await state.clear()
        return await m.answer('📚 لا توجد مادة قابلة لتحديد صفحاتها. أرسل المادة أولًا.')
    parsed = parse_page_range(m.text, int(material['page_count']))
    if not parsed:
        return await m.answer(f'❌ نطاق غير صحيح. اكتب مثل: <code>5-12</code>\n📄 المدى المسموح: 1-{material["page_count"]}', parse_mode='HTML')
    start, end = parsed
    data = await state.get_data()
    mode = data.get('pending_mode')
    if not mode:
        return await m.answer('انتهت خطوة اختيار الخدمة، اختر الخدمة من جديد.')
    await state.update_data(page_start=start, page_end=end, page_selection_done=True)
    await state.set_state(AITutorStates.mode)
    await m.answer(f'✅ تم تحديد الصفحات: <b>{start}–{end}</b>', parse_mode='HTML')
    return await start_mode(m, state, mode)


@router.callback_query(F.data.in_(list(MODES)))
async def ai_mode_legacy(c: CallbackQuery, state: FSMContext):
    await c.answer('جارٍ التحضير...')
    await start_mode(c.message, state, c.data)

@router.callback_query(F.data.startswith('ai_mode:'))
async def ai_mode_bound(c: CallbackQuery, state: FSMContext):
    parts = (c.data or '').split(':')
    if len(parts) != 3 or parts[1] not in MODES:
        return await c.answer('الخدمة غير صالحة.', show_alert=True)
    try:
        session_id = int(parts[2])
    except (TypeError, ValueError):
        return await c.answer('انتهت صلاحية زر المادة. افتح 🤖 من جديد.', show_alert=True)
    # Resolve the exact id first, but fall back to the current interaction's
    # material or current durable session if the callback id became stale.
    material = await resolve_current_material(c.from_user.id, session_id=session_id)
    if not material:
        material = await _current_material(c.from_user.id, state)
    if not material:
        return await c.answer('انتهت صلاحية هذه المادة. أرسل/اختر مادة جديدة.', show_alert=True)
    real_session_id = material.get('session_id') or session_id
    await state.update_data(active_session_id=real_session_id, page_selection_done=False)
    await c.answer('جارٍ التحضير...')
    await start_mode(c.message, state, parts[1])

@router.callback_query(F.data.startswith('ai_exam_type:'))
async def ai_exam_type(c, state):
    value=c.data.split(':',1)[1]
    mapping={'mcq':'ai_mcq','tf':'ai_tf','qa':'ai_ask','mixed':'ai_mixed'}
    mode=mapping.get(value)
    if not mode:
        return await c.answer('نوع غير صالح', show_alert=True)
    await state.update_data(requested_mode=mode, exam_mode=True)
    await state.set_state(AITutorStates.count)
    await c.message.answer('🔢 <b>عدد أسئلة الامتحان:</b>', parse_mode='HTML', reply_markup=count_keyboard())
    await c.answer()


@router.callback_query(F.data.startswith('ai_count:'))
async def ai_count(c, state):
    value = c.data.split(':',1)[1]
    if value == 'custom':
        await state.set_state(AITutorStates.count)
        await c.message.answer('✏️ اكتب عدد الأسئلة/البطاقات المطلوب، مثل: 250')
        await c.answer()
        return
    try:
        count = int(value)
    except (TypeError, ValueError):
        return await c.answer('عدد غير صالح.', show_alert=True)
    if count < 1:
        return await c.answer('عدد غير صالح.', show_alert=True)
    await _run_count(c.message, state, min(count, INTERNAL_AI_ITEM_LIMIT))
    await c.answer()

@router.message(AITutorStates.count, F.text)
async def ai_custom_count(m, state):
    count = _validate_positive_count(m.text)
    if count is None:
        return await m.answer('❌ اكتب عددًا صحيحًا أكبر من صفر.')
    await _run_count(m, state, min(count, INTERNAL_AI_ITEM_LIMIT))

def _page_prompt_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📚 كامل المادة', callback_data='ai_pages:all')],
        [InlineKeyboardButton(text='📑 تحديد الصفحات', callback_data='ai_pages:custom')],
        [InlineKeyboardButton(text='🔄 تغيير المادة', callback_data='ai_change_material')],
        [InlineKeyboardButton(text='🏠 الرئيسية', callback_data='ai_home')],
    ])


def _selected_material_text(material, data):
    start = data.get('page_start')
    end = data.get('page_end')
    if material.get('has_pages') and start and end:
        selected = slice_pages(material['text'], int(start), int(end))
        return selected or material['text']
    return material['text']


async def _ensure_page_range(m, state, material, mode):
    """Require an explicit page scope for page-aware materials before AI work."""
    if not material.get('has_pages'):
        return True
    data = await state.get_data()
    if data.get('page_selection_done'):
        return True
    await state.update_data(pending_mode=mode, page_selection_done=False)
    await state.set_state(AITutorStates.page_range)
    await m.answer(
        f'📖 <b>{safe_html_escape(material["title"])}</b>\n\n'
        f'هذا الملف يحتوي على <b>{material["page_count"]}</b> صفحة.\n'
        'حدد نطاق الصفحات الذي تريد استخدامه في الخدمة الحالية:',
        parse_mode='HTML', reply_markup=_page_prompt_keyboard()
    )
    return False


async def start_mode(m, state, mode):
    # Always re-resolve the durable study session here. This is the final guard
    # against stale FSM states from older deployments/restarts.
    material = await _current_material(m.from_user.id, state)
    if not material:
        await state.clear()
        await state.set_state(AITutorStates.source)
        return await m.answer('📚 لا توجد مادة حالية. أرسل المادة أولًا.')
    if not await _ensure_page_range(m, state, material, mode):
        return
    if not ai_service.available:
        return await m.answer('❌ الذكاء الاصطناعي غير متاح حاليًا. تأكد من GEMINI_API_KEY.')
    await update_session_mode(m.from_user.id, mode)
    if mode == 'ai_mixed':
        await state.set_state(AITutorStates.mode)
        return await m.answer(
            f'📖 <b>المادة:</b> {safe_html_escape(material["title"])}\n\n📚 تم اعتماد النطاق المحدد لهذه الخدمة.\n\n📝 <b>نوع الامتحان:</b>',
            parse_mode='HTML', reply_markup=exam_type_keyboard()
        )
    if mode in COUNT_MODES:
        await state.set_state(AITutorStates.count)
        await state.update_data(requested_mode=mode)
        return await m.answer(
            f'📖 المادة الحالية: <b>{safe_html_escape(material["title"])}</b>\n'
            '📚 سيتم استخدام الصفحات التي حددتها لهذه الخدمة.\n\n'
            f'اختر العدد لخدمة <b>{safe_html_escape(MODES[mode])}</b>:', parse_mode='HTML', reply_markup=count_keyboard()
        )
    if mode == 'ai_explain':
        context = retrieve_context(_selected_material_text(material, await state.get_data()), max_chars=AI_SOURCE_CHARS)
        prompt = f'اشرح المادة للطالب بالعربية العراقية المبسطة، اعتمادًا على المادة فقط، مع ترتيب واضح وأمثلة عند الحاجة دون اختلاق.\n{context}'
        heading = '💡 شرح الدرس'
    elif mode == 'ai_summary':
        context = retrieve_context(_selected_material_text(material, await state.get_data()), max_chars=AI_SOURCE_CHARS)
        prompt = f'لخّص المادة بدقة بالعربية، مع أهم الأفكار والتعاريف والقوانين والنقاط الامتحانية، دون اختلاق.\n{context}'
        heading = '📚 تلخيص ومراجعة'
    else:
        return await m.answer('الخدمة غير معروفة.')
    try:
        ans = await asyncio.wait_for(ai_service.call(prompt), timeout=AI_TIMEOUT)
    except Exception as exc:
        await log_error('ai.text_mode', str(exc))
        ans = None
    if not ans:
        # One focused retry for text services. This handles a successful API
        # response with no printable candidate text without changing quiz logic.
        retry_prompt = (
            'أجب بالعربية بشكل واضح ومباشر، اعتمادًا على المادة فقط، دون اختلاق معلومات.\n'
            + ('اشرح الدرس بطريقة مبسطة للطالب مع ترتيب النقاط والأمثلة الضرورية.\n'
               if mode == 'ai_explain' else
               'لخّص المادة في نقاط مرتبة تشمل أهم الأفكار والتعاريف والقوانين والنقاط الامتحانية.\n')
            + context[:min(len(context), 30000)]
        )
        try:
            ans = await asyncio.wait_for(ai_service.call(retry_prompt), timeout=AI_TIMEOUT)
        except Exception as exc:
            await log_error('ai.text_mode.retry', str(exc))
            ans = None
    ans = clean_ai_text(ans or 'تعذر توليد النتيجة حاليًا.')
    await update_session_mode(m.from_user.id, mode)
    await record_event(m.from_user.id, 'explanation' if mode == 'ai_explain' else 'summary', metadata={'mode': mode})
    await _send_chunked(m, f'<b>{safe_html_escape(heading)}</b>\n\n{safe_html_escape(ans)}', reply_markup=session_actions())
    await state.clear(); await state.set_state(AITutorStates.ask)

async def _run_count(m, state, count):
    d = await state.get_data(); mode = d.get('requested_mode')
    if not mode:
        mode = (await _current_material(m.from_user.id) or {}).get('mode') or 'ai_ask'
    material = await _current_material(m.from_user.id, state)
    if not material:
        return await m.answer('لا توجد مادة حالية.')
    data = await state.get_data()
    source_text = _selected_material_text(material, data)
    if not source_text.strip():
        return await m.answer('لا توجد صفحات صالحة ضمن النطاق المحدد.')
    if not ai_service.available:
        return await m.answer('❌ الذكاء الاصطناعي غير متاح حاليًا.')
    if not rate_limiter.is_allowed(m.from_user.id):
        return await m.answer(f'⏳ انتظر {rate_limiter.get_wait_time(m.from_user.id)} ثانية قبل طلب ذكاء اصطناعي جديد.')
    status = await m.answer(f'🤖 جارٍ إعداد {count} عنصرًا على دفعات…')
    items = await generate_questions(source_text, mode, count, subject=material.get('subject'), title=material.get('title'))
    if not items:
        return await status.edit_text('❌ تعذر إنشاء عناصر من المادة الحالية. تحقق من النص أو حاول مرة أخرى.')
    actual = len(items)
    title_map = {'ai_ask':'🤖 أسئلة وأجوبة', 'ai_flash':'🃏 بطاقات مراجعة', 'ai_match':'🔗 مطابقة', 'ai_mcq':'📝 امتحان — اختيار من متعدد', 'ai_tf':'📝 امتحان — صح وخطأ', 'ai_short':'📝 أسئلة قصيرة', 'ai_fill':'📝 امتحان — أكمل الفراغ', 'ai_mixed':'📝 امتحان شامل'}
    heading = title_map.get(mode, '📝 امتحان')
    try:
        await status.delete()
    except Exception:
        pass
    await state.clear()
    if mode in QUIZ_MODES:
        # True interactive exam flow: answers are hidden until the student responds.
        await state.update_data(quiz_questions=items, quiz_heading=heading, quiz_requested=count, quiz_index=0, quiz_score=0, quiz_busy=False)
        await state.set_state(AITutorStates.quiz)
        await update_session_mode(m.from_user.id, mode)
        await record_event(m.from_user.id, 'exam', metadata={'mode': mode, 'requested': count, 'actual': actual})
        return await send_question(m, state)
    await state.update_data(generated_items=items, generated_heading=heading, generated_requested=count, generated_page=0)
    await state.set_state(AITutorStates.generated)
    await update_session_mode(m.from_user.id, mode)
    await record_event(m.from_user.id, 'question', metadata={'mode': mode, 'requested': count, 'actual': actual})
    await show_generated_page(m, state, 0)


GENERATED_PAGE_SIZE = 10

def generated_page_keyboard(page, total_pages):
    rows=[]
    nav=[]
    if page>0: nav.append(InlineKeyboardButton(text='⬅️ السابق', callback_data=f'ai_qpage:{page-1}'))
    nav.append(InlineKeyboardButton(text=f'📄 {page+1} من {max(1,total_pages)}', callback_data='noop'))
    if page<total_pages-1: nav.append(InlineKeyboardButton(text='التالي ➡️', callback_data=f'ai_qpage:{page+1}'))
    rows.append(nav)
    rows += [[InlineKeyboardButton(text='🔄 تغيير النوع', callback_data='ai_change_mode'), InlineKeyboardButton(text='🔄 تغيير المادة', callback_data='ai_change_material')],
             [InlineKeyboardButton(text='❓ سؤال جديد', callback_data='ai_new_question'), InlineKeyboardButton(text='❌ إنهاء', callback_data='ai_end_session')]]
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def show_generated_page(m, state, page):
    d=await state.get_data(); items=d.get('generated_items') or []
    if not items: return await m.answer('انتهت النتائج.')
    total_pages=max(1,(len(items)+GENERATED_PAGE_SIZE-1)//GENERATED_PAGE_SIZE)
    page=max(0,min(int(page),total_pages-1))
    start=page*GENERATED_PAGE_SIZE
    chunk=items[start:start+GENERATED_PAGE_SIZE]
    text=format_question_set(chunk, heading=d.get('generated_heading','🤖 النتائج'))
    if start==0 and len(items)<int(d.get('generated_requested') or len(items)):
        text=f'<b>ℹ️ المتاح فعليًا: {len(items)} من {d.get("generated_requested")}</b>\n\n'+text
    text=f'<b>📄 الصفحة {page+1} من {total_pages}</b>\n\n'+text
    await state.update_data(generated_page=page)
    await _send_chunked(m, text, reply_markup=generated_page_keyboard(page,total_pages))


def _quiz_result_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔄 تغيير النوع', callback_data='ai_change_mode'), InlineKeyboardButton(text='🔄 تغيير المادة', callback_data='ai_change_material')],
        [InlineKeyboardButton(text='❌ إنهاء', callback_data='ai_end_session')],
    ])

async def send_question(m, state):
    d = await state.get_data(); qs = d.get('quiz_questions', []); i = int(d.get('quiz_index', 0))
    if i >= len(qs):
        score = int(d.get('quiz_score', 0)); total = len(qs)
        await state.clear(); await state.set_state(AITutorStates.ask)
        return await m.answer(f'🏁 <b>انتهى الامتحان!</b>\n\n📊 النتيجة: <b>{score}/{total}</b>', parse_mode='HTML', reply_markup=session_actions())
    q = qs[i]
    text = f'📝 <b>السؤال {i+1}/{len(qs)}</b>\n\n{safe_html_escape(q["question"])}'
    if q.get('options'):
        return await m.answer(text, parse_mode='HTML', reply_markup=quiz_kb([safe_html_escape(x) for x in q['options']]))
    return await m.answer(text + '\n\n✍️ اكتب إجابتك:', parse_mode='HTML')

async def grade_choice(c, state, idx):
    async with _quiz_lock(c.from_user.id):
        d = await state.get_data(); qs = d.get('quiz_questions', []); i = int(d.get('quiz_index', 0))
        if i >= len(qs): return await c.answer('انتهى الامتحان.')
        if d.get('quiz_busy'): return await c.answer('⏳ انتظر قليلًا...')
        q = qs[i]; opts = q.get('options', [])
        if idx < 0 or idx >= len(opts): return await c.answer('خيار غير صالح.')
        await state.update_data(quiz_busy=True)
        ok = choice_correct(opts[idx], q.get('answer',''), idx)
        score = int(d.get('quiz_score',0)) + (1 if ok else 0)
        result = '✅ <b>صحيح!</b>' if ok else '❌ <b>خطأ</b>\n\n✅ <b>الإجابة الصحيحة:</b> ' + safe_html_escape(q.get('answer',''))
        if q.get('explanation'):
            result += '\n💡 <b>التوضيح:</b> ' + safe_html_escape(q['explanation'])
        await c.answer('صحيح ✅' if ok else 'خطأ ❌')
        await c.message.answer(result, parse_mode='HTML')
        await state.update_data(quiz_index=i+1, quiz_score=score, quiz_busy=False)
        await send_question(c.message, state)

@router.callback_query(F.data.startswith('quizans_'))
async def quiz_answer(c, state):
    try: idx = int(c.data[8:])
    except Exception: return await c.answer('خيار غير صالح.')
    await grade_choice(c, state, idx)

@router.callback_query(F.data.startswith('ai_qpage:'))
async def ai_qpage(c, state):
    try: page=int(c.data.split(':',1)[1])
    except Exception: return await c.answer('صفحة غير صالحة', show_alert=True)
    await c.answer()
    await show_generated_page(c.message, state, page)


@router.message(AITutorStates.generated, F.text)
async def generated_text(m, state):
    # Natural commands must keep working even after a generated question page.
    text = normalize_arabic(m.text or '').strip()
    mode = resolve_natural_mode(text)
    if mode:
        material = await _current_material(m.from_user.id)
        if not material:
            await state.clear(); await state.set_state(AITutorStates.source)
            return await m.answer('📚 لا توجد مادة حالية. أرسل المادة أولًا.')
        await state.set_state(AITutorStates.mode)
        return await start_mode(m, state, mode)
    await state.clear(); await state.set_state(AITutorStates.ask)
    material=await _current_material(m.from_user.id)
    if material:
        await answer_free_question(m,state,m.text.strip(),material)
    else:
        await m.answer('لا توجد مادة حالية.')


@router.message(AITutorStates.quiz, F.text)
async def typed(m, state):
    async with _quiz_lock(m.from_user.id):
        d = await state.get_data(); qs = d.get('quiz_questions', []); i = int(d.get('quiz_index',0))
        if i >= len(qs): return await send_question(m,state)
        q = qs[i]; expected = str(q.get('answer','')).strip(); given = m.text.strip()
        if len(given) > 3000:
            return await m.answer('الإجابة طويلة جدًا.')
        await state.update_data(quiz_busy=True)
        try:
            raw = await asyncio.wait_for(ai_service.call(
                f'قيّم إجابة الطالب اعتمادًا على السؤال والإجابة النموذجية. أعد JSON فقط: {{"correct":true/false,"feedback":"..."}}.\nالسؤال: {q["question"]}\nالإجابة النموذجية: {expected}\nإجابة الطالب: {given}'
            ), timeout=min(AI_TIMEOUT, 30))
        except Exception:
            raw = None
        dct = clean_json(raw)
        ok = bool(dct.get('correct')) if isinstance(dct,dict) and isinstance(dct.get('correct'),bool) else norm(given) == norm(expected)
        feedback = clean_ai_text(dct.get('feedback','')) if isinstance(dct,dict) else ''
        score = int(d.get('quiz_score',0)) + (1 if ok else 0)
        result = '✅ <b>إجابة صحيحة!</b>' if ok else '❌ <b>خطأ</b>\n\n✅ <b>الإجابة الصحيحة:</b> ' + safe_html_escape(expected)
        if feedback: result += '\n💡 <b>التوضيح:</b> ' + safe_html_escape(feedback)
        await m.answer(result, parse_mode='HTML')
        await state.update_data(quiz_index=i+1, quiz_score=score, quiz_busy=False)
        await send_question(m, state)

@router.message(AITutorStates.ask, F.text)
async def ask_continuous(m, state):
    text = normalize_arabic(m.text or '').strip()
    mode = resolve_natural_mode(text)
    if mode:
        material = await _current_material(m.from_user.id)
        if not material:
            await state.clear()
            await state.set_state(AITutorStates.source)
            return await m.answer('📚 لا توجد مادة حالية. أرسل المادة أولًا.')
        await state.set_state(AITutorStates.mode)
        return await start_mode(m, state, mode)
    material = await _current_material(m.from_user.id, state)
    if not material:
        return await m.answer('لا توجد مادة حالية. أرسل مادة جديدة.')
    await answer_free_question(m, state, m.text.strip(), material)
