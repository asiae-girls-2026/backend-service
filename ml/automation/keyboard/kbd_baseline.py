# -*- coding: utf-8 -*-
"""
kbd_baseline.py — 키보드(1차) 개인 기준선 배치 (RDS windows → 개인 평균/표준편차)
====================================================================================
[무엇] RDS `windows`(16피처, 1분 창)를 훑어 유저별 "평소" 평균·표준편차·n 을
       `kbd_personal_baseline` 에 upsert + 전체 공통(글로벌) 평균/표준편차를
       `kbd_global_seed` 에 저장. 키보드 서빙(kbd_personalize.py)이 이걸 읽어 개인화.

[언제] 평소값은 수천 창의 집계라 매 판정 재계산이 무거움 → **배치로 하루 1번**(새벽) 갱신.
       판정(적용)은 kbd_personalize 가 실시간으로 이 캐시를 읽어 변환.

[자동화] n 이 커질수록 kbd_personalize 의 w=n/(n+k) 가 개인 비중을 자동으로 올림
         → 버전업 없이 데이터 쌓일수록 개인화 정교화.

[환경변수] DB_HOST/PORT/NAME/USER/PASSWORD  (키보드 서버와 동일 RDS. 이름 다르면 맞출 것)

[주기 실행]
  · EC2(cron) 매일 새벽 4시:
      0 4 * * *  /usr/bin/python3 /home/ec2-user/kbd/kbd_baseline.py >> kbd_baseline.log 2>&1
  · Windows: 함께 둔 run_kbd_baseline.bat 를 작업 스케줄러 매일 1회로.
"""
import os, json, logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("kbd_baseline")

# 1차 키보드 16피처 (windows.features jsonb 키)
FEATS = ['SnKeyStrokes','SnChars','SnSpecialKeys','SnDirectionKeys','SnErrorKeys',
         'SnShortcutKeys','SnSpaces','CharactersRatio','SnMouseAct','SnLeftClicked',
         'SnRightClicked','SnDoubleClicked','SnWheel','SnDragged','SnMouseDistance','ErrorKeyRatio']
K = 300  # shrinkage k: 반반 지점. 하루 활동창 ~135개 기준 ~2일이면 반반, 2주면 86% 개인화.
         # (초반을 전체 공통에 붙잡아 얇은 데이터 노이즈 방지. 유저 더 모이면 경험적 베이즈로 재추정)

DDL = """
CREATE TABLE IF NOT EXISTS kbd_personal_baseline (
  user_id    varchar(64) PRIMARY KEY,
  n          integer,
  mean_json  jsonb,       -- {피처: 개인 평균}
  std_json   jsonb,       -- {피처: 개인 표준편차}
  updated_at timestamptz
);
CREATE TABLE IF NOT EXISTS kbd_global_seed (
  id         smallint PRIMARY KEY DEFAULT 1,
  mean_json  jsonb,       -- {피처: 전체 공통 평균}
  std_json   jsonb,       -- {피처: 전체 공통 표준편차}
  k          integer,
  updated_at timestamptz
);
"""

UPSERT_USER = """
INSERT INTO kbd_personal_baseline (user_id, n, mean_json, std_json, updated_at)
VALUES (%s,%s,%s,%s,%s)
ON CONFLICT (user_id) DO UPDATE SET
  n=EXCLUDED.n, mean_json=EXCLUDED.mean_json, std_json=EXCLUDED.std_json,
  updated_at=EXCLUDED.updated_at;
"""
UPSERT_GLOBAL = """
INSERT INTO kbd_global_seed (id, mean_json, std_json, k, updated_at)
VALUES (1,%s,%s,%s,%s)
ON CONFLICT (id) DO UPDATE SET
  mean_json=EXCLUDED.mean_json, std_json=EXCLUDED.std_json,
  k=EXCLUDED.k, updated_at=EXCLUDED.updated_at;
"""


def _db():
    host = os.environ.get("DB_HOST", "").strip()
    if not host:
        raise SystemExit("DB_HOST 미설정 — .env/환경변수 확인")
    import psycopg2
    return psycopg2.connect(host=host, port=int(os.environ.get("DB_PORT","5432") or 5432),
        dbname=os.environ.get("DB_NAME","").strip(), user=os.environ.get("DB_USER","").strip(),
        password=os.environ.get("DB_PASSWORD","").strip(), connect_timeout=10)


# ── 실사용자 필터 ──────────────────────────────────────────────────
# 자동화 테스트 제외 = 실제 규칙 windows.session_id LIKE 'ses_smoke_%' (SQL에서 거름).
# 품질이상(중복 과다) 계정은 user_id 프리픽스로 추가 제외.
QUALITY_EXCLUDE_PREFIX = ('2b03c992',)   # 중복 과다 계정(혜윤 학원PC) 등 품질 제외


def _expand(features):
    """windows.features(jsonb)가 dict/str 어느 쪽이든 16피처 숫자 리스트로."""
    if isinstance(features, str):
        features = json.loads(features)
    return [float(features.get(f)) if features.get(f) is not None else None for f in FEATS]


def main():
    import numpy as np
    conn = _db()
    with conn, conn.cursor() as cur:
        cur.execute(DDL)
        # 활동 창만(무활동 제외) + 자동화 세션(ses_smoke_) 제외. features jsonb 통째로 받아 펼침.
        cur.execute(
            "SELECT user_id, features FROM windows "
            "WHERE activity_zero = false AND session_id NOT LIKE 'ses_smoke_%'")
        rows = cur.fetchall()

    from collections import defaultdict
    by_user = defaultdict(list); allv = []
    skipped = 0
    for uid, feats in rows:
        if str(uid).startswith(QUALITY_EXCLUDE_PREFIX):       # 품질이상 계정 제외
            skipped += 1
            continue
        v = _expand(feats)
        if any(x is None for x in v):
            continue
        by_user[uid].append(v); allv.append(v)
    log.info("실사용자 %d명 집계 (비실사용 행 %d개 제외)", len(by_user), skipped)

    now = datetime.now(timezone.utc)
    # 글로벌(콜드스타트 기준값) = "1인1표": 사람별 평균을 먼저 낸 뒤 그걸 평균
    #  → 창 많은 heavy 유저 쏠림 방지, 신규 유저에 더 대표성 있는 출발점.
    user_means = np.array([np.array(v, dtype=float).mean(0) for v in by_user.values()])
    g_mean_arr = user_means.mean(0)                          # 사람별 평균의 평균(1인1표)
    g = np.array(allv, dtype=float)
    g_std_arr = np.where(g.std(0) == 0, 1.0, g.std(0))       # 스케일(표준편차)은 전체 창 기준 유지
    g_mean = dict(zip(FEATS, g_mean_arr.tolist()))
    g_std  = dict(zip(FEATS, g_std_arr.tolist()))

    ups = []
    for uid, vals in by_user.items():
        a = np.array(vals, dtype=float)
        ups.append((uid, len(a),
                    json.dumps(dict(zip(FEATS, a.mean(0).tolist()))),
                    json.dumps(dict(zip(FEATS, a.std(0).tolist()))), now))
    with conn, conn.cursor() as cur:
        cur.executemany(UPSERT_USER, ups)
        cur.execute(UPSERT_GLOBAL, (json.dumps(g_mean), json.dumps(g_std), K, now))
    conn.close()
    log.info("키보드 기준선 갱신: 유저 %d명, 전체 피처 %d. (n 커질수록 개인화 자동↑)", len(ups), len(FEATS))


if __name__ == "__main__":
    main()
