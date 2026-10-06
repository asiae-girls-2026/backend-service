"""M07 회원번호 — 앱이 발급한 CP-XXXXXXXX를 서버에 등록하고 토큰을 받는다.
인증 방식(D07)은 미정. 일단 토큰 발급 형태로 껍데기만."""
from fastapi import APIRouter
from pydantic import BaseModel, Field
from ..services import db

router = APIRouter(tags=["회원번호 (M07)"])

class EnrollIn(BaseModel):
    cp_number: str = Field(..., min_length=3, max_length=12, pattern=r"^CP-[A-Z0-9]{1,9}$", examples=["CP-3F9A2B7C"], description="앱이 최초 실행 시 발급한 익명 번호 CP-XXXXXXXX (대문자·숫자, 최대 12자 — DB users.cp_number varchar(12))")
    device_id: str = Field(..., examples=["win-7d1c…"], description="앱이 만든 기기 식별자")
    app_version: str = Field(..., examples=["0.1.0"])
    os: str = Field("windows", examples=["windows"])

class EnrollOut(BaseModel):
    user_id: str = Field(..., examples=["usr_01HZX…"])
    access_token: str = Field(..., description="이후 모든 요청의 Authorization: Bearer 에 넣는다")

@router.post("/enroll", response_model=EnrollOut, summary="[Stub] 회원번호 등록 → 토큰 발급")
def enroll(body: EnrollIn):
    uid = db.ensure_user(body.cp_number, body.device_id, body.app_version) or "usr_nodb"
    # TODO(정화·D07): 토큰 발급 + users.token_hash 저장. 지금은 cp_number 를 그대로 토큰 자리에 (앱은 X-User-Id 에 cp_number 를 보냄)
    return EnrollOut(user_id=uid, access_token=body.cp_number)
