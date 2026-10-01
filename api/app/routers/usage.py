"""이용 지표 — 게임·대화·알람의 '숫자'만 서버로. 내용(일기 원문·기분·펫 이름 등)은 로컬.
용도: 성공지표(수집 지속률·개입 실행율·D-14 잔존율) + 리텐션 분석.
[Draft] 항목 목록은 DA·PM과 확정 전. 하루 1건 요약을 앱이 자정 이후 또는 앱 시작 시 전송."""
from datetime import date, datetime
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["이용 지표"])

class DailyUsageIn(BaseModel):
    day: date = Field(..., description="로컬 날짜 기준 하루")
    app_active_min: int = Field(0, ge=0, description="앱 켜져 있던 분")
    collector_active_min: int = Field(0, ge=0, description="수집기 동작 분 — 수집 지속률 분자")
    dnd_min: int = Field(0, ge=0)
    # 펫·게임
    care_actions: int = Field(0, ge=0, description="밥·쓰다듬기·산책 합계")
    walks: int = 0
    chat_messages: int = Field(0, ge=0, description="미니홈 대화 수 (내용 X)")
    mood_checks: int = 0
    hearts_filled: bool = Field(False, description="오늘의 하트 10칸 달성")
    pet_level: int = Field(1, ge=1, le=5)
    pet_generation: int = Field(1, ge=1)
    furniture_unlocked: int = 0
    # 알람
    alarms_shown: int = 0
    alarms_done: int = 0
    alarms_snoozed: int = 0
    alarms_timeout: int = 0
    med_scheduled: int = 0
    med_taken: int = Field(0, description="순응률 = med_taken / med_scheduled")
    # 개입
    prompts_shown: int = Field(0, description="컨디션 확인 프롬프트 노출")
    prompts_yes: int = 0
    prompts_no: int = 0
    relief_started: int = 0
    relief_completed: int = Field(0, description="개입 실행율 = relief_completed / prompts_shown")
    # 미니홈
    minihome_opens: int = 0
    tab_opens: dict[str, int] = Field(default_factory=dict, examples=[{"meds": 3, "mind": 1, "archive": 0, "settings": 1}])
    client_event_id: str
    app_version: str

class DailyAccepted(BaseModel):
    accepted: bool = True
    day: date

@router.put("/usage/daily", status_code=200, response_model=DailyAccepted, summary="[Draft] 일별 이용 지표 업로드 (같은 날은 덮어씀)")
def put_daily(body: DailyUsageIn):
    """⚠️ **[TODO]** 지표 항목·분모 정의는 DA(혜윤)·PM(채원)과 확정 후 고정. 현재는 TA 초안.
    내용 데이터(일기·기분 텍스트·펫 이름)는 포함하지 않는다."""
    return DailyAccepted(day=body.day)
