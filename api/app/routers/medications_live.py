"""CarePong 2차 복약 일정·회차·복용기록·리포트 API."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from ..services import db
from ..services.medication_schedule import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_TIMEZONE,
    ScheduleValidationError,
    ensure_user_occurrences,
    generate_for_medication,
    normalize_schedule,
    timezone_for,
    utc_bounds,
)
from ..services.raw_auth import require_raw_user


router = APIRouter(tags=["복약 관리 (2차)"])


def fail(status_code: int, code: str, message: str):
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message})


def require_db():
    if not db.available():
        fail(503, "DATABASE_UNAVAILABLE", "Database unavailable")


class MedicationScheduleIn(BaseModel):
    times: list[str] = Field(min_length=1, max_length=24)
    repeat_days: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    timezone: str = Field(default=DEFAULT_TIMEZONE, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_schedule(self):
        try:
            normalized = normalize_schedule(self.model_dump())
        except ScheduleValidationError as exc:
            raise ValueError(str(exc)) from exc
        self.times = normalized["times"]
        self.repeat_days = normalized["repeat_days"]
        self.timezone = normalized["timezone"]
        return self


class MedicationRegisterIn(BaseModel):
    item_type: Literal["DRUG", "HFF"]
    product_id: int = Field(gt=0)
    client_medication_id: str | None = Field(default=None, min_length=1, max_length=100)
    schedule: MedicationScheduleIn | None = None
    # 기존 Electron 계약 호환용. 신규 클라이언트는 schedule 객체를 사용한다.
    times: list[str] | None = Field(default=None, max_length=24)
    repeat_days: list[int] | None = Field(default=None, max_length=7)
    timezone: str | None = Field(default=None, max_length=64)
    start_date: date | None = None
    end_date: date | None = None
    dose_text: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_input(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date는 start_date보다 빠를 수 없습니다.")
        if self.schedule is not None and any(
            value is not None for value in (self.times, self.repeat_days, self.timezone)
        ):
            raise ValueError("schedule과 기존 times/repeat_days/timezone을 함께 보낼 수 없습니다.")
        if self.schedule is None:
            if not self.times:
                raise ValueError("schedule.times는 한 개 이상 필요합니다.")
            self.schedule = MedicationScheduleIn(
                times=self.times,
                repeat_days=self.repeat_days if self.repeat_days is not None else list(range(7)),
                timezone=self.timezone or DEFAULT_TIMEZONE,
            )
        return self


class MedicationPatchIn(BaseModel):
    schedule_revision: int = Field(gt=0, description="조회한 현재 일정 버전")
    schedule: MedicationScheduleIn | None = None
    start_date: date | None = None
    end_date: date | None = None
    dose_text: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_dates(self):
        if (
            "start_date" in self.model_fields_set
            and "end_date" in self.model_fields_set
            and self.start_date
            and self.end_date
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date는 start_date보다 빠를 수 없습니다.")
        return self


class IntakeIn(BaseModel):
    occurrence_id: int = Field(gt=0)
    event_type: Literal["TAKEN", "SNOOZED", "MISSED"]
    client_event_id: str = Field(min_length=1, max_length=100)
    occurred_at: datetime
    scheduled_at: datetime | None = None

    @model_validator(mode="after")
    def require_timezone(self):
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at에는 timezone이 필요합니다.")
        if self.scheduled_at is not None and self.scheduled_at.tzinfo is None:
            raise ValueError("scheduled_at에는 timezone이 필요합니다.")
        return self


class IntakeBatchIn(BaseModel):
    events: list[IntakeIn] = Field(min_length=1, max_length=500)


LIST_SQL = """
SELECT
    um.user_medication_id,
    um.item_type,
    um.med_product_id,
    um.hff_product_id,
    COALESCE(mp.product_name, hp.product_name),
    um.dose_text,
    um.schedule_json,
    um.start_date,
    um.end_date,
    um.active,
    um.client_medication_id,
    um.schedule_revision,
    um.created_at,
    um.updated_at
