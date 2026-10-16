from school_catalog import is_ministerial_grade, school_grades, subjects_for


def test_vocational_third_stage_is_sixth_for_ministerial():
    assert not is_ministerial_grade("🏫 إعدادية", "المرحلة الأولى", "مهني / الصناعي")
    assert not is_ministerial_grade("🏫 إعدادية", "المرحلة الثانية", "مهني / الصناعي")
    assert is_ministerial_grade("🏫 إعدادية", "المرحلة الثالثة", "مهني / الصناعي")


def test_vocational_subjects_stay_stage_based():
    subjects = subjects_for("🏫 إعدادية", "المرحلة الثالثة", "مهني / الحاسوب وتقنيات المعلومات", "الحاسوب وتقنيات المعلومات", "صيانة وتجميع الحاسوب", "المرحلة الثالثة")
    assert "صيانة الحاسوب" in subjects


def test_school_grades_are_separate_for_scientific_and_literary():
    grades = school_grades("🏫 إعدادية")
    assert grades == ["الرابع العلمي", "الخامس العلمي", "السادس العلمي", "الرابع الأدبي", "الخامس الأدبي", "السادس الأدبي"]
