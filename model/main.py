"""모델 서빙 컨테이너 — 실제 추론 경로 (D03 서버 추론).
혜윤한테 받을 것: predict(features: dict) -> float(0~1) 또는 score(0~100) 함수 + 모델 파일 + 샘플 입출력 5개.
받으면 아래 TODO만 교체. api 컨테이너가 /predict를 호출한다. 앱은 이 컨테이너를 직접 부르지 않는다."""
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="CarePong Model API", version="0.0.1")
MODEL_VERSION = "mock-0"

class PredictIn(BaseModel):
    features: dict[str, float | None] = Field(..., description="16피처(+14). 결측 null → 중앙값 대체는 여기서")

class PredictOut(BaseModel):
    score: int | None = Field(None, ge=0, le=100, description="컨디션 지수. 활동 0 창이면 null")
    prob: float | None = Field(None, ge=0, le=1, description="보정 후 확률")
    model_version: str

@app.get("/health")
def health():
    return {"ok": True, "service": "model", "model_version": MODEL_VERSION}

@app.post("/predict", response_model=PredictOut)
def predict(body: PredictIn):
    # TODO(혜윤 predict.py): 활동0 필터 → 중앙값 대체 → 표준화 → z → sigmoid → isotonic → 0~100
    return PredictOut(score=71, prob=0.71, model_version=MODEL_VERSION)
