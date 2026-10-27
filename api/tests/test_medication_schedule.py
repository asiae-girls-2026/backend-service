from datetime import date, datetime, timezone

import pytest

from app.services.medication_schedule import (
    ScheduleValidationError,
    iter_occurrence_times,
    normalize_schedule,
    utc_bounds,
)


def test_normalize_schedule_sorts_and_preserves_korean_timezone():
    result = normalize_schedule(
        {"times": ["20:00", "08:00"], "repeat_days": [6, 0], "timezone": "Asia/Seoul"}
    )
    assert result == {
        "times": ["08:00", "20:00"],
        "repeat_days": [0, 6],
        "timezone": "Asia/Seoul",
    }


@pytest.mark.parametrize(
    "schedule",
    [
        {"times": [], "repeat_days": [0]},
        {"times": ["8:00"], "repeat_days": [0]},
        {"times": ["08:00", "08:00"], "repeat_days": [0]},
        {"times": ["08:00"], "repeat_days": [7]},
        {"times": ["08:00"], "repeat_days": [0], "timezone": "No/Such_Zone"},
    ],
)
def test_invalid_schedule_is_rejected(schedule):
    with pytest.raises(ScheduleValidationError):
        normalize_schedule(schedule)


def test_occurrence_generation_uses_local_weekday_and_utc_storage():
    # 2026-10-26은 월요일. 09:00 KST는 00:00 UTC다.
    values = iter_occurrence_times(
        {"times": ["09:00"], "repeat_days": [0], "timezone": "Asia/Seoul"},
        date(2026, 10, 26),
        date(2026, 11, 1),
    )
    assert values == [datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)]


def test_new_registration_does_not_create_past_occurrence():
    values = iter_occurrence_times(
        {"times": ["09:00", "20:00"], "repeat_days": [0], "timezone": "Asia/Seoul"},
        date(2026, 10, 26),
        date(2026, 10, 26),
        not_before=datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc),
    )
    assert values == [datetime(2026, 10, 26, 11, 0, tzinfo=timezone.utc)]


def test_utc_bounds_are_half_open():
    start, end = utc_bounds(date(2026, 10, 26), date(2026, 10, 26), "Asia/Seoul")
    assert start == datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)
