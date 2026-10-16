from __future__ import annotations
import asyncio
import json
import unicodedata
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from services.ai_service import ai_service
from services.ai_parsing import clean_json
from services.error_service import log_error
from services.study_session import get_session, save_session, update_session_mode, clear_session
from utils.helpers import get_main_menu_for_user, truncate, chunk_text
from utils.security import safe_html_escape
from utils.text import clean_ai_text, normalize_arabic
from config import AI_TIMEOUT, AI_QUIZ_BATCH_SIZE, AI_SOURCE_CHARS, AI_ASK_CHARS, MAX_QUERY_LENGTH
from services.usage_service import record_event

router = Router(name='ai_tutor')

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


def mode_keyboard():
    items = list(MODES.items())
    rows = []
    for i in range(0, len(items), 2):
        row = [InlineKeyboardButton(text=items[i][1], callback_data=items[i][0])]
        if i + 1 < len(items):
            row.append(InlineKeyboardButton(text=items[i+1][1], callback_data=items[i+1][0]))
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
    if a.isdigit() and (int(a) == idx or int(a) == idx + 1):
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
    data = clean_json(raw)
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ('questions','items','cards','results'):
        if isinstance(data.get(key), list):
            return data[key]
    return []


def _normalize_question(q, kind):
    if not isinstance(q, dict):
        return None
    question = clean_ai_text(q.get('question') or q.get('prompt') or '')
    answer = clean_ai_text(q.get('answer') or q.get('correct_answer') or '')
    if not question or not answer:
        return None
    typ = str(q.get('type') or '').lower().strip()
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
    opts = [clean_ai_text(x) for x in (q.get('options') or []) if str(x).strip()]
    if typ == 'tf':
        opts = ['صح', 'خطأ']
    if typ == 'mcq' and len(opts) < 2:
        return None
    return {'type': typ, 'question': question, 'answer': answer, 'options': opts,
            'explanation': clean_ai_text(q.get('explanation') or q.get('feedback') or '')}


async def generate_questions(source, kind, count):
    if not ai_service.available or count <= 0:
        return []
    all_questions = []
    seen = set()
    empty_rounds = 0
    source_text = source[:AI_SOURCE_CHARS]
    while len(all_questions) < count:
        batch_count = min(AI_QUIZ_BATCH_SIZE, count - len(all_questions))
        previous = '\n'.join(q['question'] for q in all_questions[-30:])
        labels = {
            'ai_ask': 'سؤال وجواب: أعد سؤالاً وإجابة نموذجية من المادة.',
            'ai_mcq': 'اختيار من متعدد: أربعة خيارات وإجابة واحدة صحيحة.',
            'ai_tf': 'صح أو خطأ: options يجب أن تكون [صح, خطأ].',
            'ai_short': 'أسئلة قصيرة بإجابة نموذجية واضحة.',
            'ai_fill': 'أكمل الفراغ بإجابة محددة مستمدة من المادة.',
            'ai_match': 'مطابقة: مصطلح أو فكرة مع التعريف/المطابقة الصحيحة.',
            'ai_flash': 'بطاقات مراجعة: سؤال مباشر وإجابة مختصرة.',
            'ai_mixed': 'امتحان مختلط من MCQ وTF وshort وfill.',
        }[kind]
        prompt = f'''أنشئ حتى {batch_count} عنصرًا فقط من المادة المرفقة. {labels}
اعتمد على المادة فقط، ولا تخترع معلومات. إذا كانت المادة لا تكفي، أرجع العدد الممكن فقط.
أعد JSON فقط بهذا الشكل: {{"questions":[{{"type":"mcq|tf|short|fill|qa|flash|match","question":"...","options":["..."],"answer":"...","explanation":"..."}}]}}
لا تضع أي كلام قبل JSON أو بعده.
العناصر التي سبقت، لتجنب التكرار:
{previous[:5000]}
المادة:
{source_text}'''
        try:
            raw = await asyncio.wait_for(ai_service.call(prompt), timeout=AI_TIMEOUT)
        except Exception as exc:
            await log_error('ai.generate_questions', str(exc))
            raw = None
        batch = [_normalize_question(q, kind) for q in _extract_questions(raw)]
        batch = [q for q in batch if q]
        new_count = 0
        for q in batch:
            key = _question_key(q)
            if key and key not in seen:
                seen.add(key)
                all_questions.append(q)
                new_count += 1
                if len(all_questions) >= count:
                    break
        if new_count == 0:
            empty_rounds += 1
            if empty_rounds >= 2:
                break
        else:
            empty_rounds = 0
    return all_questions


