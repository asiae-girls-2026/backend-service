"""감지 — 1분 창 피처 업로드 + 서버 추론 결과 회신. (D03: 서버 추론 확정)
앱은 1분마다 16피처(+저장용 14개)를 보내고, 서버가 model 컨테이너에 넘겨 지수를 받아 돌려준다.
팝업 여부(prompt)도 서버가 정한다: 지수 ≥ 개입 컷 + 하루 상한 미만 + 쿨다운 지남 + 방해금지 아님."""
from datetime import datetime
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["감지 (P03)"])

class WindowIn(BaseModel):
    window_id: str = Field(..., examples=["win_01HZX…"], description="앱이 만든 창 ID. 라벨·예측을 이걸로 짝지음")
    session_id: str = Field(..., examples=["ses_01"], description="수집 세션(차수)")
    window_start: datetime = Field(..., description="창 시작 시각 (앱 시계)")
    window_sec: int = Field(60, description="DA 확정 1분")
    features: dict[str, float] = Field(..., examples=[{"SnKeyStrokes": 142, "SnChars": 110, "SnErrorKeys": 6, "SnMouseAct": 38, "SnLeftClicked": 21, "SnMouseDistance": 5120.5}],
                                       description="판정용 16 + 저장용 14. 이름은 DA 변수 명세 그대로. 결측은 null")
    dnd: bool = Field(False, description="방해금지 중이면 true — 서버가 팝업을 안 띄움")
    client_event_id: str

class WindowOut(BaseModel):
    window_id: str
    condition_score: int | None = Field(None, ge=0, le=100, description="컨디션 지수. 활동 0 창이면 null")
    level: str = Field("normal", examples=["normal", "observe", "intervene"], description="컷 기준: <50 normal / 50~62 observe / ≥63 intervene")
    prompt: bool = Field(False, description="true면 앱이 '지금 과부하인가요?' 팝업을 띄움. 상한·쿨다운·방해금지 반영")
    model_version: str = Field(..., examples=["v1"], description="어떤 모델이 판단했는지 — 라벨과 함께 저장")

class WindowBatchIn(BaseModel):
    windows: list[WindowIn] = Field(..., description="오프라인이었다가 복구되면 여러 창을 한 번에")

@router.post("/windows", response_model=WindowOut, summary="[Stub] 1분 창 업로드 → 지수·팝업 여부 회신")
def post_window(body: WindowIn):
    # TODO: model 컨테이너 /predict 호출 → 컷·상한·쿨다운 적용
    return WindowOut(window_id=body.window_id, condition_score=71, level="intervene", prompt=True, model_version="mock-0")

@router.post("/windows/batch", response_model=list[WindowOut], summary="[Stub] 창 여러 개 업로드 (복구 시). 팝업은 띄우지 않음")
def post_windows(body: WindowBatchIn):
    return [WindowOut(window_id=w.window_id, condition_score=40, level="normal", prompt=False, model_version="mock-0") for w in body.windows]
