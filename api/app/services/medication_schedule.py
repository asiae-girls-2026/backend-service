"""복약 반복 일정으로 서버 예정 회차를 생성하는 공용 서비스.

요일은 Python ``date.weekday()``와 동일하게 0=월요일 ... 6=일요일이다.
저장 시각은 timezone-aware datetime이며 PostgreSQL ``timestamptz``에 기록한다.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = os.getenv("MEDICATION_DEFAULT_TIMEZONE", "Asia/Seoul")
DEFAULT_HORIZON_DAYS = int(os.getenv("MEDICATION_OCCURRENCE_HORIZON_DAYS", "30"))


class ScheduleValidationError(ValueError):
    pass


def timezone_for(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleValidationError(f"지원하지 않는 timezone입니다: {name}") from exc


def validate_hhmm(value: str) -> str:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ScheduleValidationError("시간은 HH:MM 형식이어야 합니다.") from exc
    if parsed.second or parsed.microsecond or len(value) != 5:
        raise ScheduleValidationError("시간은 HH:MM 형식이어야 합니다.")
    return value


def normalize_schedule(schedule: dict | None) -> dict:
    schedule = dict(schedule or {})
    times = schedule.get("times") or []
    repeat_days = schedule.get("repeat_days")
    if repeat_days is None:
        repeat_days = list(range(7))
    timezone_name = schedule.get("timezone") or DEFAULT_TIMEZONE

    if not isinstance(times, list) or not times:
        raise ScheduleValidationError("복약 시간은 한 개 이상 필요합니다.")
    if len(times) > 24:
        raise ScheduleValidationError("복약 시간은 최대 24개입니다.")
    if not isinstance(repeat_days, list) or not repeat_days:
        raise ScheduleValidationError("반복 요일은 한 개 이상 필요합니다.")
    if any(type(day) is not int or day < 0 or day > 6 for day in repeat_days):
        raise ScheduleValidationError("요일은 0~6이어야 합니다.")

    normalized_times = sorted(validate_hhmm(str(value)) for value in times)
    normalized_days = sorted(repeat_days)
    if len(normalized_times) != len(set(normalized_times)):
        raise ScheduleValidationError("복약 시간이 중복되었습니다.")
    if len(normalized_days) != len(set(normalized_days)):
        raise ScheduleValidationError("요일이 중복되었습니다.")
    timezone_for(str(timezone_name))

    return {
        "times": normalized_times,
        "repeat_days": normalized_days,
        "timezone": str(timezone_name),
    }


def utc_bounds(local_from: date, local_to: date, timezone_name: str) -> tuple[datetime, datetime]:
    if local_to < local_from:
        raise ScheduleValidationError("종료일은 시작일보다 빠를 수 없습니다.")
    tz = timezone_for(timezone_name)
    start = datetime.combine(local_from, time.min, tzinfo=tz)
    end = datetime.combine(local_to + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def iter_occurrence_times(
    schedule: dict,
    local_from: date,
    local_to: date,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    not_before: datetime | None = None,
) -> list[datetime]:
    normalized = normalize_schedule(schedule)
    tz = timezone_for(normalized["timezone"])
    first = max(local_from, start_date) if start_date else local_from
    last = min(local_to, end_date) if end_date else local_to
    if last < first:
        return []

    threshold = not_before.astimezone(timezone.utc) if not_before else None
    results: list[datetime] = []
    current = first
    allowed_days = set(normalized["repeat_days"])
    while current <= last:
        if current.weekday() in allowed_days:
            for hhmm in normalized["times"]:
                hour, minute = map(int, hhmm.split(":"))
                value = datetime.combine(current, time(hour, minute), tzinfo=tz).astimezone(timezone.utc)
                if threshold is None or value >= threshold:
                    results.append(value)
        current += timedelta(days=1)
    return results


def generate_for_medication(
    conn,
    *,
    user_medication_id: int,
    user_id: str,
    schedule: dict,
    schedule_revision: int,
    start_date: date | None,
    end_date: date | None,
    local_from: date,
    local_to: date,
    not_before: datetime | None = None,
) -> int:
    values = iter_occurrence_times(
        schedule,
        local_from,
        local_to,
        start_date=start_date,
        end_date=end_date,
        not_before=not_before,
    )
    if not values:
        return 0

    payload = json.dumps([{"scheduled_at": value.isoformat()} for value in values])
    rows = conn.execute(
        """
        WITH incoming AS (
            SELECT scheduled_at
            FROM jsonb_to_recordset(%s::jsonb)
                 AS x(scheduled_at timestamptz)
        )
        INSERT INTO medication_occurrence (
            user_medication_id,
            user_id,
            scheduled_at,
            occurrence_revision,
            status
        )
        SELECT %s, %s, scheduled_at, %s, 'SCHEDULED'
        FROM incoming
        ON CONFLICT (user_medication_id, scheduled_at, occurrence_revision)
        DO NOTHING
        RETURNING occurrence_id
        """,
        (payload, user_medication_id, user_id, schedule_revision),
    ).fetchall()
    return len(rows)


def ensure_user_occurrences(
    conn,
    *,
    user_id: str,
    local_from: date,
    local_to: date,
    timezone_name: str,
    not_before: datetime | None = None,
) -> int:
    # 조회 timezone은 조회 구간 계산에만 사용한다. 각 등록 일정은 자체 timezone으로 생성한다.
    timezone_for(timezone_name)
    rows = conn.execute(
        """
        SELECT
            user_medication_id,
            schedule_json,
            schedule_revision,
            start_date,
            end_date
        FROM user_medication
        WHERE user_id = %s
          AND active = TRUE
          AND schedule_json IS NOT NULL
          AND (start_date IS NULL OR start_date <= %s)
          AND (end_date IS NULL OR end_date >= %s)
        ORDER BY user_medication_id
        """,
        (user_id, local_to, local_from),
    ).fetchall()

    created = 0
    for medication_id, schedule, revision, start, end in rows:
        created += generate_for_medication(
            conn,
            user_medication_id=medication_id,
            user_id=user_id,
            schedule=schedule,
            schedule_revision=revision,
            start_date=start,
            end_date=end,
            local_from=local_from,
            local_to=local_to,
            not_before=not_before,
        )
    return created


def generate_horizon(conn, *, days: int = DEFAULT_HORIZON_DAYS, now: datetime | None = None) -> int:
    if days < 1 or days > 366:
        raise ScheduleValidationError("생성 범위는 1~366일이어야 합니다.")
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        """
        SELECT DISTINCT user_id
        FROM user_medication
        WHERE active = TRUE AND schedule_json IS NOT NULL
        ORDER BY user_id
        """
    ).fetchall()
    total = 0
    for (user_id,) in rows:
        # 사용자 등록별 timezone이 다를 수 있어 넉넉한 UTC 날짜 구간을 넘기고
        # 실제 생성은 각 schedule timezone에서 처리한다.
        total += ensure_user_occurrences(
            conn,
            user_id=str(user_id),
            local_from=now.date() - timedelta(days=1),
            local_to=now.date() + timedelta(days=days + 1),
            timezone_name=DEFAULT_TIMEZONE,
            not_before=now,
        )
    return total
