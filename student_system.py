from __future__ import annotations
import json
from pathlib import Path

from config import BASE_DIR

BUTTONS_FILE = BASE_DIR / 'buttons.json'

DEFAULT_BUTTONS = [
    {'key':'school','label':'🏫 مدرستي','enabled':True,'order':10,'action':'builtin:school'},
    {'key':'university','label':'🎓 الجامعة','enabled':True,'order':20,'action':'builtin:university'},
    {'key':'search','label':'🔎 بحث','enabled':True,'order':30,'action':'builtin:search'},
    {'key':'graduation','label':'🎓 مشروع التخرج','enabled':True,'order':40,'action':'builtin:graduation'},
    {'key':'ai','label':'🤖 اسأل الذكاء الاصطناعي','enabled':True,'order':50,'action':'builtin:ai'},
    {'key':'study','label':'📚 دراستي','enabled':True,'order':60,'action':'builtin:study'},
    {'key':'library','label':'🗂 مكتبتي','enabled':True,'order':70,'action':'builtin:library'},
    {'key':'saved','label':'⭐ المحفوظات','enabled':True,'order':80,'action':'builtin:saved'},
    {'key':'account','label':'👤 حسابي','enabled':True,'order':90,'action':'builtin:account'},
    {'key':'help','label':'ℹ️ المساعدة','enabled':True,'order':100,'action':'builtin:help'},
    {'key':'delete_account','label':'🗑 حذف حسابي','enabled':True,'order':110,'action':'builtin:delete_account'},
    {'key':'home','label':'🏠 القائمة الرئيسية','enabled':True,'order':120,'action':'builtin:home'},
]


def load_buttons():
    try:
        data=json.loads(BUTTONS_FILE.read_text(encoding='utf-8'))
        if isinstance(data,list):
            by={str(x.get('key')):x for x in data if isinstance(x,dict) and x.get('key')}
            out=[]
            for d in DEFAULT_BUTTONS:
                x=dict(d); x.update(by.get(d['key'],{})); out.append(x)
            for x in data:
                if isinstance(x,dict) and str(x.get('key')) not in {d['key'] for d in DEFAULT_BUTTONS}:
                    out.append(x)
            return sorted(out,key=lambda x:int(x.get('order',9999)))
    except Exception:
        pass
    return DEFAULT_BUTTONS.copy()

def save_buttons(items):
    BUTTONS_FILE.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')

def reset_buttons(): save_buttons(DEFAULT_BUTTONS)
