"""M07 회원번호 — 앱이 발급한 CP-XXXXXXXX를 서버에 등록하고 토큰을 받는다.
인증 방식(D07)은 미정. 일단 토큰 발급 형태로 껍데기만."""
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["회원번호 (M07)"])

class EnrollIn(BaseModel):
    cp_number: str = Field(..., examples=["CP-3F9A2B7C"], description="앱이 최초 실행 시 발급한 익명 번호")
    device_id: str = Field(..., examples=["win-7d1c…"], description="앱이 만든 기기 식별자")
    app_version: str = Field(..., examples=["0.1.0"])
    os: str = Field("windows", examples=["windows"])

class EnrollOut(BaseModel):
    user_id: str = Field(..., examples=["usr_01HZX…"])
    access_token: str = Field(..., description="이후 모든 요청의 Authorization: Bearer 에 넣는다")

@router.post("/enroll", response_model=EnrollOut, summary="[Stub] 회원번호 등록 → 토큰 발급")
def enroll(body: EnrollIn):
    return EnrollOut(user_id="usr_mock_0001", access_token="mock-token")
