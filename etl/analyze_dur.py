import gzip
import json
from collections import Counter
from pathlib import Path

BASE = Path("/home/ubuntu/backend-service/etl/data/dur")
PRODUCT_JSON = Path("/tmp/carepong_med_item_seqs.json")
CODE_JSON = Path("/tmp/carepong_med_codes.json")

with PRODUCT_JSON.open(encoding="utf-8") as f:
    product_ids = set(map(str, json.load(f)))

with CODE_JSON.open(encoding="utf-8") as f:
    med_codes = {
        str(x).strip()
        for x in json.load(f)
        if x is not None and str(x).strip()
    }

files = sorted(BASE.glob("page_*.json.gz"))

if not files:
    raise SystemExit("DUR 원본 파일 없음")

types = Counter()
dur_codes = set()
matched_codes = set()
matched_products = set()
pair_counts = Counter()

total_rows = 0
a_rows = 0
a_product_rows = 0
a_code_rows = 0
missing_keys = Counter()
examples = []

expected_total = None

for path in files:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)

    root = data.get("response", data)
    body = root["body"]
    total = int(body["totalCount"])

    if expected_total is None:
        expected_total = total
    elif total != expected_total:
        raise RuntimeError(
            f"원천 totalCount 불일치: {path.name}"
        )

    items = body.get("items") or []

    if isinstance(items, dict):
        items = items.get("item", [])

    if isinstance(items, dict):
        items = [items]

    for row in items:
        total_rows += 1

        type_code = str(row.get("TYPE_CODE") or "").strip()
        types[type_code] += 1

        if type_code != "A":
            continue

        a_rows += 1

        item1 = str(row.get("ITEM_SEQ") or "").strip()
        item2 = str(row.get("MIXTURE_ITEM_SEQ") or "").strip()

        code1 = str(row.get("INGR_CODE") or "").strip()
        code2 = str(row.get("MIXTURE_INGR_CODE") or "").strip()

        if not item1 or not item2:
            missing_keys["제품번호"] += 1

        if not code1 or not code2:
            missing_keys["성분코드"] += 1

        found_products = {item1, item2} & product_ids
        matched_products.update(found_products)

        if found_products:
            a_product_rows += 1

        for code in (code1, code2):
            if code:
                dur_codes.add(code)
                if code in med_codes:
                    matched_codes.add(code)

        if code1 in med_codes or code2 in med_codes:
            a_code_rows += 1

        if code1 and code2:
            pair = tuple(sorted((code1, code2)))
            pair_counts[pair] += 1

        if len(examples) < 3:
            examples.append({
                "ITEM_SEQ": item1,
                "MIXTURE_ITEM_SEQ": item2,
                "INGR_CODE": code1,
                "MIXTURE_INGR_CODE": code2,
                "TYPE_CODE": type_code,
            })

print("\n========== DUR 전체 원본 ==========")
print("파일 수:", len(files))
print("원천 totalCount:", expected_total)
print("실제 수집 행:", total_rows)
print("원천 건수 일치:", total_rows == expected_total)
print("TYPE_CODE 분포:", dict(types))

print("\n========== 병용금기(TYPE_CODE=A) ==========")
print("병용금기 원천 행:", a_rows)
print("고유 성분 코드:", len(dur_codes))
print("고유 성분쌍:", len(pair_counts))
print("제품번호 또는 성분코드 누락:", dict(missing_keys))

print("\n========== 기존 RDS와 교차 비교 ==========")
print("기존 의약품:", len(product_ids))
print("DUR에 등장하는 기존 의약품:", len(matched_products))
print("기존 의약품이 한쪽 이상 포함된 병용금기 행:", a_product_rows)
print("RDS MTRAL_CODE 고유값:", len(med_codes))
print("DUR 코드와 정확히 일치하는 코드:", len(matched_codes))
print("코드가 한쪽 이상 일치하는 병용금기 행:", a_code_rows)

print("\n========== 원천 구조 예시 ==========")
for item in examples:
    print(item)
