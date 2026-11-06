"""/windows — 활동 0 창은 모델을 거치지 않고 null, 모델 실패 폴백, 배치 상한. 실행: cd api && pytest"""
from fastapi.testclient import TestClient
from app.main import app
from app.services import model_client

client = TestClient(app)
BASE = {"window_id": "w1", "session_id": "s1", "window_start": "2026-10-07T10:00:00+09:00", "client_event_id": "e1"}

def test_activity_zero_returns_null(monkeypatch):
    called = []
    monkeypatch.setattr(model_client, "predict", lambda f: called.append(f) or {"score": 70, "level": "intervene", "model_version": "v1"})
    r = client.post("/v1/windows", json={**BASE, "features": {"SnKeyStrokes": 0, "SnMouseAct": 0}})
    assert r.status_code == 200 and r.json()["condition_score"] is None and called == []

def test_model_failure_fallback(monkeypatch):
    monkeypatch.setattr(model_client, "predict", lambda f: None)
    r = client.post("/v1/windows", json={**BASE, "features": {"SnKeyStrokes": 10, "SnMouseAct": 3}})
    j = r.json(); assert j["model_version"] == "unavailable" and j["prompt"] is False

def test_prediction_passthrough(monkeypatch):
    monkeypatch.setattr(model_client, "predict", lambda f: {"score": 55, "level": "observe", "model_version": "v1"})
    monkeypatch.setattr(model_client, "model_info", lambda: {"cut_intervene": 63})
    r = client.post("/v1/windows", json={**BASE, "features": {"SnKeyStrokes": 10, "SnMouseAct": 3}}, headers={"X-User-Id": "t1"})
    assert r.json()["condition_score"] == 55

def test_batch_limit():
    ws = [{**BASE, "window_id": f"w{i}", "features": {"SnKeyStrokes": 0, "SnMouseAct": 0}} for i in range(241)]
    assert client.post("/v1/windows/batch", json={"windows": ws}).status_code == 422

def test_events_rejects_unknown_name():
    ev = lambda n, i: {"client_event_id": i, "event": n, "occurred_at": "2026-10-07T09:00:00+09:00", "props": {}, "app_version": "0.1.0"}
    r = client.post("/v1/events/batch", json={"events": [ev("alarm_answered", "a"), ev("made_up", "b")]})
    assert r.json() == {"accepted_ids": ["a"], "rejected_ids": ["b"]}


# ── 키보드 개인화 서빙 훅 (11/6, 혜윤 carepong-automation) ──
class _FakeConn:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): return self

def _db_on(monkeypatch):
    from app.services import db
    monkeypatch.setattr(db, "available", lambda: True)
    monkeypatch.setattr(db, "ensure_user", lambda cp, *a, **k: "u1")
    monkeypatch.setattr(db, "ensure_session", lambda *a, **k: None)
    monkeypatch.setattr(db, "conn", lambda pool=None: _FakeConn())

def test_personalized_vector_goes_to_model_standardized(monkeypatch):
    from app.services import kbd_personalize
    from app.routers import windows as wr
    _db_on(monkeypatch); monkeypatch.setattr(wr, "KBD_PERSONALIZE", True)
    monkeypatch.setattr(kbd_personalize, "get_personalized_features", lambda uid, conn=None, **k: [0.0] * 16)
    sent = []
    monkeypatch.setattr(model_client, "predict", lambda f, standardized=False: sent.append((f, standardized)) or {"score": 40, "level": "normal", "model_version": "v1"})
    monkeypatch.setattr(model_client, "model_info", lambda: {"cut_intervene": 63, "standardized_input": True})
    r = client.post("/v1/windows", json={**BASE, "features": {"SnKeyStrokes": 10, "SnMouseAct": 3}}, headers={"X-User-Id": "t1"})
    assert r.status_code == 200 and sent == [(dict(zip(kbd_personalize.FEATS, [0.0] * 16)), True)]
    assert r.json()["condition_score"] == 40 and r.json()["model_version"] == "v1+kbd"