FROM user_medication um
LEFT JOIN med_product mp ON mp.med_product_id = um.med_product_id
LEFT JOIN hff_product hp ON hp.hff_product_id = um.hff_product_id
WHERE um.user_id = %s
  AND (%s::boolean IS NULL OR um.active = %s::boolean)
ORDER BY um.created_at DESC, um.user_medication_id DESC
"""

ONE_SQL = LIST_SQL.replace(
    "WHERE um.user_id = %s\n  AND (%s::boolean IS NULL OR um.active = %s::boolean)",
    "WHERE um.user_id = %s AND um.user_medication_id = %s",
)


def serialize_medication(row):
    return {
        "user_medication_id": row[0],
        "item_type": row[1],
        "med_product_id": row[2],
        "hff_product_id": row[3],
        "product_name": row[4],
        "dose_text": row[5],
        "schedule": row[6],
        "start_date": row[7],
        "end_date": row[8],
        "active": row[9],
        "client_medication_id": row[10],
        "schedule_revision": row[11],
        "created_at": row[12],
        "updated_at": row[13],
    }


def effective_client_id(body_id: str | None, header_id: str | None) -> str:
    if body_id and header_id and body_id != header_id:
        fail(409, "IDEMPOTENCY_KEY_MISMATCH", "Idempotency-Key와 client_medication_id가 다릅니다.")
    value = header_id or body_id
    if not value:
        fail(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key 또는 client_medication_id가 필요합니다.")
    if len(value) > 100:
        fail(400, "INVALID_IDEMPOTENCY_KEY", "멱등키는 최대 100자입니다.")
    return value


def product_settings(item_type: str):
    if item_type == "DRUG":
        return "med_product_id", "med_product"
    return "hff_product_id", "hff_product"


@router.get("/users/me/medications")
def list_user_medications(
    active: Annotated[bool | None, Query()] = True,
    user_id: str = Depends(require_raw_user),
):
    require_db()
    with db.conn() as conn:
        rows = conn.execute(LIST_SQL, (user_id, active, active)).fetchall()
    return {"items": [serialize_medication(row) for row in rows]}


@router.post("/users/me/medications", status_code=201)
def register_user_medication(
    body: MedicationRegisterIn,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    user_id: str = Depends(require_raw_user),
):
    require_db()
    client_id = effective_client_id(body.client_medication_id, idempotency_key)
    product_column, product_table = product_settings(body.item_type)
    schedule = body.schedule.model_dump()
    now = datetime.now(timezone.utc)

    with db.conn() as conn:
        owner = conn.execute(
            "SELECT user_id FROM users WHERE user_id = %s FOR UPDATE", (user_id,)
        ).fetchone()
        if owner is None:
            fail(401, "UNAUTHORIZED", "Invalid user")

        existing = conn.execute(
            """
            SELECT user_medication_id, item_type, med_product_id, hff_product_id,
                   schedule_json, start_date, end_date, dose_text
            FROM user_medication
            WHERE user_id = %s AND client_medication_id = %s
            FOR UPDATE
            """,
            (user_id, client_id),
        ).fetchone()
        if existing:
            old_product = existing[2] if existing[1] == "DRUG" else existing[3]
            if existing[1] != body.item_type or old_product != body.product_id:
                fail(409, "IDEMPOTENCY_PAYLOAD_CONFLICT", "멱등키가 다른 제품에 이미 사용되었습니다.")
            if (
                existing[4] != schedule
                or existing[5] != body.start_date
                or existing[6] != body.end_date
                or existing[7] != body.dose_text
            ):
                fail(409, "IDEMPOTENCY_PAYLOAD_CONFLICT", "같은 멱등키의 등록 요청 내용이 다릅니다.")
            saved = conn.execute(ONE_SQL, (user_id, existing[0])).fetchone()
            return {"medication": serialize_medication(saved), "created": False, "synchronized": True}

        if conn.execute(
            f"SELECT 1 FROM {product_table} WHERE {product_column} = %s", (body.product_id,)
        ).fetchone() is None:
            fail(404, "CATALOG_PRODUCT_NOT_FOUND", "Catalog product not found")

        row = conn.execute(
            f"""
            INSERT INTO user_medication (
                user_id, item_type, {product_column}, start_date, end_date,
                dose_text, schedule_json, client_medication_id, active
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, TRUE)
            RETURNING user_medication_id, schedule_revision
            """,
            (
                user_id,
                body.item_type,
                body.product_id,
                body.start_date,
                body.end_date,
                body.dose_text,
                json.dumps(schedule),
                client_id,
            ),
        ).fetchone()

        tz = timezone_for(schedule["timezone"])
        local_today = now.astimezone(tz).date()
        generated = generate_for_medication(
            conn,
            user_medication_id=row[0],
            user_id=user_id,
            schedule=schedule,
            schedule_revision=row[1],
            start_date=body.start_date,
            end_date=body.end_date,
            local_from=local_today,
            local_to=local_today + timedelta(days=DEFAULT_HORIZON_DAYS),
            not_before=now,
        )
        saved = conn.execute(ONE_SQL, (user_id, row[0])).fetchone()

    return {
        "medication": serialize_medication(saved),
        "created": True,
        "synchronized": True,
        "generated_occurrences": generated,
    }


@router.get("/users/me/medications/today")
def today_medications(
    local_date: date | None = Query(default=None, alias="date"),
    timezone_name: str = Query(default=DEFAULT_TIMEZONE, alias="timezone"),
    user_id: str = Depends(require_raw_user),
):
    require_db()
    try:
        tz = timezone_for(timezone_name)
        selected = local_date or datetime.now(timezone.utc).astimezone(tz).date()
        start_utc, end_utc = utc_bounds(selected, selected, timezone_name)
    except ScheduleValidationError as exc:
        fail(400, "INVALID_TIMEZONE", str(exc))

    with db.conn() as conn:
        ensure_user_occurrences(
            conn,
            user_id=user_id,
            local_from=selected,
            local_to=selected,
            timezone_name=timezone_name,
        )
        rows = conn.execute(
            """
            SELECT
                o.occurrence_id, o.user_medication_id, o.scheduled_at,
                o.status, o.status_updated_at, o.decision_reason,
                um.item_type, COALESCE(mp.product_name, hp.product_name),
                um.dose_text, um.schedule_revision,
                EXISTS (
                    SELECT 1 FROM medication_intake_event e
                    WHERE e.occurrence_id=o.occurrence_id AND e.event_type='SNOOZED'
                ) AS snoozed
            FROM medication_occurrence o
            JOIN user_medication um ON um.user_medication_id=o.user_medication_id
            LEFT JOIN med_product mp ON mp.med_product_id=um.med_product_id
            LEFT JOIN hff_product hp ON hp.hff_product_id=um.hff_product_id
            WHERE o.user_id=%s AND o.scheduled_at >= %s AND o.scheduled_at < %s
            ORDER BY o.scheduled_at, o.occurrence_id
            """,
            (user_id, start_utc, end_utc),
        ).fetchall()

    items = [
        {
            "occurrence_id": row[0],
            "user_medication_id": row[1],
            "scheduled_at": row[2],
            "status": row[3],
            "status_updated_at": row[4],
            "decision_reason": row[5],
            "item_type": row[6],
            "product_name": row[7],
            "dose_text": row[8],
            "schedule_revision": row[9],
            "snoozed": row[10],
        }
        for row in rows
    ]
    return {
        "date": selected,
        "timezone": timezone_name,
        "items": items,
        "summary": {
            "scheduled": len(items),
            "taken": sum(item["status"] == "TAKEN" for item in items),
            "missed": sum(item["status"] == "MISSED" for item in items),
            "unknown": sum(item["status"] == "UNKNOWN" for item in items),
            "pending": sum(item["status"] == "SCHEDULED" for item in items),
        },
    }


@router.get("/users/me/medication-adherence")
def medication_adherence(
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    timezone_name: str = Query(default=DEFAULT_TIMEZONE, alias="timezone"),
    user_id: str = Depends(require_raw_user),
):
    require_db()
    if date_to < date_from:
        fail(400, "INVALID_DATE_RANGE", "to는 from보다 빠를 수 없습니다.")
    if (date_to - date_from).days > 366:
        fail(400, "DATE_RANGE_TOO_LARGE", "조회 범위는 최대 366일입니다.")
    try:
        tz = timezone_for(timezone_name)
        start_utc, end_utc = utc_bounds(date_from, date_to, timezone_name)
    except ScheduleValidationError as exc:
        fail(400, "INVALID_TIMEZONE", str(exc))

    today = datetime.now(timezone.utc).astimezone(tz).date()
    with db.conn() as conn:
        if date_to >= today:
            ensure_user_occurrences(
                conn,
                user_id=user_id,
                local_from=max(date_from, today),
                local_to=date_to,
                timezone_name=timezone_name,
            )
        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS scheduled,
                COUNT(*) FILTER (WHERE status='TAKEN') AS taken,
                COUNT(*) FILTER (WHERE status='MISSED') AS missed,
                COUNT(*) FILTER (WHERE status='UNKNOWN') AS unknown,
                COUNT(*) FILTER (WHERE status='SCHEDULED') AS pending
            FROM medication_occurrence
            WHERE user_id=%s AND scheduled_at >= %s AND scheduled_at < %s
            """,
            (user_id, start_utc, end_utc),
        ).fetchone()
        snoozed = conn.execute(
            """
            SELECT COUNT(*)
            FROM medication_intake_event
            WHERE user_id=%s AND event_type='SNOOZED'
              AND scheduled_at >= %s AND scheduled_at < %s
            """,
            (user_id, start_utc, end_utc),
        ).fetchone()[0]
        daily_rows = conn.execute(
            """
            SELECT
                (scheduled_at AT TIME ZONE %s)::date AS local_date,
                COUNT(*),
                COUNT(*) FILTER (WHERE status='TAKEN'),
                COUNT(*) FILTER (WHERE status='MISSED'),
                COUNT(*) FILTER (WHERE status='UNKNOWN'),
                COUNT(*) FILTER (WHERE status='SCHEDULED')
            FROM medication_occurrence
            WHERE user_id=%s AND scheduled_at >= %s AND scheduled_at < %s
            GROUP BY local_date
            ORDER BY local_date
            """,
            (timezone_name, user_id, start_utc, end_utc),
        ).fetchall()

    scheduled, taken, missed, unknown, pending = map(int, totals)
    denominator = taken + missed
    return {
        "from": date_from,
        "to": date_to,
        "timezone": timezone_name,
        "totals": {
            "scheduled": scheduled,
            "taken": taken,
            "missed": missed,
            "unknown": unknown,
            "pending": pending,
            "snoozed_actions": int(snoozed),
            "adherence_denominator": denominator,
            "adherence_rate": round(taken / denominator, 4) if denominator else None,
        },
        "adherence_policy": "TAKEN / (TAKEN + MISSED); UNKNOWN과 SCHEDULED는 분모에서 제외",
        "days": [
            {
                "date": row[0],
                "scheduled": row[1],
                "taken": row[2],
                "missed": row[3],
                "unknown": row[4],
                "pending": row[5],
            }
            for row in daily_rows
        ],
    }


