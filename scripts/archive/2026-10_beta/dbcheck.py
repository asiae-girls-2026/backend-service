#!/usr/bin/env python3
"""케어퐁 DB 점검 — QA 시트 8번(서버데이터확인) 용. api 컨테이너 안에서 실행 (DATABASE_URL 사용).

EC2 터미널에서:
  docker exec -i <api컨테이너> python - < scripts/dbcheck.py      (파일이 EC2에 있을 때)
  또는 아래처럼 통째로 붙여넣기:
  docker exec -i <api컨테이너> python - <<'EOF'
  ...이 파일 내용...
  EOF

출력 5개: ① 사용자별 창 수  ② 라벨↔창 연결  ③ 이벤트  ④ 수집 빈 구간  ⑤ 방해금지·활동0 비율
스모크 데이터(CP-S…)는 제외. 읽기만 하고 아무것도 바꾸지 않는다.
"""
import os, psycopg

c = psycopg.connect(os.environ["DATABASE_URL"])
q = lambda sql: c.execute(sql).fetchall()
REAL = "u.cp_number NOT LIKE 'CP-S%'"

def show(title, rows, cols):
    print(f"\n── {title}")
    if not rows: print("   (없음)"); return
    w = [max(len(str(x)) for x in [h] + [r[i] for r in rows]) for i, h in enumerate(cols)]
    print("   " + "  ".join(h.ljust(w[i]) for i, h in enumerate(cols)))
    for r in rows: print("   " + "  ".join(str(x).ljust(w[i]) for i, x in enumerate(r)))

# ① 사용자별 창 수 — 팀원 전원 나와야 함. n=켜둔 분 수와 비슷, scored>0, null_model 에 'unavailable' 많으면 모델 서버 문제
show("① 사용자별 1분 창 (최근 2일)", q(f"""
  SELECT u.cp_number, u.app_version, COUNT(*) n, COUNT(w.condition_score) scored,
         SUM((w.activity_zero)::int) act0, SUM((w.dnd)::int) dnd,
         SUM((w.model_version='unavailable')::int) model_fail,
         ROUND(AVG(w.condition_score),1) avg_score, MAX(w.condition_score) max_score,
         SUM((w.prompt_type<>'none')::int) prompts,
         to_char(MIN(w.window_start) AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI') first,
         to_char(MAX(w.window_start) AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI') last
  FROM windows w JOIN users u ON u.user_id=w.user_id
  WHERE {REAL} AND w.window_start > now() - interval '2 days'
  GROUP BY 1,2 ORDER BY 1"""),
  ["회원번호","앱버전","n","scored","활동0","dnd","모델실패","평균","최고","팝업","처음","마지막"])

# ② 라벨 ↔ 창 — linked 가 n 과 같아야 함 (체크인 trigger=checkin 은 window_id 없는 게 정상)
show("② 자가보고(라벨) ↔ 창 연결", q(f"""
  SELECT u.cp_number, s.trigger, s.status, COUNT(*) n, COUNT(w.window_id) linked,
         SUM((s.yesno=1)::int) yes, SUM((s.yesno=0)::int) no,
         ROUND(AVG(s.latency_sec)) avg_latency_s,
         to_char(MAX(s.answered_at) AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI') last
  FROM self_reports s JOIN users u ON u.user_id=s.user_id
  LEFT JOIN windows w ON w.window_id=s.window_id
  WHERE {REAL} GROUP BY 1,2,3 ORDER BY 1,2,3"""),
  ["회원번호","trigger","status","n","linked","예","아니오","응답초","마지막"])

# ③ 이벤트 — app_start·app_health 가 보여야 앱이 배치를 보내는 것
show("③ 이벤트 종류별 (최근 2일)", q(f"""
  SELECT e.event, COUNT(*) n, COUNT(DISTINCT e.user_id) users,
         to_char(MAX(e.occurred_at) AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI') last_occurred,
         to_char(MAX(e.received_at) AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI') last_received
  FROM events e JOIN users u ON u.user_id=e.user_id
  WHERE {REAL} AND e.occurred_at > now() - interval '2 days'
  GROUP BY 1 ORDER BY 2 DESC"""),
  ["event","n","사용자","마지막 발생","마지막 수신"])

# ④ 수집 빈 구간 — 같은 사람 창 사이가 3분 넘게 비면 그 시간에 뭐였는지 (잠자기·방해금지·앱 종료) 확인
show("④ 3분 넘게 빈 구간 (큰 순 10개)", q(f"""
  SELECT * FROM (
    SELECT u.cp_number,
           to_char(w.window_start AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI') at,
           ROUND(EXTRACT(EPOCH FROM (w.window_start - LAG(w.window_start) OVER (PARTITION BY w.user_id ORDER BY w.window_start)))/60) gap_min
    FROM windows w JOIN users u ON u.user_id=w.user_id
    WHERE {REAL} AND w.window_start > now() - interval '2 days') t
  WHERE gap_min > 3 ORDER BY gap_min DESC LIMIT 10"""),
  ["회원번호","창 시각","빈 분"])

