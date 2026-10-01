"""게임 요소 — 틀만. 원본은 앱 로컬. 기획서상 서버 전송 없음이지만
리텐션 분석·여러 기기 복원에 필요할 수 있어 백업 형태의 틀을 둔다. 쓸지는 D01에서 결정."""
from datetime import date, datetime
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["게임 (틀만 · 로컬 원본)"])

class DailyProgress(BaseModel):
    day: date
    hearts: int = Field(0, ge=0, le=10, description="오늘의 하트 10칸")
    bonus_claimed: bool = False
    chat_count: int = 0

class FurnitureState(BaseModel):
    furniture_id: str = Field(..., examples=["UN-01"])
    unlocked_at: datetime | None = None
    placed: bool = False

class LifetimeStats(BaseModel):
    generations: int = 1
    total_care: int = 0
    total_alarms_done: int = 0
    total_relief_done: int = 0
    total_chats: int = 0

class PetArchiveItem(BaseModel):
    generation: int
    name: str
    species: Literal["cat", "quokka", "dog"]
    retired_at: datetime

@router.put("/game/daily", response_model=DailyProgress, summary="오늘의 하트 백업")
def put_daily(body: DailyProgress):
    return body

@router.put("/game/furniture", response_model=list[FurnitureState], summary="가구 해금·배치 백업")
def put_furniture(body: list[FurnitureState]):
    return body

@router.get("/game/lifetime", response_model=LifetimeStats, summary="평생 기록 조회 (환생해도 유지)")
def get_lifetime():
    return LifetimeStats()

@router.get("/game/archive", response_model=list[PetArchiveItem], summary="역대 펫 목록 (M06)")
def get_archive():
    return []