async def generate_quiz(source, count=10, kind='ai_mixed'):
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
        if q.get('options'):
            for j, opt in enumerate(q['options']):
                lines.append(f'{chr(65+j)}) {safe_html_escape(opt)}')
        lines.append(f'✅ <b>الجواب:</b> {safe_html_escape(q["answer"])}')
        if q.get('explanation'):
            lines.append(f'💡 <b>التوضيح:</b> {safe_html_escape(q["explanation"])}')
        lines.append('')
    return '\n'.join(lines)


async def _send_chunked(m, text, reply_markup=None):
    parts = chunk_text(text, 3500)
    for i, part in enumerate(parts):
        await m.answer(part, parse_mode='HTML', reply_markup=reply_markup if i == len(parts)-1 else None)


async def _current_material(uid, state: FSMContext = None):
    row = await get_session(uid)
    if row and row.source_text:
        return {
            'title': row.source_title, 
            'text': row.source_text, 
            'section': row.section,
            'content_id': row.source_content_id, 
            'content_type': row.source_content_type,
            'mode': row.current_mode
        }
    if state:
        d = await state.get_data()
        fallback_text = d.get('pending_text_content') or "ملف تعليمي"
        fallback_title = d.get('pending_filename') or d.get('default_title') or "مادة"
        if fallback_text:
            return {
                'title': fallback_title,
                'text': fallback_text,
                'section': 'school',
                'content_id': None,
                'content_type': 'pdf',
                'mode': None
            }
    return None


async def _save_material(uid, *, title, text, section='university', content_id=None, content_type=None):
    text = (text or '').strip()
    if not text:
        return None
    return await save_session(uid, title=title or 'مادة', text=text, section=section, content_id=content_id, content_type=content_type, mode=None)


async def _show_mode_menu(m, uid, state: FSMContext = None):
    material = await _current_material(uid, state)
    if not material:
        await m.answer('📚 لا توجد مادة محفوظة حاليًا. أرسل المادة أولًا.')
        return False
    await m.answer(
        f'📖 <b>المادة الحالية:</b> {safe_html_escape(material["title"])}\n\n'
        '✅ المادة محفوظة للجلسة. تگدر تسأل أكثر من مرة وتغيّر نوع الخدمة بدون إعادة إرسالها.\n\n'
        'اختَر الخدمة:', parse_mode='HTML', reply_markup=mode_keyboard()
    )
    return True


@router.message(F.text == '🤖 اسأل الذكاء الاصطناعي')
async def ai_start(m: Message, state: FSMContext, source=None, preferred_mode=None):
    await state.clear()
    if source:
        await _save_material(m.from_user.id, title='مادة', text=source)
    material = await _current_material(m.from_user.id, state)
    if material:
        if preferred_mode:
            await state.set_state(AITutorStates.mode)
            return await start_mode(m, state, preferred_mode)
        await state.set_state(AITutorStates.mode)
        return await _show_mode_menu(m, m.from_user.id, state)
    await state.set_state(AITutorStates.source)
    if preferred_mode:
        await state.update_data(preferred_mode=preferred_mode)
    await m.answer('🤖 <b>أرسل المادة</b>\n\nأرسل اسم المادة أو الصق النص، أو أرسل ملفًا ثم اختر الخدمة.',
                   parse_mode='HTML', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='❌ إلغاء', callback_data='ai_home')]]))


@router.callback_query(F.data.startswith('study_content:'))
async def study_content(c: CallbackQuery, state: FSMContext):
    from database import async_session_maker
    from models import SchoolContent
    from sqlalchemy import select
    if not c.from_user:
        return await c.answer()
    cid = int(c.data.split(':',1)[1])
    async with async_session_maker() as s:
        item = await s.scalar(select(SchoolContent).where(SchoolContent.id == cid))
    if not item:
        return await c.answer('المادة غير موجودة', show_alert=True)
    if not item.text_content:
        return await c.answer('هذه المادة لا تحتوي نصًا مستخرجًا يمكن استخدامه حاليًا.', show_alert=True)
    await _save_material(c.from_user.id, title=item.title, text=item.text_content,
                         section='school', content_id=item.id, content_type=item.content_type)
    await state.clear(); await state.set_state(AITutorStates.mode)
    await c.message.answer(f'📖 <b>تم حفظ المادة:</b> {safe_html_escape(item.title)}\n\nاختَر الخدمة:',
                            parse_mode='HTML', reply_markup=mode_keyboard())
    await c.answer()


