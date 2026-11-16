#!/usr/bin/env python3
"""케어퐁 1차 가설(S1~S4·B1·B2) 검증용 수치 — 채원 가설표(10/23) 기준. 읽기만 한다.
api 컨테이너 안에서 실행 (DATABASE_URL 사용):
  docker exec -i $(docker ps --format '{{.Names}}' | grep -i api | head -1) python - < scripts/hypcheck.py

기간: 환경변수 SINCE (기본 2026-10-14 00:00 KST = 1차 베타 배포일) ~ 지금. 스모크 CP-S* 제외.
수치는 n/분모 (xx.x%) 형식. 분모 0 이면 n/a.

블록:
 0  데이터 유입 — 테이블 건수, 이벤트명별 건수·사용자 수(허용 목록 중 0건 표시), 앱 버전
 S1 지속 이용 — 일별 활성 사용자, 사용자별 활성 일수, N일 무접속(이탈 추정), D-7 잔존, app_start/quit
 S2 완화 효과 — relief 종류별 선택·완료율, 완화 전후 10분 모델 점수, 자가보고 분포
 S3 업무 방해 — 업무시간(평일 08:30~18:30) 종료 비율, 방해금지 수동 토글
 S4 기능 니즈 — 알람 종류별 노출·응답, 기능별 사용 사용자 비율, 공감챗
 B1 강제 종료 — app_quit 없이 다시 app_start 한 비율, heartbeat 끊김, app_health 플래그
 B2 무겁지 않음 — 창 전송 지연, 1분 창 결손률(heartbeat·세션 기준), 조인·유실 점검
앱이 아예 안 보내는 항목(삭제, 종료 버튼 종류, 자동 방해금지, 크래시 플래그, 완화 후 자가보고)은 각 블록 '한계' 줄에 적음.
"""
import os, psycopg
from datetime import datetime, timedelta, timezone, date

KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)
SINCE = os.environ.get("SINCE") or "2026-10-14 00:00"
S = f"(timestamp '{SINCE}' AT TIME ZONE 'Asia/Seoul')"
REAL = "u.cp_number NOT LIKE 'CP-S%'"
K = "AT TIME ZONE 'Asia/Seoul'"
WORK = f"EXTRACT(ISODOW FROM e.occurred_at {K}) < 6 AND (e.occurred_at {K})::time BETWEEN '08:30' AND '18:30'"
EV = f"FROM events e JOIN users u USING(user_id) WHERE {REAL} AND e.occurred_at >= {S}"

c = psycopg.connect(os.environ["DATABASE_URL"])

def q(sql):
    try:
        return c.execute(sql).fetchall()
    except Exception as ex:
        c.rollback()
        print(f"   !! SQL 오류: {str(ex).splitlines()[0][:160]}")
        return []

def one(sql, default=None):
    r = q(sql)
    return r[0] if r else default

def pct(n, d):
    n = n or 0; d = d or 0
    return f"{n}/{d} ({100*n/d:.1f}%)" if d else f"{n}/0 (n/a)"

def f1(x):
    return "-" if x is None else f"{float(x):.1f}"

def show(title, rows, cols):
    print(f"\n   · {title}")
    if not rows:
        print("     (없음)"); return
    w = [max(len(str(x)) for x in [h] + [r[i] for r in rows]) for i, h in enumerate(cols)]
    print("     " + "  ".join(h.ljust(w[i]) for i, h in enumerate(cols)))
    for r in rows:
        print("     " + "  ".join(str(x).ljust(w[i]) for i, x in enumerate(r)))

def block(title, fn):
    print(f"\n━━ {title}")
    try:
        fn()
    except Exception as ex:
        c.rollback()
        print(f"   !! 블록 오류: {str(ex)[:200]}")

print(f"케어퐁 1차 가설 수치  기간: {SINCE} KST ~ 지금({now_kst:%m-%d %H:%M} KST)  스모크 CP-S* 제외")

