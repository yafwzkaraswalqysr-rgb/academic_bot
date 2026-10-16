import asyncio, os

def test_imports():
    import config, models, database
    from utils import security, helpers, text
    from services import ai_service, search_engine, reference_service
    from sources import base, registry

def test_text_normalization():
    from utils.text import normalize_arabic
    assert normalize_arabic('أحمد')=='احمد'
    assert normalize_arabic('مُحَمَّد')=='محمد'

def test_security(tmp_path):
    from utils.security import sanitize_filename,is_safe_path
    assert sanitize_filename('file<>.txt')=='file__.txt'
    assert not is_safe_path('/etc/passwd')
    p=tmp_path/'a.txt';p.write_text('x');assert is_safe_path(p,tmp_path)

def test_reference():
    from services.reference_service import generate_reference
    ref=generate_reference({'title':'Test','author':'Author','year':'2024'},'APA')
    assert 'Author' in ref and '2024' in ref