@router.message(AITutorStates.source, F.text)
async def ai_source(m: Message, state: FSMContext):
    d = await state.get_data()
    text = m.text.strip()
    if d.get('mode') == 'ai_ask':
        material = await _current_material(m.from_user.id, state)
        if not material:
            return await m.answer('انتهت المادة الحالية. أرسل المادة من جديد.')
        await answer_free_question(m, state, text, material)
        return
    if len(text) < 2 or len(text) > 10000:
        return await m.answer('اكتب نصًا بين حرفين و10000 حرف، أو ارفع ملفًا.')
    await _save_material(m.from_user.id, title=text[:120], text=text)
    await state.clear(); await state.set_state(AITutorStates.mode)
    preferred = d.get('preferred_mode')
    if preferred:
        return await start_mode(m, state, preferred)
    await _show_mode_menu(m, m.from_user.id, state)


async def answer_free_question(m, state, question, material):
    if len(question) < 2:
        return await m.answer('اكتب سؤالك عن المادة.')
    prompt = f'أجب اعتمادًا على المادة فقط. إذا لم تكف المادة، صرّح بذلك. لا تخترع معلومات.\nالمادة:\n{material["text"][:AI_ASK_CHARS]}\nالسؤال:\n{question[:3000]}'
    try:
        ans = await asyncio.wait_for(ai_service.call(prompt), timeout=AI_TIMEOUT)
    except Exception as exc:
        await log_error('ai.ask', str(exc), user_id=None)
        ans = None
    ans = clean_ai_text(ans or 'تعذر توليد الإجابة حاليًا.')
    await update_session_mode(m.from_user.id, 'ai_ask')
    await record_event(m.from_user.id, 'question', metadata={'mode': 'ai_ask'})
    await m.answer('🤖 <b>الإجابة</b>\n\n' + safe_html_escape(ans), parse_mode='HTML', reply_markup=session_actions())


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
    await state.clear(); await state.set_state(AITutorStates.mode)
    await c.message.answer('🔄 اختر نوع الخدمة للمادة الحالية:', reply_markup=mode_keyboard())
    await c.answer()

@router.callback_query(F.data == 'ai_new_question')
async def ai_new_question(c, state):
    material = await _current_material(c.from_user.id, state)
    if not material:
        return await c.answer('لا توجد مادة حالية.', show_alert=True)
    await state.clear(); await state.set_state(AITutorStates.ask); await update_session_mode(c.from_user.id, 'ai_ask')
    await c.message.answer(f'❓ اكتب سؤالًا عن <b>{safe_html_escape(material["title"])}</b>:', parse_mode='HTML')
    await c.answer()

@router.callback_query(F.data.in_(list(MODES)))
async def ai_mode(c: CallbackQuery, state: FSMContext):
    material = await _current_material(c.from_user.id, state)
    if not material:
        return await c.answer('📚 لا توجد مادة حالية. أرسل المادة أولًا.', show_alert=True)
    
    await state.update_data(pending_mode=c.data)
    await state.set_state(AITutorStates.page_range)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📖 الملف بالكامل', callback_data='pages_all')],
        [InlineKeyboardButton(text='❌ إلغاء', callback_data='ai_change_mode')]
    ])
    await c.message.answer(
        '📄 <b>تحديد نطاق الصفحات</b>\n\n'
        'أدخل رقم الصفحة التي تريد البدء منها والانهاء عندها (مثال: <code>1-3</code>)،\n'
        'أو اضغط على الزر أدناه لاستخدام الملف بالكامل:',
        parse_mode='HTML',
        reply_markup=kb
    )
    await c.answer()

