"""케어퐁 메인 API — 화면기획서 v0.5 기준.
http://localhost:8000/docs 에서 Swagger로 확인.

상태 표기 규칙 (summary 접두어)
  [Stub]    1.0 범위. 주소·필드 확정 대상. 응답은 고정 가짜값, 로직 미구현
  [Draft]   설계 미확정. 다른 담당자(DA·PM) 확정 대기
  [Planned] 1.0 미포함. 정식 배포(v1.x)에서 구현
"""
from fastapi import FastAPI
from pydantic import BaseModel
from .routers import enroll, medications, routines, pet, relief, drugs, events, usage, windows, self_reports, config, telegram, ppg

TAGS = [
    {"name": "운영",                 "description": "서버 생존·앱 이벤트. 1.0"},
    {"name": "회원번호 (M07)",       "description": "CP 번호 등록·토큰. 인증 방식(D07)은 확정 전이라 토큰 모양만 고정"},
    {"name": "약지도 (M02~M04, M07, P02)", "description": "약 등록·목록·삭제·알람 응답. 1.0"},
    {"name": "루틴·설정 (M07, P02)", "description": "물·스트레칭·휴식·방해금지 설정과 이행 기록. 1.0"},
    {"name": "펫 (P01, P06, P07)",   "description": "펫 상태 백업·행동 기록. 보상 계산은 앱. 1.0"},
    {"name": "완화 (P04, P05)",      "description": "심호흡·명상 완료/중단. 1.0"},
    {"name": "약 정보 (M04)",        "description": "식약처 e약은요 프록시. API 키는 서버만. 1.0"},
    {"name": "이용 지표",            "description": "게임·대화·알람의 숫자만. 내용은 로컬. [Draft] 항목 확정 전"},
    {"name": "감지 (P03)",           "description": "1분 창 업로드 → 서버 추론 → 지수·팝업 여부 회신. 라벨 수집. 추론은 model 컨테이너 (D03 서버 추론)"},
    {"name": "모델 설정",            "description": "앱 정책값(컷·상한·체크인). 가중치는 서버에만. [Draft] 문구·체크인 시각은 결정 대기"},
    {"name": "텔레그램 봇",          "description": "연결 코드·webhook. [Draft] 1.0 포함 여부 미정"},
    {"name": "PPG 생체 층",          "description": "[Planned] 정식 배포에서 구현. DA 데모 흐름(QR→폰 측정→HRV) 기준"},
]

app = FastAPI(
    title="CarePong API",
    version="0.1.0",
    description="케어퐁 서버 API 명세. 모든 엔드포인트는 현재 목 응답. summary 접두어 [Stub]/[Draft]/[Planned]로 상태 표시.",
    openapi_tags=TAGS,
)

class Health(BaseModel):
    ok: bool = True
    service: str = "api"
    version: str = "0.1.0"

@app.get("/health", tags=["운영"], summary="[Stub] 서버 생존 확인", response_model=Health)
def health():
    return Health(version=app.version)

for r in (enroll, medications, routines, pet, relief, drugs, events, usage, windows, self_reports, config, telegram, ppg):
    app.include_router(r.router, prefix="/v1")
