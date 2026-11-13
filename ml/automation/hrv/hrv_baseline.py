# -*- coding: utf-8 -*-
"""
hrv_baseline.py — 유저 개인 안정(calm) HRV 기준선 조회 (서버가 judge_stress에 넘길 재료)
===========================================================================================
[왜 필요한가]
현재 server_rds.py 는 personal_calm_hrv 를 "요청(폰)"에서만 받음(line 199). 폰은 유저 과거
측정 이력을 모르므로 실제로는 늘 None → judge_stress 가 coldstart(인구 평균/표준편차)로만 동작
= 사실상 개인화 안 됨("절대값"처럼 보이는 이유).

[이 모듈이 하는 일]
RDS `ppg_measure` 에서 그 유저의 "안정 상태(calm)" 측정들을 모아
judge_stress 가 바로 쓰는 [{mean_hr, rmssd, sdnn, pnn50}, ...] 리스트로 돌려줌.
→ 유저가 calm 측정을 min_n(기본 5)개 이상 쌓이면 judge_stress 가 자동으로
   coldstart → personal 로 승격. 버전업·재배포 없이 데이터가 쌓일수록 개인화됨.

[calm(안정) 정의]
status='ok' 이고 grade='저'(저부하=차분) 인 측정. 아침(trigger='morning') 측정도
차분하면 자연히 grade='저'로 들어옴. 고/관찰은 기준선에서 제외(스트레스 상태라 평소가 아님).

[서버 연결 — server_rds.py line 199 `calm = data.get("personal_calm_hrv")` 바로 아래]
    calm = data.get("personal_calm_hrv")
    if not calm:
        try:
            from hrv_baseline import get_personal_calm_hrv
            calm = get_personal_calm_hrv(user_id)   # DB에서 개인 안정 기준선
        except Exception:
            log.exception("개인 기준선 조회 실패 id=%s", user_id); calm = None
    # (이번 측정은 아직 DB에 없으므로 자기 자신이 섞일 일 없음)
    ...
    j = judge_stress(h, MODEL_FILE, personal_calm_hrv=calm)

환경변수(DB_HOST/PORT/NAME/USER/PASSWORD)는 server_rds.py 와 동일하게 사용.
"""
import os
import logging

log = logging.getLogger("hrv_baseline")

# judge_stress 가 쓰는 v5 모델 피처 4개 (순서 무관, 키만 맞으면 됨)
CALM_FEATURES = ["mean_hr", "rmssd", "sdnn", "pnn50"]

# 기준선으로 쓸 최근 calm 측정 최대 개수(며칠치 추이). 너무 오래된 것까지는 안 씀.
DEFAULT_LIMIT = 60
# judge_stress 의 min_personal_n 과 맞춤(이 값 미만이면 서버단에서 그냥 None 반환 → coldstart)
DEFAULT_MIN_N = 5


def _db_connect():
    host = os.environ.get("DB_HOST", "").strip()
    if not host:
        return None
    import psycopg2
    return psycopg2.connect(
        host=host,
        port=int(os.environ.get("DB_PORT", "5432") or 5432),
        dbname=os.environ.get("DB_NAME", "").strip(),
        user=os.environ.get("DB_USER", "").strip(),
        password=os.environ.get("DB_PASSWORD", "").strip(),
        connect_timeout=5,
    )


def get_personal_calm_hrv(user_id, conn=None, exclude_id=None,
                          limit=DEFAULT_LIMIT, min_n=DEFAULT_MIN_N):
    """
    그 유저의 안정(calm) HRV 측정들을 judge_stress 입력 형태로 반환.
    반환: [{mean_hr, rmssd, sdnn, pnn50}, ...]  (min_n 미만이면 [] → 서버는 coldstart)
    - conn: 열린 psycopg2 커넥션을 넘기면 재사용(매 요청 새 연결 피함). 없으면 자체 연결.
    - exclude_id: 이번 측정 id는 기준선에서 제외(자기 자신 섞임 방지).
    """
    own = False
    if conn is None:
        conn = _db_connect()
        own = True
    if conn is None:
        log.warning("DB_HOST 미설정 — 개인 기준선 조회 건너뜀(coldstart)")
        return []
    cols = ", ".join(f'"{f}"' for f in CALM_FEATURES)
    sql = (
        f"SELECT {cols} FROM ppg_measure "
        "WHERE user_id = %s AND status = 'ok' AND grade = '저' "
        + ("AND id <> %s " if exclude_id else "")
        + "AND mean_hr IS NOT NULL AND rmssd IS NOT NULL "
          "AND sdnn IS NOT NULL AND pnn50 IS NOT NULL "
        "ORDER BY timestamp DESC LIMIT %s"
    )
    params = [user_id] + ([exclude_id] if exclude_id else []) + [limit]
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception:
        log.exception("개인 기준선 조회 실패 user_id=%s", user_id)
        return []
    finally:
        if own:
            conn.close()
    calm = [dict(zip(CALM_FEATURES, [float(v) for v in r])) for r in rows]
    if len(calm) < min_n:
        return []          # 아직 안정 표본 부족 → coldstart(인구 평균)로
    return calm


if __name__ == "__main__":
    import sys
    uid = sys.argv[1] if len(sys.argv) > 1 else "CP-33948A80"
    c = get_personal_calm_hrv(uid)
    print(f"{uid}: calm 측정 {len(c)}개 → {'personal' if c else 'coldstart'}")
    if c:
        print("최근 1건:", c[0])