@router.patch("/users/me/medications/{medication_id}")
def update_user_medication(
    medication_id: int,
    body: MedicationPatchIn,
    user_id: str = Depends(require_raw_user),
):
    require_db()
    with db.conn() as conn:
        current = conn.execute(
            """
            SELECT schedule_json, start_date, end_date, dose_text, schedule_revision, active
            FROM user_medication
            WHERE user_medication_id=%s AND user_id=%s
            FOR UPDATE
            """,
            (medication_id, user_id),
        ).fetchone()
        if current is None:
            fail(404, "MEDICATION_NOT_FOUND", "등록된 복약 항목을 찾을 수 없습니다.")
        if current[4] != body.schedule_revision:
            fail(409, "SCHEDULE_REVISION_CONFLICT", "일정이 다른 기기에서 변경되었습니다. 목록을 다시 조회하세요.")
        if not current[5]:
            fail(409, "MEDICATION_INACTIVE", "삭제된 복약 항목은 수정할 수 없습니다.")

        schedule = body.schedule.model_dump() if body.schedule is not None else current[0]
        start = body.start_date if "start_date" in body.model_fields_set else current[1]
        end = body.end_date if "end_date" in body.model_fields_set else current[2]
        dose = body.dose_text if "dose_text" in body.model_fields_set else current[3]
        if start and end and end < start:
            fail(400, "INVALID_DATE_RANGE", "end_date는 start_date보다 빠를 수 없습니다.")

        schedule_changed = body.schedule is not None or "start_date" in body.model_fields_set or "end_date" in body.model_fields_set
        revision = current[4] + 1 if schedule_changed else current[4]
        conn.execute(
            """
            UPDATE user_medication
            SET schedule_json=%s::jsonb, start_date=%s, end_date=%s,
                dose_text=%s, schedule_revision=%s, updated_at=now()
            WHERE user_medication_id=%s AND user_id=%s
            """,
            (json.dumps(schedule), start, end, dose, revision, medication_id, user_id),
        )

        generated = 0
        if schedule_changed:
            now = datetime.now(timezone.utc)
            conn.execute(
                """
                DELETE FROM medication_occurrence
                WHERE user_medication_id=%s AND user_id=%s
                  AND scheduled_at >= %s AND status='SCHEDULED'
                """,
                (medication_id, user_id, now),
            )
            tz = timezone_for(schedule["timezone"])
            local_today = now.astimezone(tz).date()
            generated = generate_for_medication(
                conn,
                user_medication_id=medication_id,
                user_id=user_id,
                schedule=schedule,
                schedule_revision=revision,
                start_date=start,
                end_date=end,
                local_from=local_today,
                local_to=local_today + timedelta(days=DEFAULT_HORIZON_DAYS),
                not_before=now,
            )

        saved = conn.execute(ONE_SQL, (user_id, medication_id)).fetchone()
    return {"medication": serialize_medication(saved), "generated_occurrences": generated}


