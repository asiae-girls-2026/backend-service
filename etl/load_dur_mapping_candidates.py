import csv
import json
import subprocess
from pathlib import Path

BASE = Path("/home/ubuntu/backend-service/etl/reports/ddi")
SOURCE = BASE / "dur_full_med_mapping_candidates.csv"

with SOURCE.open(encoding="utf-8-sig", newline="") as f:
    records = list(csv.DictReader(f))

if len(records) != 217:
    raise SystemExit(
        f"DUR 코드 수 불일치: {len(records)}/217"
    )

rows = []
seen = set()
covered = set()
unresolved = []

for record in records:
    d_code = record["dur_code"].strip()
    status = record["status"]

    m_codes = [
        x.strip()
        for x in record["matched_m_codes"].split("|")
        if x.strip()
    ]

    if not m_codes:
        unresolved.append(d_code)
        continue

    covered.add(d_code)

    for m_code in m_codes:
        key = (d_code, m_code)

        if key in seen:
            continue

        seen.add(key)

        rows.append({
            "dur_code": d_code,
            "mtral_code": m_code,
            "match_method": "EXACT_SOURCE_NAME",
            "evidence_json": {
                "analysis": "dur_full_med_mapping_candidates",
                "candidate_status": status,
                "dur_names": record["dur_names"],
                "source_datasets": [
                    "MFDS_DUR",
                    "MFDS_PERMIT"
                ],
                "note": (
                    "이름 및 원천 코드 기반 후보. "
                    "화학적·임상적 동일성은 미검증."
                )
            }
        })

if len(covered) != 207 or len(unresolved) != 10:
    raise SystemExit(
        f"후보 범위 불일치: "
        f"연결={len(covered)}, 미해결={len(unresolved)}"
    )

print("후보 연결 DUR 코드:", len(covered), flush=True)
print("D-M 개별 후보 관계:", len(rows), flush=True)
print("미해결 DUR 코드:", len(unresolved), flush=True)

db_code = r'''
import os
import sys
import json
import psycopg

rows = json.load(sys.stdin)

sql = """
WITH input AS (
    SELECT *
    FROM jsonb_to_recordset(%s::jsonb)
    AS x(
        dur_code text,
        mtral_code text,
        match_method text,
        evidence_json jsonb
    )
),
inserted AS (
    INSERT INTO public.med_dur_code_mapping (
        dur_code,
        mtral_code,
        match_method,
        evidence_json,
        review_status
    )
    SELECT
        dur_code,
        mtral_code,
        match_method,
        evidence_json,
        'PENDING'
    FROM input
    WHERE true
    ON CONFLICT (dur_code, mtral_code)
    DO NOTHING
    RETURNING 1
)
SELECT count(*) FROM inserted
"""

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    conn.execute("SET lock_timeout = '5s'")
    conn.execute("SET statement_timeout = '60s'")

    inserted = conn.execute(
        sql,
        (json.dumps(rows, ensure_ascii=False),)
    ).fetchone()[0]

    summary = conn.execute("""
        SELECT
            count(*),
            count(DISTINCT dur_code),
            count(*) FILTER (
                WHERE review_status = 'PENDING'
            )
        FROM public.med_dur_code_mapping
    """).fetchone()

print("이번 신규 INSERT:", inserted)
print("전체 D-M 관계:", summary[0])
print("연결된 DUR 코드:", summary[1])
print("검토 대기 관계:", summary[2])
'''

result = subprocess.run(
    ["docker", "exec", "-i",
     "infra-api-1", "python", "-c", db_code],
    input=json.dumps(rows, ensure_ascii=False),
    text=True,
    capture_output=True,
    check=True,
)

print(result.stdout)