def test_old_model_without_standardized_flag_skips_personalization(monkeypatch):
    """ML EC2 모델이 아직 구버전(health 에 standardized_input 없음)이면 z-벡터를 보내지 않는다 — 스케일러 이중 적용 방지."""
    from app.services import kbd_personalize
    from app.routers import windows as wr
    _db_on(monkeypatch); monkeypatch.setattr(wr, "KBD_PERSONALIZE", True)
    called = []
    monkeypatch.setattr(kbd_personalize, "get_personalized_features", lambda *a, **k: called.append(1) or [0.0] * 16)
    sent = []
    monkeypatch.setattr(model_client, "predict", lambda f, standardized=False: sent.append(standardized) or {"score": 55, "level": "observe", "model_version": "v1"})
    monkeypatch.setattr(model_client, "model_info", lambda: {"cut_intervene": 63})
    r = client.post("/v1/windows", json={**BASE, "features": {"SnKeyStrokes": 10, "SnMouseAct": 3}}, headers={"X-User-Id": "t1"})
    assert r.status_code == 200 and called == [] and sent == [False] and r.json()["model_version"] == "v1"

def test_no_baseline_falls_back_to_raw_features(monkeypatch):
    from app.services import kbd_personalize
    from app.routers import windows as wr
    _db_on(monkeypatch); monkeypatch.setattr(wr, "KBD_PERSONALIZE", True)
    monkeypatch.setattr(kbd_personalize, "get_personalized_features", lambda uid, conn=None, **k: None)
    sent = []
    monkeypatch.setattr(model_client, "predict", lambda f, standardized=False: sent.append((f, standardized)) or {"score": 55, "level": "observe", "model_version": "v1"})
    monkeypatch.setattr(model_client, "model_info", lambda: {"cut_intervene": 63, "standardized_input": True})
    r = client.post("/v1/windows", json={**BASE, "features": {"SnKeyStrokes": 10, "SnMouseAct": 3}}, headers={"X-User-Id": "t1"})
    assert len(sent) == 1 and sent[0][1] is False and sent[0][0]["SnKeyStrokes"] == 10
    assert r.json()["model_version"] == "v1"

def test_personalize_error_never_breaks_window(monkeypatch):
    from app.services import kbd_personalize
    from app.routers import windows as wr
    _db_on(monkeypatch); monkeypatch.setattr(wr, "KBD_PERSONALIZE", True)
    def boom(*a, **k): raise RuntimeError("kbd_global_seed 없음")
    monkeypatch.setattr(kbd_personalize, "get_personalized_features", boom)
    monkeypatch.setattr(model_client, "predict", lambda f, standardized=False: {"score": 30, "level": "normal", "model_version": "v1"})
    monkeypatch.setattr(model_client, "model_info", lambda: {"cut_intervene": 63, "standardized_input": True})
    r = client.post("/v1/windows", json={**BASE, "features": {"SnKeyStrokes": 10, "SnMouseAct": 3}}, headers={"X-User-Id": "t1"})
    assert r.status_code == 200 and r.json()["condition_score"] == 30

def test_batch_does_not_personalize(monkeypatch):
    from app.services import kbd_personalize
    from app.routers import windows as wr
    _db_on(monkeypatch); monkeypatch.setattr(wr, "KBD_PERSONALIZE", True)
    called = []
    monkeypatch.setattr(kbd_personalize, "get_personalized_features", lambda *a, **k: called.append(1) or [0.0] * 16)
    monkeypatch.setattr(model_client, "predict", lambda f, standardized=False: {"score": 30, "level": "normal", "model_version": "v1"})
    monkeypatch.setattr(model_client, "model_info", lambda: {"cut_intervene": 63})
    ws = [{**BASE, "window_id": "b1", "features": {"SnKeyStrokes": 10, "SnMouseAct": 3}}]
    assert client.post("/v1/windows/batch", json={"windows": ws}, headers={"X-User-Id": "t1"}).status_code == 200 and called == []
