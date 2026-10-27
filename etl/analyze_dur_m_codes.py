import csv
import gzip
import json
import re
import unicodedata
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path("/home/ubuntu/backend-service/etl")
DUR = ROOT / "data/dur"
OUT = ROOT / "reports/ddi"
OUT.mkdir(parents=True, exist_ok=True)

with (OUT / "med_dictionary.json").open(encoding="utf-8") as f:
    med_rows = json.load(f)


def normalize(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", "", s).casefold()


# M 코드 -> 허가정보 성분명
med_names = defaultdict(set)
for r in med_rows:
    if r.get("code") and r.get("name"):
        med_names[str(r["code"]).strip()].add(
            normalize(r["name"])
        )


# D 코드별 공식 성분명 및 제품 성분 코드 빈도
dur_names = defaultdict(set)
embedded = defaultdict(Counter)

pattern = re.compile(r"\[(M\d+)\]")

total = 0

for path in sorted(DUR.glob("page_*.json.gz")):
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

        sides = (
            ("INGR_CODE", "INGR_KOR_NAME", "MAIN_INGR"),
            (
                "MIXTURE_INGR_CODE",
                "MIXTURE_INGR_KOR_NAME",
                "MIXTURE_MAIN_INGR",
            ),
        )

        for code_col, name_col, material_col in sides:
            d_code = str(row.get(code_col) or "").strip()
            d_name = str(row.get(name_col) or "").strip()
            raw = str(row.get(material_col) or "")

            if not d_code:
                continue

            if d_name:
                dur_names[d_code].add(normalize(d_name))

            # 복합제에서 나타난 코드도 기록하되 승인하지 않음
            for m_code in set(pattern.findall(raw)):
                embedded[d_code][m_code] += 1


results = []
status_counts = Counter()

for d_code in sorted(set(dur_names) | set(embedded)):
    names = dur_names[d_code]
    codes = embedded[d_code]

    # 공식 DUR 성분명과 허가정보 성분명이 같은 M 코드
    matched = [
        m for m in codes
        if m in med_names and (names & med_names[m])
    ]

    if len(matched) == 1:
        status = "NAME_CODE_CANDIDATE"
    elif len(matched) > 1:
        status = "AMBIGUOUS"
    else:
        status = "REVIEW_REQUIRED"

    status_counts[status] += 1

    results.append({
        "dur_code": d_code,
        "dur_names": " | ".join(sorted(names)),
        "embedded_m_code_count": len(codes),
        "matched_m_codes": " | ".join(sorted(matched)),
        "matched_code_count": len(matched),
        "all_embedded_m_codes": " | ".join(sorted(codes)),
        "status": status,
    })


path = OUT / "dur_m_code_candidates.csv"

with path.open("w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(
        f, fieldnames=list(results[0])
    )
    writer.writeheader()
    writer.writerows(results)

print("분석한 DUR 원본 행:", total)
print("DUR 고유 코드:", len(results))
print("후보 상태:", dict(status_counts))

print("\n=== 코드·성분명 일치 후보 ===")
for r in results:
    if r["status"] == "NAME_CODE_CANDIDATE":
        print(
            r["dur_code"],
            r["dur_names"][:35],
            "=>",
            r["matched_m_codes"],
        )

print("\n결과 파일:", path)
