from aiogram import Router,F
from aiogram.types import Message,InlineKeyboardMarkup,InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from handlers.ai_tutor import AITutorStates,mode_keyboard
from utils.helpers import get_main_menu, get_main_menu_for_user
router=Router()
@router.message(F.text=='📚 دراستي')
async def study(m,state):
    await state.clear()
    kb=InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text='🤖 اسأل الذكاء الاصطناعي',callback_data='study_ai')],
      [InlineKeyboardButton(text='📝 امتحان سريع',callback_data='study_quick')],
      [InlineKeyboardButton(text='🃏 بطاقات مراجعة',callback_data='study_cards')],
      [InlineKeyboardButton(text='💡 شرح درس',callback_data='study_explain')],
      [InlineKeyboardButton(text='📚 تلخيص',callback_data='study_summary')],
      [InlineKeyboardButton(text='🏠 الرئيسية',callback_data='main_menu')]])
    await m.answer('📚 <b>دراستي</b>\n\nكل أدوات الدراسة والامتحانات والذكاء الاصطناعي بمكان واحد.\n\nاختَر الخدمة:',parse_mode='HTML',reply_markup=kb)
@router.callback_query(F.data=='study_ai')
async def study_ai(c,state):
    from handlers.ai_tutor import ai_start
    await ai_start(c.message,state); await c.answer()
@router.callback_query(F.data.in_({'study_quick','study_cards','study_explain','study_summary'}))
async def shortcut(c,state):
    mode={'study_quick':'ai_mixed','study_cards':'ai_flash','study_explain':'ai_explain','study_summary':'ai_summary'}[c.data]
    await state.clear(); await state.set_state(AITutorStates.source); await state.update_data(preferred_mode=mode)
    await c.message.answer('📚 أرسل اسم المادة أو الصق نص الدرس، وسأبدأ مباشرة بالخدمة التي اخترتها.\n\n❌ للإلغاء: /start')
    await c.answer()
