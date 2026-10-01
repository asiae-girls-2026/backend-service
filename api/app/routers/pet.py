"""펫 P01·P06·P07. 원본은 앱 로컬(electron-store). 서버는 백업 + 분석용 행동 이벤트.
보상(XP·게이지) 계산은 앱이 한다. 서버는 기록만."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["펫 (P01, P06, P07)"])

class PetState(BaseModel):
    name: str = Field(..., examples=["퐁이"])
    species: Literal["cat", "quokka", "dog"] = "cat"
    generation: int = Field(1, ge=1, description="N번째 아이")
    level: int = Field(1, ge=1, le=5)
    xp: int = Field(0, ge=0)
    happiness: int = Field(70, ge=0, le=100)
    health: int = Field(70, ge=0, le=100)
    energy: int = Field(70, ge=0, le=100)
    updated_at: datetime

class Accepted(BaseModel):
    accepted: bool = True

class PetActionIn(BaseModel):
    action: Literal["feed", "pet", "walk", "walk_refused", "rebirth", "rename", "level_up"]
    occurred_at: datetime
    client_event_id: str
    detail: dict | None = Field(None, examples=[{"new_level": 2}])

@router.get("/pet", response_model=PetState, summary="[Stub] 펫 상태 (서버 백업본) 조회")
def get_pet():
    return PetState(name="퐁이", updated_at=datetime.now())

@router.put("/pet", response_model=PetState, summary="[Stub] 펫 상태 백업 (앱 → 서버)")
def put_pet(body: PetState):
    return body

@router.post("/pet/actions", status_code=201, response_model=Accepted, summary="[Stub] 돌봄·환생·레벨업 행동 기록")
def pet_action(body: PetActionIn):
    return Accepted()
