"""앱이 켜질 때 받아가는 정책값. 추론은 서버가 하므로 가중치는 여기 없음 (D03).
앱은 이 값으로 팝업 UI·체크인 스케줄만 맞춘다. 판단 자체는 서버 응답(prompt)을 따른다."""
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["모델 설정"])

class AppConfig(BaseModel):
    config_version: str = Field("c1")
    model_version: str = Field("mock-0", description="현재 서버가 쓰는 모델. 표시·로그용")
    window_sec: int = Field(60, description="DA 확정 1분")
    upload_interval_sec: int = Field(60, description="창 업로드 주기")
    cut_observe: int = Field(50)
    cut_intervene: int = Field(63, description="DA 컷. 게임규칙서 70 → 63")
    daily_prompt_limit: int = Field(3)
    cooldown_min: int = Field(30)
    snooze_min: int = Field(10, description="'아니오' 후 재팝업 금지")
    checkin_hours_after_start: list[int] = Field(default_factory=lambda: [1, 4, 8], description="정기 체크인: 앱 켠 뒤 N시간 (Q20)")
    prompt_text: str = Field("지금 과부하인가요?", description="팝업 문구 (Q07·Q08 결정 후 확정)")
    question_version: str = Field("q1")

@router.get("/config", response_model=AppConfig, summary="[Draft] 앱 시작 시 정책값 다운로드 (컷·상한·체크인 시각)")
def get_config():
    return AppConfig()