# ───────────────────────── 0 데이터 유입
ALLOWED = ["app_start", "heartbeat", "app_quit", "app_health", "care_action", "dnd_toggle",
           "alarm_shown", "alarm_answered", "stress_prompt_shown", "stress_prompt_answered", "stress_score_answered",
           "relief_selected", "relief_completed", "relief_abandoned", "mood_checked", "chat_message",
           "med_register_started", "med_register_completed", "med_register_abandoned",
           "collector_toggle", "beta_upload", "water_goal_changed", "level_up", "rename", "rebirth",
           "collector_stopped", "inference_error", "tab_opened", "minihome_closed", "furniture_unlocked"]

def b0():
    for t in ["users", "sessions", "windows", "self_reports", "events", "daily_usage", "pet_state", "routine_settings", "user_prompt_state"]:
        n = one(f"SELECT COUNT(*) FROM {t}", (None,))[0]
        print(f"   {t:18s} {n}")
    print("   (daily_usage·pet_state·routine_settings·user_prompt_state 는 1차 stub → 0 이 정상)")
    users = one(f"SELECT COUNT(*) FROM users u WHERE {REAL}")[0]
    rows = q(f"SELECT e.event, COUNT(*), COUNT(DISTINCT e.user_id), to_char(MAX(e.occurred_at) {K},'MM-DD HH24:MI') {EV} GROUP BY 1 ORDER BY 2 DESC")
    have = {r[0]: r for r in rows}
    show(f"이벤트명별 (기간 내) — 사용자 {users}명", [(e, n, f"{u}/{users}", last) for e, n, u, last in rows], ["event", "건수", "사용자", "마지막"])
    zero = [e for e in ALLOWED if e not in have]
    print(f"   허용 목록 중 0건: {', '.join(zero) if zero else '없음'}")
    print("   (앱이 안 보내는 이름: med_register_*, collector_*, water_goal_changed, tab_opened, minihome_closed, furniture_unlocked, beta_upload, inference_error → 0 이 정상)")
    show("앱 버전별 사용자", q(f"SELECT u.app_version, COUNT(*) FROM users u WHERE {REAL} GROUP BY 1 ORDER BY 1"), ["버전", "사용자"])
    lw, le, lr = one(f"""SELECT ROUND(EXTRACT(EPOCH FROM (now()-MAX(w.received_at)))/60) FROM windows w JOIN users u USING(user_id) WHERE {REAL}""")[0], \
        one(f"""SELECT ROUND(EXTRACT(EPOCH FROM (now()-MAX(e.received_at)))/60) FROM events e JOIN users u USING(user_id) WHERE {REAL}""")[0], \
        one(f"""SELECT ROUND(EXTRACT(EPOCH FROM (now()-MAX(s.received_at)))/60) FROM self_reports s JOIN users u USING(user_id) WHERE {REAL}""")[0]
    print(f"   마지막 수신: 창 {lw}분 전 · 이벤트 {le}분 전 · 라벨 {lr}분 전")

block("0 데이터 유입 확인", b0)

