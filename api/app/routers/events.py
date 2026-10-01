"""운영 계측 — 수집 멈춤·에러·앱 시작/종료. 민감 원문 금지(창 제목·키 내용 X)."""
from datetime import datetime
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["운영"])

class AppEvent(BaseModel):
    client_event_id: str
    kind: str = Field(..., examples=["app_start", "collector_stopped", "inference_error", "dnd_on"])
    occurred_at: datetime
    app_version: str
    payload: dict | None = None

class BatchIn(BaseModel):
    events: list[AppEvent] = Field(..., description="한 번에 여러 건. 앱이 모아뒀다 보냄")

class BatchOut(BaseModel):
    accepted_ids: list[str] = Field(..., description="저장된 client_event_id. 앱은 이것만 로컬 큐에서 지움")
    rejected_ids: list[str] = Field(default_factory=list, description="거절된 ID (형식 오류 등)")

@router.post("/events/batch", response_model=BatchOut, summary="[Stub] 앱 이벤트 배치 전송 (중복 ID는 무시)")
def batch(body: BatchIn):
    return BatchOut(accepted_ids=[e.client_event_id for e in body.events])
