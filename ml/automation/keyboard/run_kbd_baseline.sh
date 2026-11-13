#!/usr/bin/env bash
# 키보드 개인/전체 기준선 배치 — cron 새벽 1회 실행용
# crontab 예: 0 4 * * *  /home/ec2-user/carepong-automation/keyboard/run_kbd_baseline.sh
set -euo pipefail
cd "$(dirname "$0")"
# 환경변수(.env) 로드: DB_HOST/PORT/NAME/USER/PASSWORD
[ -f ../.env ] && set -a && . ../.env && set +a
exec python3 kbd_baseline.py >> /var/log/carepong/kbd_baseline.log 2>&1