# ───────────────────────── S1
def s1():
    users = one(f"SELECT COUNT(*) FROM users u WHERE {REAL}")[0]
    act = f"""(SELECT w.user_id, (w.window_start {K})::date d FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.window_start >= {S}
              UNION SELECT e.user_id, (e.occurred_at {K})::date FROM events e JOIN users u USING(user_id) WHERE {REAL} AND e.occurred_at >= {S}
                AND e.event NOT IN ('app_health')) a"""
    daily = q(f"SELECT d, COUNT(DISTINCT user_id) FROM {act} GROUP BY d ORDER BY d")
    print("   일별 활성 사용자(창 또는 이벤트 1건 이상):")
    print("     " + "  ".join(f"{d:%m/%d}({'월화수목금토일'[d.weekday()]})={n}" for d, n in daily))
    per = q(f"SELECT user_id, MIN(d), MAX(d), COUNT(DISTINCT d) FROM {act} GROUP BY user_id")
    ever = len(per)
    print(f"   한 번이라도 활성: {pct(ever, users)}  (나머지는 등록만 하고 데이터 없음)")
    today = now_kst.date()
    def weekdays(a, b):
        return sum(1 for i in range((b - a).days + 1) if (a + timedelta(i)).weekday() < 5)
    ratios = []
    for _, first, last, nd in per:
        poss = weekdays(first, today)
        ratios.append((nd, poss))
    tot_d = sum(r[0] for r in ratios); tot_p = sum(r[1] for r in ratios)
    half = sum(1 for nd, p in ratios if p and nd / p >= 0.5)
    print(f"   활성 일수 합 / 첫 접속 이후 평일 합 = {pct(tot_d, tot_p)}   평일 50% 이상 켠 사용자 {pct(half, ever)}")
    for days in (3, 7):
        n = one(f"""SELECT COUNT(*) FROM users u WHERE {REAL} AND COALESCE(u.last_seen_at, u.created_at) < now() - interval '{days} days'""")[0]
        print(f"   최근 {days}일 무접속(이탈 추정): {pct(n, users)}")
    cohort = [(first, last) for _, first, last, _ in per if (today - first).days >= 7]
    kept = sum(1 for first, last in cohort if (last - first).days >= 7)
    print(f"   D-7 잔존(첫 접속 7일 지난 사용자 중 7일째 이후에도 활성): {pct(kept, len(cohort))}   ※ D-14 는 10/28 이후 산출 가능")
    st = dict(q(f"SELECT e.event, COUNT(*) {EV} AND e.event IN ('app_start','app_quit','heartbeat') GROUP BY 1"))
    print(f"   app_start {st.get('app_start',0)} · app_quit {st.get('app_quit',0)} · heartbeat {st.get('heartbeat',0)} (5분 간격 → 약 {st.get('heartbeat',0)*5//60}시간 켜둠)")
    print("   한계: 삭제/언인스톨 이벤트 없음 → 무접속으로 대체. 창은 08:30~18:30 만 수집되므로 야간 사용은 이벤트로만 잡힘")

block("S1 서비스 지속 이용률 (이탈률)", s1)

