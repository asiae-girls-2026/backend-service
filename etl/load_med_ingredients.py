import gzip
import argparse
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

BASE = Path(os.environ.get("CAREPONG_ETL_DIR", Path(__file__).resolve().parent))
RAW = BASE / "data/med_ingredients"
IDS = Path("/tmp/carepong_med_item_seqs.json")

parser = argparse.ArgumentParser()
parser.add_argument("--min-rows", type=int, default=10000)
parser.add_argument("--min-products", type=int, default=4000)
args = parser.parse_args()

with IDS.open(encoding="utf-8") as f:
    product_ids = set(map(str, json.load(f)))

rows = []
seen = set()
matched_products = set()

for path in sorted(RAW.glob("page_*.json.gz")):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)

    body = data.get("response", data)["body"]
    items = body.get("items") or []

    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]

    for item in items:
        item_seq = str(item.get("ITEM_SEQ") or "").strip()

        if item_seq not in product_ids:
            continue

        mtral_sn = str(item.get("MTRAL_SN") or "").strip()
        tamt_seq = str(item.get("TAMT_SEQ") or "").strip()
        name = str(item.get("MTRAL_NM") or "").strip()

        if not item_seq or not mtral_sn or not tamt_seq or not name:
            raise RuntimeError(
                f"필수값 누락: ITEM_SEQ={item_seq}"
            )

        key = (item_seq, mtral_sn, tamt_seq)
        if key in seen:
            raise RuntimeError(f"중복 원천 키: {key}")
        seen.add(key)
        matched_products.add(item_seq)

        qnt = item.get("QNT")
        qnt_raw = None if qnt is None else str(qnt).strip()
        amount = None

        if qnt_raw:
            try:
                amount = str(Decimal(qnt_raw.replace(",", "")))
            except InvalidOperation:
                pass

        rows.append({
            "item_seq": item_seq,
            "mtral_sn": mtral_sn,
            "tamt_seq": tamt_seq,
            "name": name,
            "code": item.get("MTRAL_CODE"),
            "amount": amount,
            "unit": item.get("INGD_UNIT_CD"),
            "qnt_raw": qnt_raw,
        })

print("적재 대상:", len(rows), flush=True)
print("매칭 의약품:", len(matched_products), flush=True)

if len(rows) < args.min_rows or len(matched_products) < args.min_products:
    raise SystemExit(
        "원천 품질 게이트 미달: "
        f"rows={len(rows)}/{args.min_rows}, "
        f"products={len(matched_products)}/{args.min_products}"
    )

import psycopg

sql = """
WITH input AS (
    SELECT *
    FROM jsonb_to_recordset(%s::jsonb) AS x(
        item_seq text,
        mtral_sn text,
        tamt_seq text,
        name text,
        code text,
        amount numeric,
        unit text,
        qnt_raw text
    )
),
upserted AS (
    INSERT INTO public.med_product_ingredient AS current (
        med_product_id,
        ingredient_id,
        source_ingredient_name,
        source_code,
        amount,
        unit,
        mapping_status,
        mtral_sn,
        tamt_seq,
        source_qnt_raw
    )
    SELECT
        p.med_product_id,
        NULL,
        i.name,
        i.code,
        i.amount,
        i.unit,
        'REVIEW',
        i.mtral_sn,
        i.tamt_seq,
        i.qnt_raw
    FROM input i
    JOIN public.med_product p
      ON p.item_seq = i.item_seq
    ON CONFLICT (med_product_id, mtral_sn, tamt_seq)
    DO UPDATE SET
        source_ingredient_name = EXCLUDED.source_ingredient_name,
        source_code = EXCLUDED.source_code,
        amount = EXCLUDED.amount,
        unit = EXCLUDED.unit,
        source_qnt_raw = EXCLUDED.source_qnt_raw,
        ingredient_id = CASE
            WHEN current.source_ingredient_name IS NOT DISTINCT FROM EXCLUDED.source_ingredient_name
             AND current.source_code IS NOT DISTINCT FROM EXCLUDED.source_code
            THEN current.ingredient_id ELSE NULL END,
        mapping_status = CASE
            WHEN current.source_ingredient_name IS NOT DISTINCT FROM EXCLUDED.source_ingredient_name
             AND current.source_code IS NOT DISTINCT FROM EXCLUDED.source_code
            THEN current.mapping_status ELSE 'REVIEW' END
    WHERE current.source_ingredient_name IS DISTINCT FROM EXCLUDED.source_ingredient_name
       OR current.source_code IS DISTINCT FROM EXCLUDED.source_code
       OR current.amount IS DISTINCT FROM EXCLUDED.amount
       OR current.unit IS DISTINCT FROM EXCLUDED.unit
       OR current.source_qnt_raw IS DISTINCT FROM EXCLUDED.source_qnt_raw
    RETURNING (xmax = 0) AS inserted
)
SELECT
    count(*) FILTER (WHERE inserted),
    count(*) FILTER (WHERE NOT inserted)
FROM upserted
"""

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    conn.execute("SET lock_timeout = '5s'")
    conn.execute("SET statement_timeout = '120s'")

    with conn.transaction():
        inserted, updated = conn.execute(
            sql, (json.dumps(rows, ensure_ascii=False),)
        ).fetchone()

    result = conn.execute("""
        SELECT count(*),
               count(DISTINCT med_product_id)
        FROM public.med_product_ingredient
    """).fetchone()

    print("이번 신규 INSERT:", inserted)
    print("이번 변경 UPDATE:", updated)
    print("현재 전체 성분 행:", result[0])
    print("현재 연결 제품:", result[1])
