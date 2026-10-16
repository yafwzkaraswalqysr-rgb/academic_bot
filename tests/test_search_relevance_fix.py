from services.search_engine import expand_query_terms, relevance


def test_data_science_bilingual_expansion():
    variants = expand_query_terms('علوم البيانات')
    assert 'علوم البيانات' in variants
    assert 'Data Science' in variants
    assert 'تحليل البيانات' in variants


def test_data_science_relevance_rejects_generic_data_result():
    good = relevance('علوم البيانات', 'مدخل إلى علوم البيانات', 'مفاهيم Data Science وتحليل البيانات')
    generic = relevance('علوم البيانات', 'Big Data Systems', 'distributed data processing')
    assert good > generic
    assert generic < 45


def test_english_and_arabic_data_science_are_close_in_relevance():
    ar = relevance('علوم البيانات', 'أساسيات علوم البيانات', 'Data Science and statistics')
    en = relevance('Data Science', 'Fundamentals of Data Science', 'علوم البيانات والإحصاء')
    assert ar > 45 and en > 45
    assert abs(ar - en) < 350
