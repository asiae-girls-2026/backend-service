#!/usr/bin/env python3
"""hypcheck.py 보조 — 1차 출력(10/23)에서 나온 이상 신호 확인. 읽기만 한다.
  ① 창이 분 수보다 많음(세션 기준 결손 −22%) → 같은 분에 창 2건 이상인 사용자·일
  ② heartbeat 없는 앱 버전 → 결손률① 분모 신뢰도
  ③ 앱 시계 앞섬(이벤트 마지막 17:40 인데 지금 15:18)
  ④ 방해금지 시간대(18:30~08:30)·주말 창 유입 (구버전 앱 버그)
  ⑤ 알람 빈도(사용자·일당 노출) — S4 무응답 96% 해석용
  ⑥ 최근 3일 활성 사용자 (S1 해석용)
실행: docker exec $C python /tmp/h2.py
"""
import os, psycopg

SINCE = os.environ.get("SINCE") or "2026-10-14 00:00"
S = f"(timestamp '{SINCE}' AT TIME ZONE 'Asia/Seoul')"
REAL = "u.cp_number NOT LIKE 'CP-S%'"
K = "AT TIME ZONE 'Asia/Seoul'"
c = psycopg.connect(os.environ["DATABASE_URL"])

def q(sql):
    try:
        return c.execute(sql).fetchall()
    except Exception as ex:
        c.rollback(); print(f"   !! SQL 오류: {str(ex).splitlines()[0][:160]}"); return []

def show(title, rows, cols):
    print(f"\n── {title}")
    if not rows: print("   (없음)"); return
    w = [max(len(str(x)) for x in [h] + [r[i] for r in rows]) for i, h in enumerate(cols)]
    print("   " + "  ".join(h.ljust(w[i]) for i, h in enumerate(cols)))
    for r in rows: print("   " + "  ".join(str(x).ljust(w[i]) for i, x in enumerate(r)))

show("① 사용자별 창 수 vs 서로 다른 분 수 (dup = 같은 분에 2건 이상)", q(f"""
  SELECT u.cp_number, u.app_version, COUNT(*) wins, COUNT(DISTINCT date_trunc('minute', w.window_start)) mins,
         COUNT(*) - COUNT(DISTINCT date_trunc('minute', w.window_start)) dup,
         COUNT(DISTINCT w.session_id) ses,
         (SELECT COUNT(*) FROM events e WHERE e.user_id=u.user_id AND e.event='heartbeat' AND e.occurred_at >= {S}) hb
  FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.window_start >= {S}
  GROUP BY u.user_id ORDER BY dup DESC LIMIT 15"""), ["회원번호", "앱버전", "창", "분", "dup", "세션", "heartbeat"])

show("①-b 같은 분에 창 2건 이상인 예 (한 사용자, 최근 5개)", q(f"""
  SELECT u.cp_number, to_char(w.window_start {K},'MM-DD HH24:MI') m, COUNT(*) n, COUNT(DISTINCT w.session_id) ses,
         string_agg(DISTINCT LEFT(w.session_id, 14), ',') sids, string_agg(DISTINCT w.model_version, ',') mv
  FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.window_start >= {S}
  GROUP BY u.user_id, u.cp_number, date_trunc('minute', w.window_start), m HAVING COUNT(*) > 1
  ORDER BY n DESC, m DESC LIMIT 5"""), ["회원번호", "분", "창", "세션수", "세션id앞", "모델버전"])

show("①-c 사용자·일별 창 수 상위 (하루 최대 601 이 정상)", q(f"""
  SELECT u.cp_number, u.app_version, (w.window_start {K})::date d, COUNT(*) wins, COUNT(DISTINCT date_trunc('minute', w.window_start)) mins
  FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.window_start >= {S}
  GROUP BY 1,2,3 ORDER BY wins DESC LIMIT 8"""), ["회원번호", "앱버전", "날짜", "창", "분"])

