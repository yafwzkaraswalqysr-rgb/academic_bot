from handlers.ai_tutor import choice_correct, clean_json
from services.search_engine import relevance

def test_choice_correct_zero_and_one_based():
    assert choice_correct('الخيار', 'الخيار', 0)
    assert choice_correct('الخيار', '1', 0)

def test_tf():
    assert choice_correct('صح','صح',0)
    assert not choice_correct('خطأ','صح',1)

def test_json_parser():
    assert clean_json('```json\n{"questions": []}\n```') == {'questions': []}

def test_relevance_code_language():
    assert relevance('لغة البرمجة C++','لغة البرمجة C++') > relevance('لغة البرمجة C++','لغة البرمجة باسكال')


def test_vocational_ministerial_only_third_stage():
    from school_catalog import is_ministerial_grade
    assert not is_ministerial_grade("🏫 إعدادية", "المرحلة الأولى", "مهني / الصناعي")
    assert not is_ministerial_grade("🏫 إعدادية", "المرحلة الثانية", "مهني / الصناعي")
    assert is_ministerial_grade("🏫 إعدادية", "المرحلة الثالثة", "مهني / الصناعي")


def test_school_menu_has_sixth_vocational_concept():
    from school_catalog import is_ministerial_grade
    assert is_ministerial_grade("🏫 إعدادية", "المرحلة الثالثة", "مهني / الحاسوب وتقنيات المعلومات")
