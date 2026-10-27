import csv
import gzip
import json
import re
import unicodedata
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path("/home/ubuntu/backend-service/etl")
RAW = ROOT / "data/med_ingredients"
OUT = ROOT / "reports/ddi"

def norm(value):
    value = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", value).casefold()

# 전체 허가정보 주성분 코드 사전 생성
med_names = defaultdict(set)
files = sorted(RAW.glob("page_*.json.gz"))

if len(files) != 252:
    raise SystemExit(f"원본 파일 수 불일치: {len(files)}/252")

total = 0

for path in files:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)

    body = data.get("response", data)["body"]
    items = body.get("items") or []

    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]

    for row in items:
        total += 1
        code = str(row.get("MTRAL_CODE") or "").strip()
        name = str(row.get("MTRAL_NM") or "").strip()

        if code and name:
            med_names[code].add(norm(name))

if total != 125692:
    raise SystemExit(f"원본 행 수 불일치: {total}")

# 기존 DUR 217개 후보 재분석
with (OUT / "dur_m_code_candidates.csv").open(
    encoding="utf-8-sig", newline=""
) as f:
    candidates = list(csv.DictReader(f))

if len(candidates) != 217:
    raise SystemExit("DUR 후보 217개 확인 필요")

results = []
counts = Counter()

for row in candidates:
    d_code = row["dur_code"]

    d_names = {
        norm(x.strip())
        for x in row["dur_names"].split("|")
        if x.strip()
    }

    embedded_codes = {
        x.strip()
        for x in row["all_embedded_m_codes"].split("|")
        if x.strip()
    }

    known_codes = embedded_codes & med_names.keys()
    missing_codes = embedded_codes - med_names.keys()

    # DUR 공식 성분명과 허가정보 M 코드 성분명
    # 둘 다 정확히 일치하는 경우만 후보로 분류
    matches = sorted(
        code for code in known_codes
        if d_names & med_names[code]
    )

    if len(matches) == 1:
        status = "EXACT_SINGLE_CANDIDATE"
    elif len(matches) > 1:
        status = "EXACT_MULTIPLE_CANDIDATES"
    elif known_codes:
        status = "NAME_REVIEW_REQUIRED"
    else:
        status = "NO_REFERENCE_MATCH"

    counts[status] += 1

    results.append({
        "dur_code": d_code,
        "dur_names": row["dur_names"],
        "matched_m_codes": " | ".join(matches),
        "embedded_codes_in_full_med": len(known_codes),
        "embedded_codes_not_in_full_med": len(missing_codes),
        "status": status,
    })

target = OUT / "dur_full_med_mapping_candidates.csv"

with target.open(
    "w", encoding="utf-8-sig", newline=""
) as f:
    writer = csv.DictWriter(
        f, fieldnames=list(results[0])
    )
    writer.writeheader()
    writer.writerows(results)

print("=== 전체 허가정보 ===")
print("원천 행:", total)
print("전체 고유 M 코드:", len(med_names))

print("\n=== DUR 217개 재분석 ===")
for status, count in sorted(counts.items()):
    print(f"{status}: {count}")

print("\n=== 신규 단일 후보 예시 ===")
shown = 0
for row in results:
    if (
        row["status"] == "EXACT_SINGLE_CANDIDATE"
        and row["dur_code"] not in {
            "D000075", "D000151", "D000155"
        }
    ):
        print(
            row["dur_code"],
            "=>",
            row["matched_m_codes"]
        )
        shown += 1
        if shown >= 15:
            break

print("\n결과 파일:", target)
