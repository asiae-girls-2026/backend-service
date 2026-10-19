"""/chat — 미설정 503, Bedrock 실패 502, 성공 통과, 하루 상한 429. 실행: cd api && pytest"""
from fastapi.testclient import TestClient
from app.main import app
from app.services import bedrock
from app.routers import chat as chat_router

client = TestClient(app)
BODY = {"message": "동료가 내 아이디어를 자기 거처럼 발표했어", "client_event_id": "c1"}

def test_disabled_without_arn(monkeypatch):
    monkeypatch.setattr(bedrock, "PROMPT_ARN", "")
    assert client.post("/v1/chat", json=BODY).status_code == 503

def test_bedrock_failure_502(monkeypatch):
    monkeypatch.setattr(bedrock, "PROMPT_ARN", "arn:test")
    monkeypatch.setattr(bedrock, "reply", lambda m, h="": None)
    assert client.post("/v1/chat", json=BODY, headers={"X-User-Id": "u-fail"}).status_code == 502

def test_reply_passthrough(monkeypatch):
    monkeypatch.setattr(bedrock, "PROMPT_ARN", "arn:test")
    monkeypatch.setattr(bedrock, "reply", lambda m, h="": {"text": "그랬구나, 많이 속상했겠다.", "model_id": "p1:1", "latency_ms": 420, "usage": {}})
    r = client.post("/v1/chat", json=BODY, headers={"X-User-Id": "u-ok"})
    assert r.status_code == 200
    j = r.json(); assert j["reply"].startswith("그랬구나") and j["source"] == "bedrock" and j["model_id"] == "p1:1"

def test_validation():
    assert client.post("/v1/chat", json={"message": "", "client_event_id": "c"}).status_code == 422
    assert client.post("/v1/chat", json={"message": "x" * 501, "client_event_id": "c"}).status_code == 422

def test_daily_limit(monkeypatch):
    monkeypatch.setattr(bedrock, "PROMPT_ARN", "arn:test")
    monkeypatch.setattr(bedrock, "reply", lambda m, h="": {"text": "응", "model_id": "p", "latency_ms": 1, "usage": {}})
    monkeypatch.setattr(chat_router, "DAILY_MAX", 2)
    h = {"X-User-Id": "u-limit"}
    assert [client.post("/v1/chat", json=BODY, headers=h).status_code for _ in range(3)] == [200, 200, 429]

# ── 10/19 혜윤 이슈 ① 맥락 유지 · ③ 난타 필터 ──
def test_history_passed_to_bedrock(monkeypatch):
    monkeypatch.setattr(bedrock, "PROMPT_ARN", "arn:test")
    seen = {}
    def fake(msg, hist=""):
        seen["msg"], seen["hist"] = msg, hist
        return {"text": "이어서 들을게요.", "model_id": "p:3", "latency_ms": 1, "usage": {}}
    monkeypatch.setattr(bedrock, "reply", fake)
    body = {**BODY, "history": [{"role": "user", "text": "오늘 상사가\n보고서를 다 뒤집었어"},
                                 {"role": "pet", "text": "많이 속상하셨겠어요."}]}
    r = client.post("/v1/chat", json=body, headers={"X-User-Id": "u-hist"})
    assert r.status_code == 200 and r.json()["source"] == "bedrock"
    assert seen["msg"] == BODY["message"]
    assert seen["hist"] == "사용자: 오늘 상사가 보고서를 다 뒤집었어\n펫: 많이 속상하셨겠어요."   # 줄바꿈 → 공백, 순서 유지

def test_history_optional_and_capped(monkeypatch):
    monkeypatch.setattr(bedrock, "PROMPT_ARN", "arn:test")
    seen = {}
    monkeypatch.setattr(bedrock, "reply", lambda m, h="": seen.update(hist=h) or {"text": "네", "model_id": "p", "latency_ms": 1, "usage": {}})
    assert client.post("/v1/chat", json=BODY, headers={"X-User-Id": "u-old"}).status_code == 200   # 옛 앱: history 없음
    assert seen["hist"] == ""
    too_many = [{"role": "user", "text": f"m{i}"} for i in range(11)]
    assert client.post("/v1/chat", json={**BODY, "history": too_many}, headers={"X-User-Id": "u-old"}).status_code == 422
    assert client.post("/v1/chat", json={**BODY, "history": [{"role": "bot", "text": "x"}]}).status_code == 422

def test_gibberish_filtered_without_bedrock(monkeypatch):
    monkeypatch.setattr(bedrock, "PROMPT_ARN", "")          # ARN 없어도 필터가 먼저 → 503 이 아니라 200 filter
    called = []
    monkeypatch.setattr(bedrock, "reply", lambda *a: called.append(a))
    monkeypatch.setattr(chat_router, "DAILY_MAX", 1)
    h = {"X-User-Id": "u-gib"}
    for junk in ("ㅁㅇㄴㄹ", "ㅈㄷ9ㅁ려", "ㅁㅇㄴㄹ ㅈㄷ9ㅁ려"):
        r = client.post("/v1/chat", json={**BODY, "message": junk}, headers=h)
        assert r.status_code == 200, junk
        j = r.json(); assert j["source"] == "filter" and j["model_id"] == "" and j["reply"] == chat_router.FILTER_REPLY
    assert called == []
    # 필터 통과분은 하루 상한에 안 셌어야 한다 → 다음 정상 호출이 첫 호출로 카운트
    monkeypatch.setattr(bedrock, "PROMPT_ARN", "arn:test")
    monkeypatch.setattr(bedrock, "reply", lambda m, h="": {"text": "응", "model_id": "p", "latency_ms": 1, "usage": {}})
    assert client.post("/v1/chat", json={**BODY, "message": "ㅋㅋㅋ 오늘 야근"}, headers=h).status_code == 200

def test_bedrock_variables(monkeypatch):
    """services.bedrock.reply — history 변수 전송, 빈 이력은 '(없음)', BEDROCK_HISTORY_VAR='' 면 미전송."""
    class FakeClient:
        def __init__(self): self.kw = None
        def converse(self, **kw):
            self.kw = kw
            return {"output": {"message": {"content": [{"text": "답"}]}}, "usage": {"inputTokens": 1}}
    fc = FakeClient()
    monkeypatch.setattr(bedrock, "PROMPT_ARN", "arn:aws:bedrock:ap-northeast-2:1:prompt/X:3")
    monkeypatch.setattr(bedrock, "_get_client", lambda: fc)
    assert bedrock.reply("안녕", "사용자: a\n펫: b")["model_id"] == "X:3"
    assert fc.kw["promptVariables"] == {"user_message": {"text": "안녕"}, "history": {"text": "사용자: a\n펫: b"}}
    bedrock.reply("안녕")
    assert fc.kw["promptVariables"]["history"] == {"text": "(없음)"}
    monkeypatch.setattr(bedrock, "HISTORY_VAR", "")
    bedrock.reply("안녕", "x")
    assert "history" not in fc.kw["promptVariables"]
