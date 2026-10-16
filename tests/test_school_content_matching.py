from types import SimpleNamespace

from services.school_content import placement_matches_profile


def test_vocational_phase_matches_sixth_professional_placement():
    profile = SimpleNamespace(
        stage='🏫 إعدادية', grade='المرحلة الثالثة',
        branch='مهني / الحاسوب وتقنيات المعلومات', department='صيانة وتجميع الحاسوب'
    )
    placement = SimpleNamespace(
        stage='المرحلة الثالثة', grade='السادس المهني',
        branch='مهني / الحاسوب وتقنيات المعلومات', department='صيانة وتجميع الحاسوب',
        subject='الطبيعيات'
    )
    assert placement_matches_profile(profile, placement, 'الطبيعيات')


def test_wrong_vocational_phase_does_not_match():
    profile = SimpleNamespace(
        stage='🏫 إعدادية', grade='المرحلة الثالثة',
        branch='مهني / الحاسوب وتقنيات المعلومات', department='صيانة وتجميع الحاسوب'
    )
    placement = SimpleNamespace(
        stage='المرحلة الثانية', grade='السادس المهني',
        branch='مهني / الحاسوب وتقنيات المعلومات', department='صيانة وتجميع الحاسوب',
        subject='الطبيعيات'
    )
    assert not placement_matches_profile(profile, placement, 'الطبيعيات')
