# -*- coding: utf-8 -*-
"""
build_hrv_baselines.py — 개인 HRV 안정 기준선 배치 (주기 실행용)
================================================================
[무엇]
RDS `ppg_measure` 를 훑어 유저별 "안정(calm) HRV 기준선"을 계산해
캐시 테이블 `ppg_personal_baseline` 에 upsert 하고, 개인화 승격 현황을 출력.

[왜]
- 서버 실시간 판정은 hrv_baseline.get_personal_calm_hrv() 가 매 요청 DB에서 직접 조회하므로
  이 배치 없이도 개인화는 "자동으로" 됨(calm 측정 ≥ min_n 쌓이면 coldstart→personal 승격).
- 이 배치는 (1) "누가 personal로 승격됐나" 모니터링, (2) 원하면 서버가 매 요청 재계산 대신
  이 캐시 테이블을 읽게 하는 빠른 경로(선택), (3) 2차 분석용 스냅샷 용도.
- 데이터가 쌓일수록 n_calm 이 커지고 자동으로 personal 비중↑ → 버전업·재배포 없이 개인화 진행.

[스키마]
  CREATE TABLE IF NOT EXISTS ppg_personal_baseline (
    user_id        varchar(64) PRIMARY KEY,
    n_calm         integer,
    mean_hr_mu real, mean_hr_sd real,
    rmssd_mu   real, rmssd_sd   real,
    sdnn_mu    real, sdnn_sd    real,
    pnn50_mu   real, pnn50_sd   real,
    used_reference varchar(12),     -- personal | coldstart
    updated_at     timestamptz
  );

[실행]  (환경변수 DB_HOST/PORT/NAME/USER/PASSWORD 는 server_rds.py 와 동일)
  python build_hrv_baselines.py

[주기 자동 실행]
  · EC2(cron) — 매일 새벽 4시:
      0 4 * * *  cd /home/ec2-user/carepong && /usr/bin/python3 build_hrv_baselines.py >> baseline.log 2>&1
  · Windows(작업 스케줄러) — 함께 둔 run_hrv_baseline.bat 를 매일 1회로 등록.
"""
import os
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("build_hrv_baselines")

FEATURES = ["mean_hr", "rmssd", "sdnn", "pnn50"]
MIN_N = 5   # hrv_baseline / judge_stress 의 min_personal_n 과 동일

DDL = """
CREATE TABLE IF NOT EXISTS ppg_personal_baseline (
  user_id        varchar(64) PRIMARY KEY,
  n_calm         integer,
  mean_hr_mu real, mean_hr_sd real,
  rmssd_mu   real, rmssd_sd   real,
  sdnn_mu    real, sdnn_sd    real,
  pnn50_mu   real, pnn50_sd   real,
  used_reference varchar(12),
  updated_at     timestamptz
);
"""

UPSERT = """
INSERT INTO ppg_personal_baseline
  (user_id, n_calm, mean_hr_mu, mean_hr_sd, rmssd_mu, rmssd_sd,
   sdnn_mu, sdnn_sd, pnn50_mu, pnn50_sd, used_reference, updated_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (user_id) DO UPDATE SET
  n_calm=EXCLUDED.n_calm,
  mean_hr_mu=EXCLUDED.mean_hr_mu, mean_hr_sd=EXCLUDED.mean_hr_sd,
  rmssd_mu=EXCLUDED.rmssd_mu, rmssd_sd=EXCLUDED.rmssd_sd,
  sdnn_mu=EXCLUDED.sdnn_mu, sdnn_sd=EXCLUDED.sdnn_sd,
  pnn50_mu=EXCLUDED.pnn50_mu, pnn50_sd=EXCLUDED.pnn50_sd,
  used_reference=EXCLUDED.used_reference, updated_at=EXCLUDED.updated_at;
"""


def _db_connect():
    host = os.environ.get("DB_HOST", "").strip()
    if not host:
        raise SystemExit("DB_HOST 미설정 — .env 또는 환경변수 확인")
    import psycopg2
    return psycopg2.connect(
        host=host, port=int(os.environ.get("DB_PORT", "5432") or 5432),
        dbname=os.environ.get("DB_NAME", "").strip(),
        user=os.environ.get("DB_USER", "").strip(),
        password=os.environ.get("DB_PASSWORD", "").strip(),
        connect_timeout=10,
    )


def main():
    import numpy as np
    conn = _db_connect()
    cols = ", ".join(f'"{f}"' for f in FEATURES)
    # calm = status ok + grade 저 (차분한 측정만), 유저별로 모음
    sql = (
        f"SELECT user_id, {cols} FROM ppg_measure "
        "WHERE status='ok' AND grade='저' "
        "AND mean_hr IS NOT NULL AND rmssd IS NOT NULL "
        "AND sdnn IS NOT NULL AND pnn50 IS NOT NULL"
    )
    with conn, conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute(sql)
        rows = cur.fetchall()

    # 유저별 집계
    from collections import defaultdict
    by_user = defaultdict(list)
    for r in rows:
        by_user[r[0]].append([float(v) for v in r[1:]])

    now = datetime.now(timezone.utc)
    upserts, n_personal, n_coldstart = [], 0, 0
    for uid, vals in by_user.items():
        arr = np.array(vals, dtype=float)
        n = len(arr)
        mu = arr.mean(0); sd = arr.std(0)
        used = "personal" if n >= MIN_N else "coldstart"
        if used == "personal": n_personal += 1
        else: n_coldstart += 1
        upserts.append((uid, n,
                        float(mu[0]), float(sd[0]), float(mu[1]), float(sd[1]),
                        float(mu[2]), float(sd[2]), float(mu[3]), float(sd[3]),
                        used, now))

    with conn, conn.cursor() as cur:
        cur.executemany(UPSERT, upserts)
    conn.close()

    log.info("기준선 갱신 완료: 유저 %d명 (personal %d / coldstart %d, min_n=%d)",
             len(upserts), n_personal, n_coldstart, MIN_N)
    log.info("personal 승격 유저 = calm 측정 %d개 이상 모인 사람. 데이터 쌓일수록 자동 증가.", MIN_N)


if __name__ == "__main__":
    main()