# ───────────────────────── S2
def s2():
    T = "COALESCE(e.props->>'type', e.props->>'kind')"
    rows = q(f"""SELECT {T} typ, e.props->>'entry' entry,
                  COUNT(*) FILTER (WHERE e.event='relief_selected') sel,
                  COUNT(*) FILTER (WHERE e.event='relief_completed') done,
                  COUNT(*) FILTER (WHERE e.event='relief_abandoned') aband,
                  ROUND(AVG((e.props->>'dur_ms')::numeric) FILTER (WHERE e.event='relief_completed')/1000) dur_s,
                  COUNT(DISTINCT e.user_id) FILTER (WHERE e.event='relief_selected') users
                 {EV} AND e.event LIKE 'relief_%' GROUP BY 1,2 ORDER BY 1,2""")
    show("완화 종류별 (entry: stress_prompt=팝업에서, mind_tab=마음탭에서, 완료 이벤트는 entry 없음)",
         [(t, en, s, d, a, f1(ds), u) for t, en, s, d, a, ds, u in rows], ["종류", "entry", "선택", "완료", "중단", "평균초", "사용자"])
    tot = q(f"""SELECT {T}, COUNT(*) FILTER (WHERE e.event='relief_selected'), COUNT(*) FILTER (WHERE e.event='relief_completed') {EV} AND e.event LIKE 'relief_%' GROUP BY 1 ORDER BY 1""")
    print("   완료율(완료/선택): " + " · ".join(f"{t}={pct(d, s)}" for t, s, d in tot))
    pair = q(f"""
      WITH s AS (SELECT e.user_id, e.occurred_at t, {T} typ {EV} AND e.event='relief_selected'),
      p AS (SELECT typ,
              (SELECT AVG(w.condition_score) FROM windows w WHERE w.user_id=s.user_id AND w.window_start >= s.t - interval '10 min' AND w.window_start < s.t) b,
              (SELECT AVG(w.condition_score) FROM windows w WHERE w.user_id=s.user_id AND w.window_start > s.t + interval '2 min' AND w.window_start <= s.t + interval '12 min') a
            FROM s)
      SELECT typ, COUNT(*) n, COUNT(*) FILTER (WHERE a IS NOT NULL AND b IS NOT NULL) paired,
             ROUND(AVG(b) FILTER (WHERE a IS NOT NULL AND b IS NOT NULL),1), ROUND(AVG(a) FILTER (WHERE a IS NOT NULL AND b IS NOT NULL),1),
             COUNT(*) FILTER (WHERE a < b) down, COUNT(*) FILTER (WHERE a > b) up
      FROM p GROUP BY typ ORDER BY typ""")
    show("완화 전후 모델 점수 (선택 직전 10분 창 평균 → 선택 후 2~12분 창 평균, condition_score 0~100 높을수록 과부하)",
         [(t, n, p, f1(b), f1(a), f"{pct(d, p)}", u) for t, n, p, b, a, d, u in pair], ["종류", "선택", "전후쌍", "전", "후", "감소", "증가"])
    sr = q(f"""SELECT s.trigger, s.status, COUNT(*), SUM((s.yesno=1)::int) yes, SUM((s.yesno=0)::int) no, COUNT(s.level_1to5) lv, ROUND(AVG(s.level_1to5),2)
               FROM self_reports s JOIN users u USING(user_id) WHERE {REAL} AND s.answered_at >= {S} GROUP BY 1,2 ORDER BY 1,2""")
    show("자가보고 (팝업 '지금 스트레스 받나요?' 예/아니오, 1~5)", [(t, st, n, y, no, lv, f1(av)) for t, st, n, y, no, lv, av in sr], ["trigger", "status", "건", "예", "아니오", "1~5응답", "평균"])
    lv = q(f"SELECT s.level_1to5, COUNT(*) FROM self_reports s JOIN users u USING(user_id) WHERE {REAL} AND s.answered_at >= {S} AND s.level_1to5 IS NOT NULL GROUP BY 1 ORDER BY 1")
    print("   1~5 분포: " + (" · ".join(f"{l}={n}" for l, n in lv) if lv else "(없음)"))
    sp = dict(q(f"SELECT e.event, COUNT(*) {EV} AND e.event IN ('stress_prompt_shown','stress_prompt_answered') GROUP BY 1"))
    ans = q(f"SELECT e.props->>'answer', COUNT(*) {EV} AND e.event='stress_prompt_answered' GROUP BY 1 ORDER BY 1")
    print(f"   팝업 노출 {sp.get('stress_prompt_shown',0)} → 응답 {pct(sp.get('stress_prompt_answered',0), sp.get('stress_prompt_shown',0))}  답: " + " · ".join(f"{a}={n}" for a, n in ans))
    yes_relief = one(f"""SELECT COUNT(*) FILTER (WHERE e.event='relief_selected' AND e.props->>'entry'='stress_prompt'), COUNT(*) FILTER (WHERE e.event='stress_prompt_answered' AND e.props->>'answer'='yes') {EV}""")
    print(f"   '예' 응답 후 완화 선택(개입 실행율): {pct(yes_relief[0], yes_relief[1])}")
    print("   한계: 완화 후 자가보고 문항 없음 → 전후 비교는 모델 점수만. 팝업을 안 누르면 아무 기록도 안 남음(무시 수 측정 불가)")

block("S2 완화 요소 전후 스트레스", s2)

