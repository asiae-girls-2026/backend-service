"""model 컨테이너 호출. 실패 시 None 반환 → 호출 측이 폴백(판정 없음) 처리.
standardized=True 면 이미 표준화된 입력(kbd_personalize shrinkage z-벡터) — 모델이 내장 스케일러를 건너뛴다 (11/6)."""
import os, time, httpx

MODEL_URL = os.environ.get("MODEL_URL", "http://model:8001")
_client = httpx.Client(base_url=MODEL_URL, timeout=3.0)
_info_cache: tuple[dict | None, float] = (None, 0.0)
INFO_TTL = 60.0

def predict(features: dict, standardized: bool = False) -> dict | None:
    try:
        body = {"features": features}
        if standardized:
            body["standardized"] = True
        r = _client.post("/predict", json=body)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError:
        return None

def model_info() -> dict | None:
    """GET /health — 60초 캐시 (창마다 다시 묻지 않음)."""
    global _info_cache
    info, ts = _info_cache
    if info is not None and time.monotonic() - ts < INFO_TTL:
        return info
    try:
        info = _client.get("/health").json()
        _info_cache = (info, time.monotonic())
        return info
    except httpx.HTTPError:
        return None