# ⑥ 가설별 마커 — QA 시트 8번 "가설 검증 가능한지": 가설마다 DB 에 있어야 하는 이벤트가 실제로 들어왔는지
ev_cnt = dict(q(f"""SELECT e.event, COUNT(*) FROM events e JOIN users u ON u.user_id=e.user_id
                    WHERE {REAL} AND e.occurred_at > now() - interval '2 days' GROUP BY 1"""))
HYP = [  # (가설, 필요한 이벤트들, 비고)
    ("M1 과부하↔스트레스", ["stress_prompt_shown", "stress_prompt_answered", "stress_score_answered"], "+ ②linked (창↔라벨)"),
    ("M2 한국 직장인 적용", [], "창 features 16개는 ①에 있음. 한글 입력 비율(ko_ratio)은 수집 안 함(혜윤 10/2) → 2차"),
    ("S1 지속 이용",      ["app_start", "heartbeat", "app_quit"], "7일 무접속은 users.last_seen_at"),
    ("S2 완화 효과",      ["relief_selected", "relief_completed", "relief_abandoned"], "props.kind(breath/meditate/chat)·window_id 가 있어야 전후 비교 가능"),
    ("S3 업무 방해 안 함", ["app_quit", "dnd_toggle", "collector_toggle"], "+ 알람 45초 무응답 = alarm_shown − alarm_answered"),
    ("S4 알람·완화 니즈",  ["alarm_shown", "alarm_answered", "med_register_completed", "water_goal_changed", "tab_opened"], ""),
    ("B1 강제 종료",      ["app_start", "app_health"], "app_start props 에 이전 세션 정상종료 플래그 있어야 함 — 아래 ⑦"),
    ("B2 무겁지 않음",    ["app_health"], "props.cpu/mem + ④ 빈 구간 + ⑦ 조인"),
]
print("\n── ⑥ 가설별 마커 (최근 2일 이벤트 수)")
for name, evs, note in HYP:
    parts = [f"{e}={ev_cnt.get(e, 0)}" for e in evs]
    missing = [e for e in evs if not ev_cnt.get(e)]
    flag = "OK " if evs and not missing else ("확인" if evs else "-- ")
    print(f"   [{flag}] {name:14s} {' '.join(parts)}" + (f"   ← 없음: {','.join(missing)}" if missing else "") + (f"   ({note})" if note else ""))

# ⑦ 조인·props 점검 — B1/B2: 회원번호로 세 테이블이 전부 붙는가, app_start·app_health props 에 뭐가 오는가
show("⑦ 사용자 ↔ pet_state ↔ routine_settings ↔ windows 조인 (B2) — ※ /v1/pet·/v1/routines 는 1차 stub(저장 안 함) → pet/routine False 가 정상, 창·이벤트만 봄", q(f"""
  SELECT u.cp_number, (p.user_id IS NOT NULL) pet, (r.user_id IS NOT NULL) routine,
         (SELECT COUNT(*) FROM windows w WHERE w.user_id=u.user_id) windows,
         (SELECT COUNT(*) FROM events e WHERE e.user_id=u.user_id) events,
         to_char(u.last_seen_at AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI') last_seen
  FROM users u LEFT JOIN pet_state p ON p.user_id=u.user_id LEFT JOIN routine_settings r ON r.user_id=u.user_id
  WHERE {REAL} ORDER BY 1"""), ["회원번호","pet_state","routine","창","이벤트","마지막 접속"])
show("⑦-b app_start / app_health props 샘플 (B1 정상종료 플래그 · B2 cpu/mem)", q(f"""
  SELECT e.event, e.props::text, to_char(e.occurred_at AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI') at
  FROM events e JOIN users u ON u.user_id=e.user_id
  WHERE {REAL} AND e.event IN ('app_start','app_health') ORDER BY e.occurred_at DESC LIMIT 4"""), ["event","props","시각"])

# ⑤ 전체 요약
(users, wins, scored, reports, events) = q(f"""
  SELECT (SELECT COUNT(*) FROM users u WHERE {REAL}),
         (SELECT COUNT(*) FROM windows w JOIN users u ON u.user_id=w.user_id WHERE {REAL}),
         (SELECT COUNT(*) FROM windows w JOIN users u ON u.user_id=w.user_id WHERE {REAL} AND w.condition_score IS NOT NULL),
         (SELECT COUNT(*) FROM self_reports s JOIN users u ON u.user_id=s.user_id WHERE {REAL}),
         (SELECT COUNT(*) FROM events e JOIN users u ON u.user_id=e.user_id WHERE {REAL})""")[0]
print(f"\n── ⑤ 전체 (스모크 제외): 사용자 {users} · 창 {wins} (점수 있음 {scored}) · 라벨 {reports} · 이벤트 {events}")
print("   판정: 팀원 수만큼 ①에 보이고, scored>0, ②linked=n, ③에 app_start 있으면 배포 OK")
