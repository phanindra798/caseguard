from datetime import date

from caseguard_agent.business_days import business_days_late
from caseguard_agent.policies import load_policies, search_policy


def test_business_days_skip_weekends():
    friday, monday = date(2026, 11, 13), date(2026, 11, 16)
    assert business_days_late(friday, monday) == 1
    assert business_days_late(monday, monday) == 0
    assert business_days_late(monday, friday) == 0                 # not late yet
    assert business_days_late(date(2026, 11, 6), date(2026, 11, 16)) == 6


def test_business_days_skip_holidays():
    assert business_days_late(date(2026, 11, 13), date(2026, 11, 17), holidays=[date(2026, 11, 16)]) == 1


def test_expired_policy_is_never_selected_today():
    p = search_policy(load_policies(), "late delivery escalation", "US", date(2026, 11, 18))
    assert p.ref == "ESC-3@v2"


def test_policy_in_force_on_a_past_date():
    p = search_policy(load_policies(), "late delivery escalation", "US", date(2026, 3, 1))
    assert p.ref == "ESC-3@v1"


def test_region_filter_beats_keyword_match():
    p = search_policy(load_policies(), "late delivery escalation US", "EU", date(2026, 11, 18))
    assert p.ref == "ESC-4@v1"


def test_no_policy_for_unknown_region():
    assert search_policy(load_policies(), "late", "APAC", date(2026, 11, 18)) is None
