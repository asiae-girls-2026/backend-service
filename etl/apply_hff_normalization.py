import os
import re
import json
import argparse
import unicodedata
import psycopg

def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip()

parser = argparse.ArgumentParser()
parser.add_argument("--limit", type=int, default=1000)
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()

if args.limit < 1:
    parser.error("--limit는 1 이상이어야 합니다.")

processed = 0
updated = 0
too_long = 0
last_id = 0

with psycopg.connect(
    os.environ["DATABASE_URL"],
    connect_timeout=10
) as conn:

    dbname = conn.execute(
        "SELECT current_database()"
    ).fetchone()[0]

    if dbname != "carepong_beta":
        raise RuntimeError("잘못된 DB: " + dbname)

    conn.rollback()

    while processed < args.limit:
        batch_size = min(1000, args.limit - processed)

        rows = conn.execute("""
            SELECT
                hff_product_ingredient_id,
                raw_ingredient_name
            FROM public.hff_product_ingredient
            WHERE hff_product_ingredient_id > %s
              AND normalized_name IS NULL
            ORDER BY hff_product_ingredient_id
            LIMIT %s
        """, (last_id, batch_size)).fetchall()

        if not rows:
            break

        payload = []

        for ingredient_id, raw in rows:
            last_id = ingredient_id
            processed += 1

            normalized = normalize(raw)

            if not normalized:
                too_long += 1
                continue

            payload.append({
                "id": ingredient_id,
                "name": normalized
            })

        if args.apply and payload:
            result = conn.execute("""
                UPDATE public.hff_product_ingredient h
                SET normalized_name = v.name
                FROM jsonb_to_recordset(%s::jsonb)
                AS v(id bigint, name text)
                WHERE h.hff_product_ingredient_id = v.id
                  AND h.normalized_name IS NULL
                RETURNING h.hff_product_ingredient_id
            """, (json.dumps(payload, ensure_ascii=False),))

            updated += len(result.fetchall())
            conn.commit()
        else:
            conn.rollback()

    print("========== 정규화 처리 결과 ==========")
    print("검사 행:", processed)
    print("정규화 가능:", processed - too_long)
    print("300자 초과 또는 빈 값:", too_long)
    print("실제 UPDATE:", updated)
    print("모드:", "APPLY" if args.apply else "DRY-RUN")