show("② 앱 버전별 사용자·창·heartbeat (heartbeat 0 인 버전은 결손률 분모에 안 잡힘)", q(f"""
  SELECT u.app_version, COUNT(DISTINCT u.user_id) users,
         COUNT(DISTINCT w.user_id) win_users, COUNT(w.window_id) wins,
         (SELECT COUNT(*) FROM events e JOIN users u2 USING(user_id) WHERE u2.app_version=u.app_version AND e.event='heartbeat' AND e.occurred_at >= {S}) hb,
         (SELECT COUNT(DISTINCT e.user_id) FROM events e JOIN users u2 USING(user_id) WHERE u2.app_version=u.app_version AND e.event='heartbeat' AND e.occurred_at >= {S}) hb_users
  FROM users u LEFT JOIN windows w ON w.user_id=u.user_id AND w.window_start >= {S}
  WHERE {REAL} GROUP BY 1 ORDER BY 1"""), ["앱버전", "사용자", "창있는사용자", "창", "heartbeat", "hb사용자"])

show("③ 앱 시계 앞섬 (서버 수신보다 5분 이상 미래) — 사용자별", q(f"""
  SELECT u.cp_number, u.app_version, COUNT(*) n, ROUND(MAX(EXTRACT(EPOCH FROM (e.occurred_at - e.received_at)))/60) max_min,
         to_char(MAX(e.occurred_at) {K},'MM-DD HH24:MI') last_occ, to_char(MAX(e.received_at) {K},'MM-DD HH24:MI') last_recv
  FROM events e JOIN users u USING(user_id) WHERE {REAL} AND e.occurred_at > e.received_at + interval '5 min'
  GROUP BY u.user_id ORDER BY max_min DESC LIMIT 8"""), ["회원번호", "앱버전", "건", "최대앞섬(분)", "마지막발생", "마지막수신"])

show("④ 방해금지 시간대(18:30~08:30)·주말 창 유입 — 앱 버전별", q(f"""
  SELECT u.app_version, COUNT(*) wins,
         COUNT(*) FILTER (WHERE (w.window_start {K})::time NOT BETWEEN '08:30' AND '18:30') night,
         COUNT(*) FILTER (WHERE EXTRACT(ISODOW FROM w.window_start {K}) >= 6) weekend,
         COUNT(DISTINCT w.user_id) FILTER (WHERE (w.window_start {K})::time NOT BETWEEN '08:30' AND '18:30') night_users
  FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.window_start >= {S}
  GROUP BY 1 ORDER BY 1"""), ["앱버전", "창", "야간창", "주말창", "야간사용자"])

show("⑤ 알람 종류별 사용자·일당 노출 횟수", q(f"""
  SELECT e.props->>'kind' kind, COUNT(*) shown, COUNT(DISTINCT (e.user_id, (e.occurred_at {K})::date)) user_days,
         ROUND(COUNT(*)::numeric / NULLIF(COUNT(DISTINCT (e.user_id, (e.occurred_at {K})::date)),0), 1) per_day
  FROM events e JOIN users u USING(user_id) WHERE {REAL} AND e.event='alarm_shown' AND e.occurred_at >= {S}
  GROUP BY 1 ORDER BY 1"""), ["kind", "노출", "사용자·일", "하루당"])

rows = q(f"""
  WITH a AS (SELECT w.user_id, (w.window_start {K})::date d FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.window_start >= {S}
             UNION SELECT e.user_id, (e.occurred_at {K})::date FROM events e JOIN users u USING(user_id) WHERE {REAL} AND e.occurred_at >= {S} AND e.event<>'app_health')
  SELECT COUNT(DISTINCT user_id),
         COUNT(DISTINCT user_id) FILTER (WHERE d >= (now() {K})::date - 2),
         COUNT(DISTINCT user_id) FILTER (WHERE d >= (now() {K})::date - 6),
         COUNT(DISTINCT user_id) FILTER (WHERE d >= (now() {K})::date - 2 AND EXISTS (SELECT 1 FROM users u WHERE u.user_id=a.user_id AND u.app_version >= '0.3'))
  FROM a""")
if rows:
    ever, d3, d7, d3new = rows[0]
    print(f"\n── ⑥ 기간 내 활성 {ever}명 중 최근 3일 활성 {d3} · 최근 7일 활성 {d7} · 최근 3일 활성 중 0.3.x 앱 {d3new}")
print("\n끝.")
