"""라벨 — 팝업 예/아니오 + 정기 체크인. 모델 vs 사용자 비교(H1-3)의 정답지.
형식(YES/NO만인지, 1~5점도인지)과 매칭 규칙은 DA 답(Q20) 후 확정. 지금은 둘 다 받게 열어둠."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["감지 (P03)"])

class SelfReportIn(BaseModel):
    window_id: str | None = Field(None, description="팝업을 띄운 창. 정기 체크인은 null (서버가 직전 창과 매칭)")
    trigger: Literal["prompt", "scheduled", "random"] = Field(..., description="prompt=지수 초과 팝업 / scheduled=1h·4h·8h 정기 / random")
    question_version: str = Field("q1", description="문구 버전. 문구 바뀌면 올림")
    answer_yes_no: bool | None = Field(None, description="예=true / 아니오=false / 무응답=null (45초 타임아웃)")
    answer_scale: int | None = Field(None, ge=1, le=5, description="1~5점 (쓸지 DA 결정)")
    answered_at: datetime
    client_event_id: str

class SelfReportOut(BaseModel):
    report_id: str = Field(..., examples=["rep_01"])
    accepted: bool = True

@router.post("/self-reports", response_model=SelfReportOut, status_code=201, summary="[Stub] 과부하 확인 응답·정기 체크인 기록")
def post_report(body: SelfReportIn):
    return SelfReportOut(report_id="rep_mock")
