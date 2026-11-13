#!/usr/bin/env bash
# (선택) HRV 개인 기준선 모니터링 배치 — 하루 1회. 판정 자체는 실시간이라 필수 아님.
# crontab 예: 30 4 * * *  /home/ec2-user/carepong-automation/hrv/run_hrv_baseline.sh
set -euo pipefail
cd "$(dirname "$0")"
[ -f ../.env ] && set -a && . ../.env && set +a
exec python3 build_hrv_baselines.py >> /var/log/carepong/hrv_baseline.log 2>&1
