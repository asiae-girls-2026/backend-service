"""CarePong 2차: RDS 기반 사용자 등록약 API."""

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from psycopg.types.json import Jsonb

from ..services import db
from ..services.raw_auth import require_raw_user


router = APIRouter(tags=["복약 등록 (2차)"])


class MedicationRegisterIn(BaseModel):
    item_type: Literal["DRUG", "HFF"]
    product_id: int = Field(gt=0)
    client_medication_id: str = Field(min_length=1, max_length=128)
    times: list[str] = Field(default_factory=list, max_length=24)
    repeat_days: list[int] = Field(
        default_factory=lambda: list(range(7))
    )
    dose_text: str | None = Field(default=None, max_length=200)

    @field_validator("times")
    @classmethod
    def check_times(cls, values):
        for value in values:
            if not re.fullmatch(
                r"(?:[01]\d|2[0-3]):[0-5]\d", value
            ):
                raise ValueError("시간은 HH:MM 형식이어야 합니다.")
        if len(values) != len(set(values)):
            raise ValueError("복약 시간이 중복되었습니다.")
        return values

    @field_validator("repeat_days")
    @classmethod
    def check_days(cls, values):
        if any(
            type(day) is not int or day < 0 or day > 6
            for day in values
        ):
            raise ValueError("요일은 0~6이어야 합니다.")
        if len(values) != len(set(values)):
            raise ValueError("요일이 중복되었습니다.")
        return values


def require_db():
    if not db.available():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )


LIST_SQL = """
SELECT
    um.user_medication_id,
    um.item_type,
    um.med_product_id,
    um.hff_product_id,
    COALESCE(mp.product_name, hp.product_name),
    um.dose_text,
    um.schedule_json,
    um.active,
    um.client_medication_id,
    um.schedule_revision
FROM user_medication um
LEFT JOIN med_product mp
    ON mp.med_product_id = um.med_product_id
LEFT JOIN hff_product hp
    ON hp.hff_product_id = um.hff_product_id
WHERE um.user_id = %s
  AND um.active = TRUE
ORDER BY um.created_at DESC
"""


def serialize(row):
    return {
        "user_medication_id": row[0],
        "item_type": row[1],
        "med_product_id": row[2],
        "hff_product_id": row[3],
        "product_name": row[4],
        "dose_text": row[5],
        "schedule": row[6],
        "active": row[7],
        "client_medication_id": row[8],
        "schedule_revision": row[9],
    }


@router.get("/users/me/medications")
def list_user_medications(
    user_id: str = Depends(require_raw_user),
):
    require_db()

    with db.conn() as conn:
        rows = conn.execute(LIST_SQL, (user_id,)).fetchall()

    return {
        "items": [serialize(row) for row in rows]
    }


@router.post("/users/me/medications", status_code=201)
def register_user_medication(
    body: MedicationRegisterIn,
    user_id: str = Depends(require_raw_user),
):
    require_db()

    product_column = (
        "med_product_id"
        if body.item_type == "DRUG"
        else "hff_product_id"
    )
    product_table = (
        "med_product"
        if body.item_type == "DRUG"
        else "hff_product"
    )

    schedule = {
        "times": body.times,
        "repeat_days": body.repeat_days,
    }

    with db.conn() as conn:
        # 같은 사용자의 동시 등록을 직렬화한다.
        owner = conn.execute(
            "SELECT user_id FROM users WHERE user_id = %s FOR UPDATE",
            (user_id,)
        ).fetchone()

        if owner is None:
            raise HTTPException(
                status_code=401, detail="Invalid user"
            )

        # 클라이언트 등록 ID가 같으면 중복 INSERT하지 않는다.
        existing = conn.execute(
            """
            SELECT
                user_medication_id, item_type,
                med_product_id, hff_product_id
            FROM user_medication
            WHERE user_id = %s
              AND client_medication_id = %s
            ORDER BY user_medication_id DESC
            LIMIT 1
            """,
            (user_id, body.client_medication_id),
        ).fetchone()

        if existing:
            old_id, old_type, old_med, old_hff = existing
            old_product = (
                old_med if old_type == "DRUG" else old_hff
            )

            if (
                old_type != body.item_type
                or old_product != body.product_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Client medication ID already used"
                )

            row = conn.execute(
                """
                UPDATE user_medication
                SET dose_text = %s,
                    schedule_json = %s,
                    active = TRUE,
                    schedule_revision = schedule_revision + 1,
                    updated_at = now()
                WHERE user_medication_id = %s
                  AND user_id = %s
                RETURNING user_medication_id, schedule_revision
                """,
                (
                    body.dose_text,
                    Jsonb(schedule),
                    old_id,
                    user_id,
                ),
            ).fetchone()

            return {
                "user_medication_id": row[0],
                "schedule_revision": row[1],
                "synchronized": True,
                "created": False,
            }

        # 사용자가 선택한 실제 Catalog 제품인지 확인한다.
        product_exists = conn.execute(
            f"SELECT 1 FROM {product_table} "
            f"WHERE {product_column} = %s",
            (body.product_id,)
        ).fetchone()

        if product_exists is None:
            raise HTTPException(
                status_code=404,
                detail="Catalog product not found"
            )

        row = conn.execute(
            f"""
            INSERT INTO user_medication (
                user_id,
                item_type,
                {product_column},
                dose_text,
                schedule_json,
                client_medication_id,
                active
            )
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            RETURNING user_medication_id, schedule_revision
            """,
            (
                user_id,
                body.item_type,
                body.product_id,
                body.dose_text,
                Jsonb(schedule),
                body.client_medication_id,
            ),
        ).fetchone()

    return {
        "user_medication_id": row[0],
        "schedule_revision": row[1],
        "synchronized": True,
        "created": True,
    }
