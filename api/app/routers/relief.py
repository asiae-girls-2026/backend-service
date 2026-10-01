"""완화 P04·P05. 심호흡·명상 완료/중단 기록. 보상은 완료 시에만.
'예/아니요' 확인(P03)과 지수는 ML 영역 → DA 명세 받은 뒤 self-reports로 추가."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["완화 (P04, P05)"])

class SessionAccepted(BaseModel):
    session_id: str = Field(..., examples=["rs_01"])
    accepted: bool = True

class ReliefSessionIn(BaseModel):
    kind: Literal["breathing", "meditation"]
    result: Literal["completed", "aborted", "declined"] = Field(..., description="declined=지금은 괜찮아요")
    started_at: datetime
    ended_at: datetime
    entry: Literal["stress_prompt", "mind_tab"] = Field(..., description="P03에서 왔는지, M05 1분 쉬어가기에서 왔는지")
    client_event_id: str

@router.post("/relief-sessions", status_code=201, response_model=SessionAccepted, summary="[Stub] 완화 세션 기록")
def relief(body: ReliefSessionIn):
    return SessionAccepted(session_id="rs_mock")