@router.delete("/users/me/medications/{medication_id}")
def delete_user_medication(
    medication_id: int,
    user_id: str = Depends(require_raw_user),
):
    require_db()
    now = datetime.now(timezone.utc)
    with db.conn() as conn:
        row = conn.execute(
            """
            SELECT active, schedule_revision, schedule_json
            FROM user_medication
            WHERE user_medication_id=%s AND user_id=%s
            FOR UPDATE
            """,
            (medication_id, user_id),
        ).fetchone()
        if row is None:
            fail(404, "MEDICATION_NOT_FOUND", "등록된 복약 항목을 찾을 수 없습니다.")
        revision = row[1]
        if row[0]:
            revision += 1
            schedule_timezone = normalize_schedule(row[2])["timezone"]
            conn.execute(
                """
                UPDATE user_medication
                SET active=FALSE, end_date=COALESCE(end_date, %s),
                    schedule_revision=%s, updated_at=now()
                WHERE user_medication_id=%s AND user_id=%s
                """,
                (now.astimezone(timezone_for(schedule_timezone)).date(), revision, medication_id, user_id),
            )
            conn.execute(
                """
                DELETE FROM medication_occurrence
                WHERE user_medication_id=%s AND user_id=%s
                  AND scheduled_at >= %s AND status='SCHEDULED'
                """,
                (medication_id, user_id, now),
            )
    return {"user_medication_id": medication_id, "active": False, "schedule_revision": revision}


