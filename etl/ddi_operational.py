"""CarePong minimal DDI: exact DUR item-pair evidence first."""
import json
import os
import sys

import psycopg
from ddi_engine_v3 import check_ddi


def check_medications(conn, product_a, product_b):
    a, b = int(product_a), int(product_b)

    # v3 validates both product IDs and supplies ingredient candidates.
    base = check_ddi(conn, a, b)

    # Exact item-pair evidence, independent of unapproved ingredient mappings.
    rows = conn.execute("""
        SELECT
            e.dur_seq,
            e.product_a_id,
            e.product_b_id,
            e.dur_code_a,
            e.dur_code_b,
            e.form_name_a,
            e.form_name_b,
            e.prohibition_content,
            e.raw_evidence,
            p1.item_seq,
            p2.item_seq
        FROM public.med_dur_product_evidence e
        JOIN public.med_product p1
          ON p1.med_product_id = e.product_a_id
        JOIN public.med_product p2
          ON p2.med_product_id = e.product_b_id
        WHERE
            (e.product_a_id = %s AND e.product_b_id = %s)
            OR
            (e.product_a_id = %s AND e.product_b_id = %s)
    """, (a, b, b, a)).fetchall()

    verified = []

    for (
        seq, pa, pb, da, db, fa, fb, reason,
        raw, item_a, item_b
    ) in rows:

        # Minimum mandatory checks against the stored original row.
        if str(raw.get("TYPE_CODE", "")).strip().upper() != "A":
            continue
        if str(raw.get("ITEM_SEQ", "")).strip() != item_a:
            continue
        if str(raw.get("MIXTURE_ITEM_SEQ", "")).strip() != item_b:
            continue
        if not seq or not reason:
            continue

        verified.append({
            "dur_seq": seq,
            "product_ids": [pa, pb],
            "item_seqs": [item_a, item_b],
            "dur_codes": [da, db],
            "form_names": [fa, fb],
            "prohibition_reason": reason,
            "source": "MFDS_DUR",
        })

    if verified:
        base["status"] = "CONFIRMED_DUR_PRODUCT_MATCH"
        base["official_product_evidence"] = verified
        base["message"] = (
            "식약처 DUR 원본에서 해당 두 품목번호의 "
            "병용금기 조합이 확인되었습니다. "
            "원본 근거와 제형을 확인하고 의사·약사와 상담하세요. "
            "이는 개별 환자의 임상적 위험도 확정 판정은 아닙니다."
        )
    else:
        base["official_product_evidence"] = []
        if base["status"] == "MATCHED_RULE":
            # v3 approved product rules need separate release/scope review.
            base["status"] = "UNCERTAIN"
        base["message"] = (
            "해당 품목쌍의 직접 DUR 근거는 확인되지 않았습니다. "
            "성분 기반 후보 또는 확인되지 않은 범위가 있을 수 있으며, "
            "이는 병용 안전성을 의미하지 않습니다."
        )

    return base


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: python ddi_operational.py PRODUCT_A PRODUCT_B"
        )

    with psycopg.connect(
        os.environ["DATABASE_URL"], connect_timeout=10
    ) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            result = check_medications(conn, sys.argv[1], sys.argv[2])

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
