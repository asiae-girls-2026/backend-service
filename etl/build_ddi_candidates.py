import csv
import gzip
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path("/home/ubuntu/backend-service/etl")
DUR = ROOT / "data/dur"
OUT = ROOT / "reports/ddi"
OUT.mkdir(parents=True, exist_ok=True)

MED_FILE = OUT / "med_dictionary.json"

with MED_FILE.open(encoding="utf-8") as f:
    med_rows = json.load(f)


def norm(value):
    """후보 검색용 최소 정규화. 화학적 동등성 판단 아님."""
    value = unicodedata.normalize(
        "NFKC", str(value or "")
    )
    return re.sub(r"\s+", "", value).casefold()


def cell(value):
    return "" if value is None else str(value).strip()


med_by_name = defaultdict(set)

for row in med_rows:
    code = cell(row["code"])
    name = cell(row["name"])
    if code and name:
        med_by_name[norm(name)].add((code, name))


# DUR 코드별로 원천 명칭을 모두 보존
dur_names = defaultdict(set)
pair_examples = {}
total = 0

files = sorted(DUR.glob("page_*.json.gz"))
if not files:
    raise SystemExit("DUR 원본 없음")

for path in files:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)

    root = data.get("response", data)
    items = root["body"].get("items") or []

    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]

    for row in items:
        total += 1

        a = cell(row.get("INGR_CODE"))
        b = cell(row.get("MIXTURE_INGR_CODE"))
        name_a = cell(row.get("MAIN_INGR"))
        name_b = cell(row.get("MIXTURE_MAIN_INGR"))

        if a and name_a:
            dur_names[a].add(name_a)
        if b and name_b:
            dur_names[b].add(name_b)

        if a and b:
            pair = tuple(sorted((a, b)))
            pair_examples.setdefault(pair, {
                "dur_code_a": pair[0],
                "dur_code_b": pair[1],
                "item_seq": cell(row.get("ITEM_SEQ")),
                "mixture_item_seq": cell(
                    row.get("MIXTURE_ITEM_SEQ")
                ),
                "dur_seq": cell(row.get("DUR_SEQ")),
                "prohbt_content": cell(
                    row.get("PROHBT_CONTENT")
                ),
            })


# 원문에 [M083734]처럼 포함된 코드와 성분명을 분리
embedded_pattern = re.compile(r"^\[([A-Za-z]\d+)\]\s*(.*)$")

def name_variants(raw):
    variants = {raw}
    match = embedded_pattern.match(raw)
    if match and match.group(2):
        variants.add(match.group(2).strip())
    return variants


result = []

for dur_code in sorted(dur_names):
    raw_names = sorted(dur_names[dur_code])

    candidates = set()
    embedded_codes = set()

    for raw in raw_names:
        match = embedded_pattern.match(raw)
        if match:
            embedded_codes.add(match.group(1))

        for variant in name_variants(raw):
            candidates.update(
                med_by_name.get(norm(variant), set())
            )

    if not candidates:
        status = "REVIEW_REQUIRED"
    elif len({x[0] for x in candidates}) == 1:
        status = "EXACT_NAME_CANDIDATE"
    else:
        status = "AMBIGUOUS"

    result.append({
        "dur_code": dur_code,
        "dur_names": " | ".join(raw_names),
        "embedded_m_codes": " | ".join(
            sorted(embedded_codes)
        ),
        "med_candidate_codes": " | ".join(
            sorted({x[0] for x in candidates})
        ),
        "med_candidate_names": " | ".join(
            sorted({x[1] for x in candidates})
        ),
        "status": status,
    })


def save_csv(path, rows, headers):
    with path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=headers, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)


save_csv(
    OUT / "dur_med_mapping_candidates.csv",
    result,
    list(result[0]) if result else [],
)

save_csv(
    OUT / "dur_ingredient_pairs.csv",
    list(pair_examples.values()),
    [
        "dur_code_a", "dur_code_b",
        "item_seq", "mixture_item_seq",
        "dur_seq", "prohbt_content"
    ],
)

counts = {
    status: sum(
        r["status"] == status for r in result
    )
    for status in (
        "EXACT_NAME_CANDIDATE",
        "AMBIGUOUS",
        "REVIEW_REQUIRED",
    )
}

print("DUR 전체 읽은 행:", total)
print("DUR 코드 수:", len(result))
print("성분쌍 수:", len(pair_examples))
print("후보 상태별 건수:", counts)
print("결과 폴더:", OUT)
