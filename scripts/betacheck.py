#!/usr/bin/env python3
"""케어퐁 베타 수집 현황 — 배포 다음날 "잘 들어오고 있나" 전용. dbcheck.py(가설 검증용)보다 '지금 흐름' 에 초점.
api 컨테이너 안에서 실행 (DATABASE_URL 사용). 읽기만 한다.

EC2 에서:
  docker exec -i $(docker ps --format '{{.Names}}' | grep -i api | head -1) python - < scripts/betacheck.py
  (파일이 EC2 에 없으면 아래 내용을 통째로 heredoc 으로 붙여넣기 — dbcheck_paste.txt 와 같은 방식)

인자 없이 돌리면 SINCE = 어제 17:00 KST. 바꾸려면 환경변수 SINCE='2026-10-14 17:00' 로.

블록:
 A 전체 요약(기준 시각 이후) — 사용자·창·라벨·이벤트 수, 마지막 수신 n분 전
 B 사용자별 — 등록 시각·토큰 여부·앱버전·오늘 창 수·모델실패·팝업·라벨·마지막 창·마지막 접속
 C 시간대별 창 수 (오늘, 사용자별 pivot) — 어느 시간에 빠졌는지 한눈에
 D 모델·저장 품질 — unavailable 비율, 점수 분포, 방해금지 시간대(18:30~08:30) 창 유입(=앱 버그)
 E 팝업 → 라벨 짝 — 서버가 띄운 창(prompt_type<>'none') 중 self_report 가 온 비율
 F 이벤트 — 종류별 수, 사용자 수, chat_message source(rule/bedrock), app_health mem, 거절 흔적
 G 수집 빈 구간 10분 이상 (사용자별 상위) — 재부팅·슬립·앱종료 추정
 H 시계 어긋남 — window_start vs received_at 차이 (앱 시계가 틀리거나 재전송 지연)
"""
import os, psycopg
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)
default_since = (now_kst - timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
SINCE = os.environ.get("SINCE") or default_since.strftime("%Y-%m-%d %H:%M")
SINCE_SQL = f"(timestamp '{SINCE}' AT TIME ZONE 'Asia/Seoul')"
TODAY_SQL = "(date_trunc('day', now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul')"
REAL = "u.cp_number NOT LIKE 'CP-S%'"
K = "AT TIME ZONE 'Asia/Seoul'"

c = psycopg.connect(os.environ["DATABASE_URL"])
q = lambda sql: c.execute(sql).fetchall()

def show(title, rows, cols):
    print(f"\n── {title}")
    if not rows: print("   (없음)"); return
    w = [max(len(str(x)) for x in [h] + [r[i] for r in rows]) for i, h in enumerate(cols)]
    print("   " + "  ".join(h.ljust(w[i]) for i, h in enumerate(cols)))
    for r in rows: print("   " + "  ".join(str(x).ljust(w[i]) for i, x in enumerate(r)))

print(f"케어퐁 베타 현황  기준: {SINCE} KST 이후  (지금 {now_kst:%m-%d %H:%M} KST, 스모크 CP-S* 제외)")

# A 전체 요약
(users_all, users_new, wins, scored, reports, events, last_w, last_e, last_r) = q(f"""
  SELECT (SELECT COUNT(*) FROM users u WHERE {REAL}),
         (SELECT COUNT(*) FROM users u WHERE {REAL} AND u.created_at >= {SINCE_SQL}),
         (SELECT COUNT(*) FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.received_at >= {SINCE_SQL}),
         (SELECT COUNT(*) FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.received_at >= {SINCE_SQL} AND w.condition_score IS NOT NULL),
         (SELECT COUNT(*) FROM self_reports s JOIN users u USING(user_id) WHERE {REAL} AND s.received_at >= {SINCE_SQL}),
         (SELECT COUNT(*) FROM events e JOIN users u USING(user_id) WHERE {REAL} AND e.received_at >= {SINCE_SQL}),
         (SELECT ROUND(EXTRACT(EPOCH FROM (now() - MAX(w.received_at)))/60) FROM windows w JOIN users u USING(user_id) WHERE {REAL}),
         (SELECT ROUND(EXTRACT(EPOCH FROM (now() - MAX(e.received_at)))/60) FROM events e JOIN users u USING(user_id) WHERE {REAL}),
         (SELECT ROUND(EXTRACT(EPOCH FROM (now() - MAX(s.received_at)))/60) FROM self_reports s JOIN users u USING(user_id) WHERE {REAL})""")[0]
print(f"\n── A 전체: 사용자 누적 {users_all} (기준 이후 신규 {users_new}) · 창 {wins} (점수 {scored}) · 라벨 {reports} · 이벤트 {events}")
print(f"   마지막 수신: 창 {last_w}분 전 · 이벤트 {last_e}분 전 · 라벨 {last_r}분 전   ← 08:30~18:30 평일이면 창은 1~2분 전이어야 정상")

# B 사용자별
show("B 사용자별 (기준 이후)", q(f"""
  SELECT u.cp_number, to_char(u.created_at {K},'MM-DD HH24:MI') enrolled, (u.token_hash IS NOT NULL) tok, u.app_version,
         COUNT(w.window_id) n, COUNT(w.condition_score) scored,
         SUM((w.activity_zero)::int) act0, SUM((w.model_version='unavailable')::int) mfail,
         ROUND(AVG(w.condition_score),1) avg, MAX(w.condition_score) max,
         SUM((w.prompt_type<>'none')::int) prompts,
         (SELECT COUNT(*) FROM self_reports s WHERE s.user_id=u.user_id AND s.received_at >= {SINCE_SQL}) labels,
         (SELECT COUNT(*) FROM events e WHERE e.user_id=u.user_id AND e.received_at >= {SINCE_SQL}) ev,
         to_char(MAX(w.window_start) {K},'MM-DD HH24:MI') last_win,
         to_char(u.last_seen_at {K},'MM-DD HH24:MI') last_seen
  FROM users u LEFT JOIN windows w ON w.user_id=u.user_id AND w.received_at >= {SINCE_SQL}
  WHERE {REAL} GROUP BY u.user_id ORDER BY n DESC, u.created_at"""),
  ["회원번호","등록","토큰","앱버전","창","점수","활동0","모델실패","평균","최고","팝업","라벨","이벤트","마지막창","마지막접속"])
print("   판정: 창 0 이고 이벤트만 있음 = 수집기 안 돎(collector_stopped·inference_error 이벤트 F 확인). 토큰 False = 구버전 앱(X-User-Id 경로) → raw 업로드 불가")

# C 시간대별 창 수 (오늘)
rows = q(f"""
  SELECT u.cp_number, EXTRACT(HOUR FROM w.window_start {K})::int h, COUNT(*)
  FROM windows w JOIN users u USING(user_id)
  WHERE {REAL} AND w.window_start >= {TODAY_SQL} GROUP BY 1,2 ORDER BY 1,2""")
if rows:
    hours = list(range(8, 19))
    piv = {}
    for cp, h, n in rows: piv.setdefault(cp, {})[h] = n
    print(f"\n── C 오늘 시간대별 창 수 (08~18시, 칸당 최대 60)")
    print("   " + "회원번호".ljust(12) + "  " + " ".join(f"{h:>3}" for h in hours) + "  합계")
    for cp, d in sorted(piv.items()):
        print("   " + cp.ljust(12) + "  " + " ".join(f"{d.get(h,0):>3}" for h in hours) + f"  {sum(d.values())}")
    print("   판정: 켜둔 시간대가 50~60 이면 정상. 20~40 = 슬립/잠금/수집기 멈춤 의심. 08:30 전·18:30 후 칸에 수가 있으면 방해금지 미적용")
else:
    print("\n── C 오늘 창 없음")

# D 품질
show("D 모델·저장 품질 (기준 이후)", q(f"""
  SELECT COUNT(*) n,
         SUM((w.activity_zero)::int) act0,
         SUM((w.model_version='unavailable')::int) model_unavailable,
         SUM((w.condition_score>=63)::int) ge63, SUM((w.condition_score BETWEEN 50 AND 62.9)::int) observe, SUM((w.condition_score<50)::int) normal,
         SUM((w.dnd)::int) dnd_flag,
         SUM(((w.window_start {K})::time NOT BETWEEN '08:30' AND '18:30')::int) outside_hours,
         SUM((w.imputed IS NOT NULL AND array_length(w.imputed,1)>0)::int) imputed
  FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.received_at >= {SINCE_SQL}"""),
  ["창","활동0","모델실패","≥63","50~62","<50","dnd","수집시간밖","결측대체"])
print("   판정: 모델실패 > 0 이면 모델 EC2 확인(MODEL_URL). 수집시간밖 > 0 이면 앱 방해금지 로직 확인. 결측대체 > 0 이면 수집기 피처 누락")

# E 팝업 → 라벨
show("E 서버 팝업 ↔ 라벨 짝 (기준 이후)", q(f"""
  SELECT w.prompt_type, COUNT(*) shown,
         COUNT(s.report_id) labeled,
         SUM((s.status='answered')::int) answered, SUM((s.status='dismissed')::int) dismissed, SUM((s.status='ignored')::int) ignored,
         SUM((s.yesno=1)::int) yes, SUM((s.yesno=0)::int) no, ROUND(AVG(s.latency_sec)) lat_s
  FROM windows w JOIN users u USING(user_id) LEFT JOIN self_reports s ON s.window_id=w.window_id
  WHERE {REAL} AND w.prompt_type<>'none' AND w.received_at >= {SINCE_SQL} GROUP BY 1 ORDER BY 2 DESC"""),
  ["prompt_type","띄움","라벨옴","answered","dismissed","ignored","예","아니오","응답초"])
show("E-2 라벨 중 창 없이 온 것 (checkin 외는 이상)", q(f"""
  SELECT s.trigger, s.status, COUNT(*) n FROM self_reports s JOIN users u USING(user_id)
  LEFT JOIN windows w ON w.window_id=s.window_id
  WHERE {REAL} AND s.received_at >= {SINCE_SQL} AND w.window_id IS NULL GROUP BY 1,2"""), ["trigger","status","n"])

# F 이벤트
show("F 이벤트 종류별 (기준 이후)", q(f"""
  SELECT e.event, COUNT(*) n, COUNT(DISTINCT e.user_id) users,
         to_char(MAX(e.occurred_at) {K},'MM-DD HH24:MI') last_occ, to_char(MAX(e.received_at) {K},'MM-DD HH24:MI') last_recv
  FROM events e JOIN users u USING(user_id) WHERE {REAL} AND e.received_at >= {SINCE_SQL} GROUP BY 1 ORDER BY 2 DESC"""),
  ["event","n","사용자","마지막발생","마지막수신"])
show("F-2 chat_message source (bedrock 비율 = LLM 실호출)", q(f"""
  SELECT COALESCE(e.props->>'source','(없음)') source, COUNT(*) n, COUNT(DISTINCT e.user_id) users,
         ROUND(AVG((e.props->>'len')::numeric)) avg_len, SUM(((e.props->>'keyword_hit')::boolean)::int) kw_hit
  FROM events e JOIN users u USING(user_id) WHERE {REAL} AND e.event='chat_message' AND e.received_at >= {SINCE_SQL} GROUP BY 1"""),
  ["source","n","사용자","평균길이","키워드"])
show("F-3 app_health 최근 (mem MB · crash · events_dropped)", q(f"""
  SELECT u.cp_number, e.props->>'mem' mem_mb, e.props->>'crash' crash, e.props->>'events_dropped' dropped, e.props->>'cpu' cpu,
         to_char(e.occurred_at {K},'MM-DD HH24:MI') at
  FROM events e JOIN users u USING(user_id) WHERE {REAL} AND e.event='app_health' AND e.received_at >= {SINCE_SQL}
  ORDER BY e.occurred_at DESC LIMIT 8"""), ["회원번호","mem","crash","dropped","cpu","시각"])
show("F-4 수집기 문제 이벤트", q(f"""
  SELECT u.cp_number, e.event, e.props::text, to_char(e.occurred_at {K},'MM-DD HH24:MI') at
  FROM events e JOIN users u USING(user_id) WHERE {REAL} AND e.event IN ('collector_stopped','inference_error','collector_toggle','beta_upload') AND e.received_at >= {SINCE_SQL}
  ORDER BY e.occurred_at DESC LIMIT 15"""), ["회원번호","event","props","시각"])

# G 빈 구간
show("G 10분 이상 빈 구간 (기준 이후, 큰 순 15)", q(f"""
  SELECT * FROM (
    SELECT u.cp_number, to_char(LAG(w.window_start) OVER (PARTITION BY w.user_id ORDER BY w.window_start) {K},'MM-DD HH24:MI') from_t,
           to_char(w.window_start {K},'MM-DD HH24:MI') to_t,
           ROUND(EXTRACT(EPOCH FROM (w.window_start - LAG(w.window_start) OVER (PARTITION BY w.user_id ORDER BY w.window_start)))/60) gap_min
    FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.window_start >= {SINCE_SQL}) t
  WHERE gap_min >= 10 ORDER BY gap_min DESC LIMIT 15"""), ["회원번호","from","to","빈 분"])
print("   판정: 18:30→08:30 사이 큰 구간은 정상(방해금지). 낮 시간 10분 이상은 F-4·app_start 와 대조")

# H 시계 어긋남
show("H window_start vs received_at 차이 (기준 이후)", q(f"""
  SELECT u.cp_number,
         ROUND(AVG(EXTRACT(EPOCH FROM (w.received_at - w.window_start)))) avg_s,
         ROUND(MAX(EXTRACT(EPOCH FROM (w.received_at - w.window_start)))) max_s,
         ROUND(MIN(EXTRACT(EPOCH FROM (w.received_at - w.window_start)))) min_s,
         SUM((ABS(EXTRACT(EPOCH FROM (w.received_at - w.window_start))) > 600)::int) over_10m
  FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.received_at >= {SINCE_SQL} GROUP BY 1 ORDER BY max_s DESC"""),
  ["회원번호","평균초","최대초","최소초","10분초과"])
print("   판정: 정상은 60~120초(창 끝나고 전송). 음수/수천 초 = PC 시계 오류 또는 오프라인 재전송(batch). 10분초과 많으면 그 사용자 네트워크 확인")

print("\n끝. 이상 없음 기준: A 마지막 창 ≤2분 · B 전원 토큰 True·모델실패 0 · C 근무시간 칸 50+ · D 수집시간밖 0 · E 띄움≈라벨옴 · F app_start·heartbeat·app_health 전원")
