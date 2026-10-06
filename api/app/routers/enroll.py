"""신규 익명 사용자 등록 및 Bearer 토큰 발급."""
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..services import db
from ..services.auth import AuthUser, require_user, token_hash

router = APIRouter(tags=["회원번호 (M07)"])


class EnrollIn(BaseModel):
    cp_number: str = Field(..., pattern=r"^CP-[0-9A-Fa-f]{8}$")
    device_id: str = Field(..., min_length=1, max_length=64)
    app_version: str = Field(..., min_length=1, max_length=16)
    os: str = Field("windows", min_length=1, max_length=16)


class EnrollOut(BaseModel):
    user_id: str
    access_token: str
    token_type: str = "bearer"


class EnrollStatus(BaseModel):
    user_id: str
    cp_number: str


@router.post("/enroll", response_model=EnrollOut, status_code=201,
             summary="신규 회원번호 등록 및 토큰 발급")
def enroll(body: EnrollIn):
    if not db.available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    token = secrets.token_urlsafe(32)

    try:
        with db.conn() as c:
            row = c.execute(
                """INSERT INTO users
                   (cp_number, device_id, app_version, os, token_hash, last_seen_at)
                   VALUES (%s, %s, %s, %s, %s, now())
                   ON CONFLICT (cp_number) DO NOTHING
                   RETURNING user_id""",
                (
                    body.cp_number.upper(),
                    body.device_id,
                    body.app_version,
                    body.os,
                    token_hash(token),
                ),
            ).fetchone()
    except Exception:
        raise HTTPException(status_code=503, detail="Registration unavailable")

    if row is None:
        raise HTTPException(
            status_code=409,
            detail="Already registered; use existing token or recovery process",
        )

    return EnrollOut(user_id=str(row[0]), access_token=token)


@router.get("/enroll", response_model=EnrollStatus,
            summary="현재 토큰 검증 및 회원정보 확인")
def enrollment_status(user: AuthUser = Depends(require_user)):
    return EnrollStatus(user_id=user.user_id, cp_number=user.cp_number)
