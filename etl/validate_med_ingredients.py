import gzip
import json
from pathlib import Path
from collections import Counter

base = Path("/home/ubuntu/backend-service/etl/data/med_ingredients")
files = sorted(base.glob("page_*.json.gz"))

with open("/tmp/carepong_med_item_seqs.json") as f:
    existing = set(map(str, json.load(f)))

rows = []
totals = set()
page_numbers = []

for file in files:
    with gzip.open(file, "rt", encoding="utf-8") as f:
        data = json.load(f)

    body = data["body"]
    totals.add(int(body["totalCount"]))
    page_numbers.append(int(body["pageNo"]))

    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]

    rows.extend(items)

codes = [str(r.get("ITEM_SEQ") or "") for r in rows]
matched = [r for r in rows if str(r.get("ITEM_SEQ") or "") in existing]
matched_codes = {str(r["ITEM_SEQ"]) for r in matched}

keys = [
    (
        str(r.get("ITEM_SEQ") or ""),
        str(r.get("MTRAL_SN") or ""),
        str(r.get("TAMT_SEQ") or "")
    )
    for r in matched
]

key_counts = Counter(keys)
duplicate_keys = {
    k: n for k, n in key_counts.items() if n > 1
}

missing_fields = Counter()
for r in matched:
    for field in ("ITEM_SEQ", "MTRAL_CODE", "MTRAL_SN", "TAMT_SEQ"):
        if r.get(field) is None or r.get(field) == "":
            missing_fields[field] += 1

print("========== 원본 수집 검증 ==========")
print("파일 수:", len(files))
print("원천 총건수:", totals)
print("실제 수집 행:", len(rows))
print("페이지 번호 연속:", page_numbers == list(range(1, len(files) + 1)))
print("원천 건수 일치:", len(totals) == 1 and len(rows) == next(iter(totals), -1))

print("\n========== 기존 의약품 매칭 ==========")
print("기존 의약품:", len(existing))
print("매칭된 주성분 행:", len(matched))
print("매칭된 의약품:", len(matched_codes))
print("주성분 미확인 의약품:", len(existing - matched_codes))
print("기존 Catalog 외 원천 행:", len(rows) - len(matched))

print("\n========== 원천 식별자 검증 ==========")
print("고유 ITEM_SEQ/MTRAL_SN/TAMT_SEQ:", len(key_counts))
print("중복 키 종류:", len(duplicate_keys))
print("중복으로 추가된 행:", sum(n - 1 for n in duplicate_keys.values()))
print("필수 필드 누락:", dict(missing_fields))

for key, n in list(duplicate_keys.items())[:5]:
    print("중복 예시:", key, "등장:", n)