@router.callback_query(F.data == 'pages_all', AITutorStates.page_range)
async def pages_all(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    mode = d.get('pending_mode')
    await c.answer('جارٍ التحضير...')
    await start_mode(c.message, state, mode)

@router.message(AITutorStates.page_range, F.text)
async def page_range_input(m: Message, state: FSMContext):
    d = await state.get_data()
    mode = d.get('pending_mode')
    await start_mode(m, state, mode)


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
    await _run_count(c.message, state, int(value))
    await c.answer()

@router.message(AITutorStates.count, F.text)
async def ai_custom_count(m, state):
    count = _validate_positive_count(m.text)
    if count is None:
        return await m.answer('❌ اكتب عددًا صحيحًا أكبر من صفر.')
    await _run_count(m, state, count)

async def start_mode(m, state, mode):
    material = await _current_material(m.from_user.id, state)
    if not material:
        return await m.answer('📚 لا توجد مادة حالية. أرسل المادة أولًا.')
    if not ai_service.available:
        return await m.answer('❌ الذكاء الاصطناعي غير متاح حاليًا. تأكد من GEMINI_API_KEY.')
    await update_session_mode(m.from_user.id, mode)
    if mode == 'ai_mixed':
        await state.set_state(AITutorStates.mode)
        return await m.answer(
            f'📖 <b>المادة:</b> {safe_html_escape(material["title"])}\n\n📝 <b>نوع الامتحان:</b>',
            parse_mode='HTML', reply_markup=exam_type_keyboard()
        )
    if mode in COUNT_MODES:
        await state.set_state(AITutorStates.count)
        await state.update_data(requested_mode=mode)
        return await m.answer(
            f'📖 المادة الحالية: <b>{safe_html_escape(material["title"])}</b>\n\n'
            f'اختر العدد لخدمة <b>{safe_html_escape(MODES[mode])}</b>:', parse_mode='HTML', reply_markup=count_keyboard()
        )
    if mode == 'ai_explain':
        prompt = f'اشرح المادة للطالب بالعربية العراقية المبسطة، اعتمادًا على المادة فقط، مع ترتيب واضح وأمثلة عند الحاجة دون اختلاق.\n{material["text"][:AI_SOURCE_CHARS]}'
        heading = '💡 شرح الدرس'
    elif mode == 'ai_summary':
        prompt = f'لخّص المادة بدقة بالعربية، مع أهم الأفكار والتعاريف والقوانين والنقاط الامتحانية، دون اختلاق.\n{material["text"][:AI_SOURCE_CHARS]}'
        heading = '📚 تلخيص ومراجعة'
    else:
        return await m.answer('الخدمة غير معروفة.')
    try:
        ans = await asyncio.wait_for(ai_service.call(prompt), timeout=AI_TIMEOUT)
    except Exception as exc:
        await log_error('ai.text_mode', str(exc))
        ans = None
    ans = clean_ai_text(ans or 'تعذر توليد النتيجة حاليًا.')
    await update_session_mode(m.from_user.id, mode)
    await record_event(m.from_user.id, 'explanation' if mode == 'ai_explain' else 'summary', metadata={'mode': mode})
    await _send_chunked(m, f'<b>{safe_html_escape(heading)}</b>\n\n{safe_html_escape(ans)}', reply_markup=session_actions())
    await state.clear(); await state.set_state(AITutorStates.ask)

async def _run_count(m, state, count):
    d = await state.get_data(); mode = d.get('requested_mode')
    if not mode:
        mode = (await _current_material(m.from_user.id, state) or {}).get('mode') or 'ai_ask'
    material = await _current_material(m.from_user.id, state)
    if not material:
        return await m.answer('لا توجد مادة حالية.')
    if not ai_service.available:
        return await m.answer('❌ الذكاء الاصطناعي غير متاح حاليًا.')
    status = await m.answer(f'🤖 جارٍ إعداد {count} عنصرًا على دفعات…')
    items = await generate_questions(material['text'], mode, count)
    if not items:
        return await status.edit_text('❌ تعذر إنشاء عناصر من المادة الحالية. تحقق من النص أو حاول مرة أخرى.')
    actual = len(items)
    title_map = {'ai_ask':'🤖 أسئلة وأجوبة', 'ai_flash':'🃏 بطاقات مراجعة', 'ai_match':'🔗 مطابقة', 'ai_mcq':'📝 امتحان — اختيار من متعدد', 'ai_tf':'📝 امتحان — صح وخطأ', 'ai_short':'📝 امتحان — أسئلة قصيرة', 'ai_fill':'📝 امتحان — أكمل الفراغ', 'ai_mixed':'📝 امتحان شامل'}
    heading = title_map.get(mode, '📝 امتحان')
    try:
        await status.delete()
    except Exception:
        pass
    await state.clear()
    await state.update_data(generated_items=items, generated_heading=heading, generated_requested=count, generated_page=0)
    await state.set_state(AITutorStates.generated)
    await update_session_mode(m.from_user.id, mode)
    await record_event(m.from_user.id, 'exam' if mode in QUIZ_MODES or d.get('exam_mode') else 'question', metadata={'mode': mode, 'requested': count, 'actual': actual})
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


@router.callback_query(F.data.startswith('ai_qpage:'))
async def ai_qpage(c, state):
    try: page=int(c.data.split(':',1)[1])
    except Exception: return await c.answer('صفحة غير صالحة', show_alert=True)
    await c.answer()
    await show_generated_page(c.message, state, page)


@router.message(AITutorStates.generated, F.text)
async def generated_text(m, state):
    await state.clear(); await state.set_state(AITutorStates.ask)
    material=await _current_material(m.from_user.id, state)
    if material:
        await answer_free_question(m,state,m.text.strip(),material)
    else:
        await m.answer('لا توجد مادة حالية.')


@router.message(AITutorStates.ask, F.text)
async def ask_continuous(m, state):
    material = await _current_material(m.from_user.id, state)
    if not material:
        return await m.answer('لا توجد مادة حالية. أرسل مادة جديدة.')
    await answer_free_question(m, state, m.text.strip(), material)
