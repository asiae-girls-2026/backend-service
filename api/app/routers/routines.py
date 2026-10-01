"""설정 M07 (물·스트레칭·휴식·방해금지) + 알람 P02 이행 기록.
설정의 원본은 앱 로컬. 서버는 백업·분석용 사본."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["루틴·설정 (M07, P02)"])

class RoutineSettings(BaseModel):
    water_goal_cups: int = Field(8, ge=1)
    water_interval_min: int = Field(90, ge=20, description="최소 20분")
    stretch_interval_min: int = Field(60, ge=10, description="끌 수 없음, 최소 10분")
    rest_interval_min: int = Field(75, ge=10, description="끌 수 없음, 최소 10분")
    dnd_schedule_enabled: bool = False
    dnd_start: str = Field("22:00", examples=["22:00"])
    dnd_end: str = Field("08:00", examples=["08:00"])
    pet_motion_minimal: bool = False

class LogAccepted(BaseModel):
    log_id: str = Field(..., examples=["rlog_01"])
    accepted: bool = True

class RoutineLogIn(BaseModel):
    kind: Literal["water", "stretch", "rest"]
    status: Literal["done", "snoozed", "timeout"]
    occurred_at: datetime
    client_event_id: str

@router.get("/routines", response_model=RoutineSettings, summary="[Stub] 루틴 설정 조회")
def get_routines():
    return RoutineSettings()

@router.put("/routines", response_model=RoutineSettings, summary="[Stub] 루틴 설정 저장 (최소값 이하는 서버도 보정)")
def put_routines(body: RoutineSettings):
    return body

@router.post("/routines/logs", status_code=201, response_model=LogAccepted, summary="[Stub] 물·스트레칭·휴식 알람 응답 기록")
def log_routine(body: RoutineLogIn):
    return LogAccepted(log_id="rlog_mock")
