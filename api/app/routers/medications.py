"""약지도 M02~M04, 설정 M07, 알람 P02.
약 = 이름 + 시간 여러 개 + 매일 반복(기본). LUI/폼 어느 쪽으로 등록해도 같은 API."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["약지도 (M02~M04, M07, P02)"])

class MedicationIn(BaseModel):
    name: str = Field(..., examples=["오메가3"])
    times: list[str] = Field(..., examples=[["09:00", "19:00"]], description="HH:MM. LUI 어휘(아침 등)는 앱에서 변환해 보냄")
    memo: str | None = Field(None, examples=["식후"])
    source: Literal["lui", "form"] = Field("form", description="등록 경로 — LUI vs GUI 비교 테스트용")

class Medication(MedicationIn):
    medication_id: str = Field(..., examples=["med_01"])
    created_at: datetime

class LogAccepted(BaseModel):
    log_id: str = Field(..., examples=["mlog_01"])
    accepted: bool = True

class MedicationLogIn(BaseModel):
    scheduled_at: datetime = Field(..., description="원래 먹기로 한 시각")
    status: Literal["taken", "snoozed", "timeout", "skipped"] = Field(
        ..., description="taken=먹었어요 / snoozed=나중에 / timeout=45초 무응답 / skipped=건너뜀. 무응답≠미복용")
    occurred_at: datetime = Field(..., description="버튼 누른 시각(앱 시계)")
    client_event_id: str = Field(..., description="앱이 만든 고유 ID. 재전송돼도 한 번만 기록")

MOCK = [Medication(medication_id="med_01", name="오메가3", times=["19:00"], memo=None, source="lui", created_at=datetime(2026,10,1,9,0))]

@router.get("/medications", response_model=list[Medication], summary="[Stub] 등록 약 목록 (오늘의 복약 카드·설정 관리 목록)")
def list_medications():
    return MOCK

@router.post("/medications", response_model=Medication, status_code=201, summary="[Stub] 약 등록 (LUI·폼 공통)")
def create_medication(body: MedicationIn):
    return Medication(medication_id="med_new", created_at=datetime.now(), **body.model_dump())

@router.delete("/medications/{medication_id}", status_code=204, summary="[Stub] 약 삭제 (M07)")
def delete_medication(medication_id: str):
    return None

@router.post("/medications/{medication_id}/logs", status_code=201, response_model=LogAccepted, summary="[Stub] 약 알람 응답 기록 (P02 먹었어요/나중에/무응답)")
def log_medication(medication_id: str, body: MedicationLogIn):
    return LogAccepted(log_id="mlog_mock")