# ───────────────────────── S3
def s3():
    tot, work, wd = one(f"""SELECT COUNT(*), COUNT(*) FILTER (WHERE {WORK}), COUNT(*) FILTER (WHERE EXTRACT(ISODOW FROM e.occurred_at {K}) < 6) {EV} AND e.event='app_quit'""")
    print(f"   app_quit 전체 {tot} · 평일 {wd} · 업무시간(평일 08:30~18:30) {pct(work, tot)}")
    src = q(f"SELECT e.props->>'src', COUNT(*) {EV} AND e.event='app_quit' GROUP BY 1")
    print("   src 분포: " + " · ".join(f"{s}={n}" for s, n in src) + "   (앱이 'menu' 고정 → 트레이/메뉴 구분 불가)")
    ud = one(f"""SELECT COUNT(DISTINCT (e.user_id, (e.occurred_at {K})::date)) {EV} AND e.event='heartbeat' AND {WORK}""")[0]
    print(f"   업무시간 종료 횟수 / 업무시간 활성 사용자·일 = {pct(work, ud)}  (사용자가 하루 평균 몇 번 껐나)")
    hrs = q(f"SELECT EXTRACT(HOUR FROM e.occurred_at {K})::int h, COUNT(*) {EV} AND e.event='app_quit' GROUP BY 1 ORDER BY 1")
    print("   종료 시각대: " + " · ".join(f"{h}시={n}" for h, n in hrs))
    users = one(f"SELECT COUNT(*) FROM users u WHERE {REAL}")[0]
    dnd = one(f"""SELECT COUNT(*) FILTER (WHERE (e.props->>'on')::boolean), COUNT(*) FILTER (WHERE NOT (e.props->>'on')::boolean), COUNT(DISTINCT e.user_id) {EV} AND e.event='dnd_toggle'""")
    print(f"   방해금지 수동 토글: 켬 {dnd[0]} · 끔 {dnd[1]} · 사용 사용자 {pct(dnd[2], users)}")
    dh = q(f"SELECT EXTRACT(HOUR FROM e.occurred_at {K})::int, COUNT(*) {EV} AND e.event='dnd_toggle' AND (e.props->>'on')::boolean GROUP BY 1 ORDER BY 1")
    print("   켠 시각대: " + (" · ".join(f"{h}시={n}" for h, n in dh) if dh else "(없음)"))
    print("   한계: 자동 방해금지(18:30~08:30)는 이벤트 없음. 수동 방해금지 중에도 수집은 계속됨(windows.dnd 항상 false)")

block("S3 펫 위젯이 업무를 방해하지 않음", s3)