def record_intake(conn, *, medication_id: int, body: IntakeIn, user_id: str):
    duplicate = conn.execute(
        """
        SELECT occurrence_id, event_type
        FROM medication_intake_event
        WHERE user_id=%s AND client_event_id=%s
        """,
        (user_id, body.client_event_id),
    ).fetchone()
    if duplicate:
        if duplicate[0] != body.occurrence_id or duplicate[1] != body.event_type:
            fail(409, "CLIENT_EVENT_CONFLICT", "client_event_id가 다른 복용 기록에 이미 사용되었습니다.")
        status = conn.execute(
            "SELECT status FROM medication_occurrence WHERE occurrence_id=%s", (body.occurrence_id,)
        ).fetchone()
        return {"client_event_id": body.client_event_id, "duplicate": True, "status": status[0] if status else None}

    occurrence = conn.execute(
        """
        SELECT scheduled_at, status
        FROM medication_occurrence
        WHERE occurrence_id=%s AND user_medication_id=%s AND user_id=%s
        FOR UPDATE
        """,
        (body.occurrence_id, medication_id, user_id),
    ).fetchone()
    if occurrence is None:
        fail(404, "OCCURRENCE_NOT_FOUND", "사용자의 해당 복약 회차를 찾을 수 없습니다.")
    if body.scheduled_at and body.scheduled_at.astimezone(timezone.utc) != occurrence[0].astimezone(timezone.utc):
        fail(409, "SCHEDULED_AT_MISMATCH", "scheduled_at이 회차와 일치하지 않습니다.")
    if body.event_type == "TAKEN" and occurrence[1] == "TAKEN":
        fail(409, "OCCURRENCE_ALREADY_TAKEN", "이미 복용 완료된 회차입니다.")
    if body.event_type == "MISSED" and occurrence[1] == "TAKEN":
        fail(409, "TAKEN_CANNOT_BECOME_MISSED", "복용 완료 회차를 미복용으로 변경할 수 없습니다.")

    conn.execute(
        """
        INSERT INTO medication_intake_event (
            occurrence_id, user_medication_id, user_id, scheduled_at,
            event_type, client_event_id, occurred_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            body.occurrence_id,
            medication_id,
            user_id,
            occurrence[0],
            body.event_type,
            body.client_event_id,
            body.occurred_at,
        ),
    )
    if body.event_type in ("TAKEN", "MISSED"):
        reason = "USER_REPORTED_TAKEN" if body.event_type == "TAKEN" else "USER_REPORTED_MISSED"
        conn.execute(
            """
            UPDATE medication_occurrence
            SET status=%s, status_updated_at=now(), decision_reason=%s
            WHERE occurrence_id=%s
            """,
            (body.event_type, reason, body.occurrence_id),
        )
        final_status = body.event_type
    else:
        final_status = occurrence[1]
    return {"client_event_id": body.client_event_id, "duplicate": False, "status": final_status}


@router.post("/users/me/medications/{medication_id}/intakes", status_code=201)
def create_intake(
    medication_id: int,
    body: IntakeIn,
    user_id: str = Depends(require_raw_user),
):
    require_db()
    with db.conn() as conn:
        result = record_intake(conn, medication_id=medication_id, body=body, user_id=user_id)
    return result


@router.post("/users/me/medication-intakes/batch")
def synchronize_intakes(
    body: IntakeBatchIn,
    user_id: str = Depends(require_raw_user),
):
    require_db()
    results = []
    with db.conn() as conn:
        medication_ids = dict(
            conn.execute(
                """
                SELECT occurrence_id, user_medication_id
                FROM medication_occurrence
                WHERE user_id=%s AND occurrence_id = ANY(%s)
                """,
                (user_id, [event.occurrence_id for event in body.events]),
            ).fetchall()
        )
        for event in body.events:
            medication_id = medication_ids.get(event.occurrence_id)
            if medication_id is None:
                results.append({"client_event_id": event.client_event_id, "accepted": False, "code": "OCCURRENCE_NOT_FOUND"})
                continue
            try:
                saved = record_intake(conn, medication_id=medication_id, body=event, user_id=user_id)
                results.append({"accepted": True, **saved})
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {"code": "INVALID_EVENT"}
                results.append({"client_event_id": event.client_event_id, "accepted": False, "code": detail.get("code")})
    return {
        "results": results,
        "accepted_ids": [row["client_event_id"] for row in results if row["accepted"]],
        "rejected_ids": [row["client_event_id"] for row in results if not row["accepted"]],
    }
