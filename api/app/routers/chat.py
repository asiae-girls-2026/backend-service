"""공감챗 (M05 마음관리 챗) — 앱이 사용자 메시지 1건(+최근 대화)을 보내면 Bedrock 프롬프트(services/bedrock)로 답변 생성.
1차(10/14): 대화 이력 없이 1건 단발. 위기 키워드는 앱(core/empathy.ts isCrisis)이 먼저 걸러 서버에 보내지 않는다.
10/19(혜윤 이슈 3건, issues/20261019-01):
  ① 맥락 유지 — 요청에 history(최근 대화, 최대 10개 메시지 = 5턴) 를 받아 '사용자: …/펫: …' 줄로 만들어 프롬프트 변수 {{history}} 로 전달.
     이력은 앱 로컬에만 있고(24h 뒤 앱이 삭제) 서버는 저장·로깅하지 않는다. history 없이 오는 옛 앱(≤0.3.2)도 그대로 동작.
  ② 마무리 규칙 — 프롬프트 v3 (서버 변경 없음).
  ③ 난타 필터 — services/gibberish.is_meaningless() 가 True 면 Bedrock 을 부르지 않고 고정 안내문(CHAT_FILTER_REPLY) 을 source="filter" 로 회신.
     하루 상한에도 안 센다. 하트는 앱이 매기므로 앱도 같은 규칙으로 하트를 막아야 한다(연우 0.3.3).
서버가 503/502/429 를 주면 앱은 기존 규칙 기반 replyEmpathy 로 폴백 — 사용자에겐 끊김 없음.
원문 저장·로깅 없음. 이벤트 집계는 앱이 기존대로 /events/batch 의 chat_message{len, keyword_hit, source} 로.
하루 상한(CHAT_DAILY_MAX, 기본 30/사용자)은 과금 캡 — 메모리 카운터(워커 1개 전제, policy 상태와 같은 방식)."""
import os, logging
from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from ..services import bedrock
from ..services.gibberish import is_meaningless

log = logging.getLogger("carepong.chat")
router = APIRouter(tags=["공감챗 (M05)"])

KST = timezone(timedelta(hours=9))
DAILY_MAX = int(os.environ.get("CHAT_DAILY_MAX", "30"))
HISTORY_MAX = 10   # 메시지 수 상한 = 5턴 (혜윤 권장값). 더 오면 최근 것만 남긴다
FILTER_REPLY = os.environ.get("CHAT_FILTER_REPLY", "하고 싶은 말을 조금만 더 적어 주실래요?")
_daily: dict[str, tuple[str, int]] = {}   # user → (KST 날짜, 오늘 호출 수)

class HistoryItem(BaseModel):
    role: Literal["user", "pet"] = Field(..., description="user = 사용자, pet = 펫(봇) 답변")
    text: str = Field(..., min_length=1, max_length=500)

class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=500, examples=["동료가 내 아이디어를 자기 거처럼 발표했어"],
                         description="사용자 입력 원문. 서버는 저장·로깅하지 않고 Bedrock 에만 전달")
    client_event_id: str = Field(..., description="앱이 만든 고유 ID (재전송 추적용)")
    history: list[HistoryItem] = Field(default_factory=list, max_length=HISTORY_MAX,
                                       description="[0.3.3+] 같은 세션의 최근 대화, 오래된 것부터 순서대로, 최대 10개(5턴). "
                                                   "이번 message 는 넣지 않는다. 앱 로컬에서만 보관(24h) — 서버 저장·로깅 없음. 생략 가능")

class ChatOut(BaseModel):
    reply: str = Field(..., description="펫 답변 (프롬프트 v3: 2문장 이내) 또는 필터 고정 안내문")
    source: str = Field("bedrock", examples=["bedrock", "filter"],
                        description="bedrock = LLM 답변. filter = 난타 입력이라 LLM 미호출·고정 안내문 — 앱은 하트를 올리지 말고 chat_message.source 도 'filter' 로. 앱 폴백 시엔 앱이 'rule' 로 기록")
    model_id: str = Field("", description="Bedrock 프롬프트 ID (버전 포함). filter 면 빈 값")
    latency_ms: int = 0

def _count(user: str) -> int:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    d, n = _daily.get(user, (today, 0))
    n = n + 1 if d == today else 1
    _daily[user] = (today, n)
    return n

def history_text(items: list[HistoryItem]) -> str:
    """최근 HISTORY_MAX 개만, '사용자: …' / '펫: …' 한 줄씩. 줄바꿈은 공백으로 — 프롬프트 변수 한 덩어리로 들어간다."""
    label = {"user": "사용자", "pet": "펫"}
    return "\n".join(f"{label[h.role]}: {' '.join(h.text.split())}" for h in items[-HISTORY_MAX:])

@router.post("/chat", response_model=ChatOut, summary="[1차] 공감챗 1건(+최근 대화) → Bedrock 프롬프트 응답",
             responses={503: {"description": "BEDROCK_PROMPT_ARN 미설정 — 앱은 규칙 응답으로 폴백"},
                        502: {"description": "Bedrock 호출 실패(권한·시간초과) — 앱 폴백"},
                        429: {"description": "하루 상한 초과 — 앱 폴백"}})
async def chat(body: ChatIn, x_user_id: str = Header("dev")):
    bad, why = is_meaningless(body.message)
    if bad:   # ③ 난타 — LLM·하루 상한 모두 건너뜀. 원문은 로그에 안 남김(사유만)
        log.info("chat filtered len=%d why=%s", len(body.message), why)
        return ChatOut(reply=FILTER_REPLY, source="filter")
    if not bedrock.enabled():
        raise HTTPException(503, "chat disabled")
    if _count(x_user_id) > DAILY_MAX:
        raise HTTPException(429, "daily limit")
    r = await run_in_threadpool(bedrock.reply, body.message, history_text(body.history))   # 워커 1개 — /windows 를 막지 않게 스레드에서
    if r is None:
        raise HTTPException(502, "bedrock unavailable")
    log.info("chat ok len=%d hist=%d latency=%dms in=%s out=%s", len(body.message), len(body.history), r["latency_ms"],
             r["usage"].get("inputTokens"), r["usage"].get("outputTokens"))
    return ChatOut(reply=r["text"], model_id=r["model_id"], latency_ms=r["latency_ms"])