# ───────────────────────── S4
def s4():
    users = one(f"SELECT COUNT(*) FROM users u WHERE {REAL}")[0]
    rows = q(f"""SELECT e.props->>'kind' kind,
                  COUNT(*) FILTER (WHERE e.event='alarm_shown') shown,
                  COUNT(*) FILTER (WHERE e.event='alarm_answered' AND e.props->>'answer'='confirm') confirm,
                  COUNT(*) FILTER (WHERE e.event='alarm_answered' AND e.props->>'answer'='later') later,
                  COUNT(*) FILTER (WHERE e.event='alarm_answered' AND e.props->>'answer'='timeout') timeout,
                  COUNT(DISTINCT e.user_id) FILTER (WHERE e.event='alarm_shown') u_shown,
                  COUNT(DISTINCT e.user_id) FILTER (WHERE e.event='alarm_answered' AND e.props->>'answer'='confirm') u_confirm
                 {EV} AND e.event IN ('alarm_shown','alarm_answered') GROUP BY 1 ORDER BY 1""")
    show("알람 종류별 (med=복약, water=물, stretch=스트레칭, rest=휴식)",
         [(k, s, pct(cf, s), pct(l, s), pct(t, s), f"{us}/{users}", f"{uc}/{users}") for k, s, cf, l, t, us, uc in rows],
         ["kind", "노출", "확인", "나중에", "무응답", "노출사용자", "확인사용자"])
    T = "COALESCE(e.props->>'type', e.props->>'kind')"
    feat = q(f"""SELECT x.name, COUNT(DISTINCT x.user_id), COUNT(*) FROM (
        SELECT '완화-심호흡' name, e.user_id {EV} AND e.event='relief_selected' AND {T}='breath'
        UNION ALL SELECT '완화-명상', e.user_id {EV} AND e.event='relief_selected' AND {T}='meditate'
        UNION ALL SELECT '완화-공감챗(팝업경유)', e.user_id {EV} AND e.event='relief_selected' AND {T}='chat'
        UNION ALL SELECT '공감챗 메시지', e.user_id {EV} AND e.event='chat_message'
        UNION ALL SELECT '복약알림 확인', e.user_id {EV} AND e.event='alarm_answered' AND e.props->>'kind'='med' AND e.props->>'answer'='confirm'
        UNION ALL SELECT '물알림 확인', e.user_id {EV} AND e.event='alarm_answered' AND e.props->>'kind'='water' AND e.props->>'answer'='confirm'
        UNION ALL SELECT '기분 체크', e.user_id {EV} AND e.event='mood_checked'
        UNION ALL SELECT '펫 돌보기(care_action)', e.user_id {EV} AND e.event='care_action'
      ) x GROUP BY 1 ORDER BY 2 DESC, 3 DESC""")
    show(f"기능별 사용 사용자 비율 (전체 {users}명)", [(n, pct(u, users), cnt) for n, u, cnt in feat], ["기능", "사용 사용자", "건수"])
    ch = one(f"""SELECT COUNT(*), COUNT(DISTINCT e.user_id), COUNT(*) FILTER (WHERE e.props->>'source'='bedrock'), COUNT(*) FILTER (WHERE e.props->>'source'='rule'),
                 COUNT(*) FILTER (WHERE e.props->>'source'='filter'), ROUND(AVG((e.props->>'len')::numeric),1) {EV} AND e.event='chat_message'""")
    print(f"   공감챗: 메시지 {ch[0]} · 사용자 {ch[1]} · 사용자당 {f1(ch[0]/ch[1] if ch[1] else None)}건 · source bedrock {ch[2]} / rule {ch[3]} / filter {ch[4]} · 평균 길이 {f1(ch[5])}자")
    ca = q(f"SELECT e.props->>'type', COUNT(*) {EV} AND e.event='care_action' GROUP BY 1 ORDER BY 2 DESC")
    print("   care_action: " + (" · ".join(f"{t}={n}" for t, n in ca) if ca else "(없음)"))
    print("   한계: 복약 등록·미니홈 수동 복약 체크·물 목표 변경은 이벤트 없음 → 알림 응답으로만 니즈 측정")

block("S4 완화·복약알림·음수량알림 니즈", s4)

# ───────────────────────── B1
def b1():
    rows = q(f"""
      WITH x AS (SELECT e.user_id, e.event, e.occurred_at, LAG(e.event) OVER (PARTITION BY e.user_id ORDER BY e.occurred_at) prev
                 {EV} AND e.event IN ('app_start','app_quit'))
      SELECT COUNT(*) FILTER (WHERE event='app_start' AND prev IS NOT NULL) starts,
             COUNT(*) FILTER (WHERE event='app_start' AND prev='app_start') abnormal,
             COUNT(DISTINCT user_id) FILTER (WHERE event='app_start' AND prev='app_start') users_abn
      FROM x""")
    starts, abn, uabn = rows[0] if rows else (0, 0, 0)
    users = one(f"SELECT COUNT(*) FROM users u WHERE {REAL}")[0]
    print(f"   app_quit 없이 다시 app_start (강제종료·PC 꺼짐 추정): {pct(abn, starts)}  (첫 실행 제외)  해당 사용자 {pct(uabn, users)}")
    gap = one(f"""
      WITH h AS (SELECT e.user_id, e.occurred_at t, LAG(e.occurred_at) OVER (PARTITION BY e.user_id ORDER BY e.occurred_at) p
                 {EV} AND e.event='heartbeat')
      SELECT COUNT(*), COUNT(*) FILTER (WHERE t - p > interval '10 min'),
             COUNT(*) FILTER (WHERE t - p > interval '10 min' AND NOT EXISTS (SELECT 1 FROM events e2 WHERE e2.user_id=h.user_id AND e2.event='app_quit' AND e2.occurred_at BETWEEN h.p AND h.t))
      FROM h WHERE p IS NOT NULL""")
    print(f"   heartbeat(5분) 간격 10분 초과 끊김: {pct(gap[1], gap[0])} · 그중 사이에 app_quit 없음(비정상 중단·슬립 추정) {gap[2]}")
    ah = q(f"""SELECT e.props->>'crash', e.props->>'events_dropped', COUNT(*), ROUND(AVG((e.props->>'mem')::numeric)), MAX((e.props->>'mem')::numeric) {EV} AND e.event='app_health' GROUP BY 1,2""")
    show("app_health props", [(cr, ed, n, m, mx) for cr, ed, n, m, mx in ah], ["crash", "events_dropped", "건", "mem평균", "mem최대"])
    print("   한계: 앱이 crash=false·events_dropped=0 고정 전송, app_start 에 이전 세션 정상종료 플래그 없음 → 위 비율은 대리 지표")

block("B1 강제 종료 (종료 버튼 없이 꺼진 경우)", b1)

# ───────────────────────── B2
def b2():
    lat = one(f"""
      WITH d AS (SELECT EXTRACT(EPOCH FROM (w.received_at - w.window_start)) - 60 s FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.window_start >= {S})
      SELECT COUNT(*), COUNT(*) FILTER (WHERE s < 600),
             ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY s) FILTER (WHERE s < 600))::numeric,1),
             ROUND((percentile_cont(0.95) WITHIN GROUP (ORDER BY s) FILTER (WHERE s < 600))::numeric,1),
             ROUND((MAX(s) FILTER (WHERE s < 600))::numeric,1),
             COUNT(*) FILTER (WHERE s >= 600), COUNT(*) FILTER (WHERE s < -60)
      FROM d""")
    print(f"   창 전송 지연(서버 수신 − 창 시작 − 60초): 창 {lat[0]} · 정상범위 {pct(lat[1], lat[0])} · p50 {lat[2]}초 · p95 {lat[3]}초 · 최대 {lat[4]}초 · 10분 이상 지연 {lat[5]} · 앱 시계 앞섬 {lat[6]}")
    exp = one(f"""
      SELECT (SELECT COUNT(*) FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.window_start >= {S}
                AND EXTRACT(ISODOW FROM w.window_start {K}) < 6 AND (w.window_start {K})::time BETWEEN '08:30' AND '18:30'),
             (SELECT COUNT(*)*5 {EV} AND e.event='heartbeat' AND {WORK})""")
    loss = max(0, exp[1] - exp[0])
    print(f"   결손률① heartbeat 기준: 업무시간 창 {exp[0]} / 켜둔 분 {exp[1]} → 결손 {pct(loss, exp[1])}  (10% 미만이면 B2 통과)")
    ses = one(f"""
      WITH s AS (SELECT s.session_id, FLOOR(EXTRACT(EPOCH FROM (s.ended_at - s.started_at))/60)+1 exp_min,
                        (SELECT COUNT(*) FROM windows w WHERE w.session_id=s.session_id) got
                 FROM sessions s JOIN users u USING(user_id) WHERE {REAL} AND s.started_at >= {S} AND s.ended_at IS NOT NULL)
      SELECT COUNT(*), SUM(exp_min), SUM(got) FROM s""")
    print(f"   결손률② 세션 구간 기준: 세션 {ses[0]} · 첫~마지막 창 사이 분 {int(ses[1] or 0)} · 실제 창 {int(ses[2] or 0)} → 결손 {pct(int((ses[1] or 0)-(ses[2] or 0)), int(ses[1] or 0))}  (슬립·잠금 구간 포함이라 ①보다 크게 나옴)")
    pu = q(f"""SELECT u.cp_number, COUNT(w.window_id) got, (SELECT COUNT(*)*5 FROM events e WHERE e.user_id=u.user_id AND e.event='heartbeat' AND e.occurred_at >= {S} AND {WORK}) hb
               FROM users u LEFT JOIN windows w ON w.user_id=u.user_id AND w.window_start >= {S} AND EXTRACT(ISODOW FROM w.window_start {K}) < 6 AND (w.window_start {K})::time BETWEEN '08:30' AND '18:30'
               WHERE {REAL} GROUP BY u.user_id ORDER BY hb DESC""")
    bad = [(cp, g, h, pct(max(0, h - g), h)) for cp, g, h in pu if h and (h - g) / h >= 0.1]
    show("사용자별 결손 10% 이상 (heartbeat 기준)", bad[:10], ["회원번호", "창", "켜둔분", "결손"])
    print(f"   사용자 {len(pu)}명 중 결손 10% 이상 {pct(len(bad), len([1 for _, _, h in pu if h]))} (heartbeat 있는 사용자 기준)")
    mf = one(f"""SELECT COUNT(*), COUNT(*) FILTER (WHERE w.model_version='unavailable'), COUNT(*) FILTER (WHERE w.condition_score IS NULL AND NOT w.activity_zero)
                 FROM windows w JOIN users u USING(user_id) WHERE {REAL} AND w.window_start >= {S}""")
    print(f"   모델 서버 실패(model_version=unavailable): {pct(mf[1], mf[0])} · 활동 있는데 점수 없음 {pct(mf[2], mf[0])}")
    j = one(f"""SELECT (SELECT COUNT(*) FROM windows w LEFT JOIN users u USING(user_id) WHERE u.user_id IS NULL),
                       (SELECT COUNT(*) FROM events e LEFT JOIN users u USING(user_id) WHERE u.user_id IS NULL),
                       (SELECT COUNT(*) FROM self_reports s LEFT JOIN users u USING(user_id) WHERE u.user_id IS NULL),
                       (SELECT COUNT(*) FROM windows w LEFT JOIN sessions s USING(session_id) WHERE s.session_id IS NULL),
                       (SELECT COUNT(*) FROM self_reports s JOIN users u USING(user_id) WHERE {REAL}),
                       (SELECT COUNT(*) FROM self_reports s JOIN users u USING(user_id) WHERE {REAL} AND s.window_id IS NULL),
                       (SELECT COUNT(*) FROM users u WHERE {REAL} AND u.cp_number NOT LIKE 'CP-%'),
                       (SELECT COUNT(*) FROM users u WHERE {REAL} AND u.token_hash IS NULL),
                       (SELECT COUNT(*) FROM users u WHERE {REAL} AND NOT EXISTS (SELECT 1 FROM windows w WHERE w.user_id=u.user_id) AND NOT EXISTS (SELECT 1 FROM events e WHERE e.user_id=u.user_id))""")
    print(f"   조인 실패: 창→사용자 {j[0]} · 이벤트→사용자 {j[1]} · 라벨→사용자 {j[2]} · 창→세션 {j[3]}   (전부 0 이면 조인 실패 0%)")
    print(f"   라벨 중 창 못 붙음(window_id null): {pct(j[5], j[4])} · 회원번호 형식 이상 {j[6]} · 토큰 없는 사용자(구버전 앱) {j[7]} · 등록만 하고 데이터 0 {j[8]}")
    print("   한계: 창은 전송 실패 시 재시도 없이 버려짐(앱) → 결손률①이 곧 네트워크/서버 유실률. CPU 는 app_health 에 없음(mem 만)")

block("B2 프로그램이 무겁지 않음 (지연·결손·조인)", b2)

print("\n끝.")
