"""CarePong DDI v2: product rules + DUR D-code ingredient rules.

Read-only. Unapproved rules/mappings never produce a confirmed match.
"""

import json
import os
import sys
import psycopg


def check_ddi(conn, product_a, product_b):
    a, b = int(product_a), int(product_b)

    if a == b:
        raise ValueError("서로 다른 제품 ID 두 개가 필요합니다.")

    # 1. 기존 제품번호 기반 규칙: 승인 + 활성화만 확정 검출
    product_rules = conn.execute("""
        SELECT interaction_rule_id, review_status, active,
               severity, evidence_text, source_dataset
        FROM public.interaction_rule
        WHERE subject_type = 'PRODUCT'
          AND object_type = 'PRODUCT'
          AND (
              (subject_id = %s AND object_id = %s)
              OR
              (subject_id = %s AND object_id = %s)
          )
          AND interaction_type = 'DDI'
    """, (a, b, b, a)).fetchall()

    confirmed = []
    pending = []

    for rid, status, active, severity, evidence, source in product_rules:
        item = {
            "type": "PRODUCT",
            "rule_id": rid,
            "severity": severity,
            "evidence": evidence,
            "source": source,
        }
        if status == "APPROVED" and active:
            confirmed.append(item)
        elif status == "PENDING" and not active:
            pending.append(item)

    # 2. 두 제품의 D코드 후보 연결
    mappings = conn.execute("""
        SELECT med_product_id, dur_code, review_status,
               by_product, by_mcode
        FROM public.med_dur_product_mapping
        WHERE med_product_id IN (%s, %s)
    """, (a, b)).fetchall()

    codes = {a: {}, b: {}}

    for pid, dcode, status, by_product, by_mcode in mappings:
        codes[pid][dcode] = {
            "status": status,
            "by_product": by_product,
            "by_mcode": by_mcode,
        }

    # 3. 제품 양쪽에 D코드가 있어야 성분쌍 비교 가능
    if codes[a] and codes[b]:
        ingredient_rows = conn.execute("""
            SELECT dur_code_a, dur_code_b, review_status,
                   active, evidence_example
            FROM public.med_dur_ingredient_rule_stage
            WHERE dur_code_a IN (
                SELECT dur_code
                FROM public.med_dur_product_mapping
                WHERE med_product_id = %s
            )
            AND dur_code_b IN (
                SELECT dur_code
                FROM public.med_dur_product_mapping
                WHERE med_product_id = %s
            )
            UNION ALL
            SELECT dur_code_a, dur_code_b, review_status,
                   active, evidence_example
            FROM public.med_dur_ingredient_rule_stage
            WHERE dur_code_a IN (
                SELECT dur_code
                FROM public.med_dur_product_mapping
                WHERE med_product_id = %s
            )
            AND dur_code_b IN (
                SELECT dur_code
                FROM public.med_dur_product_mapping
                WHERE med_product_id = %s
            )
        """, (a, b, b, a)).fetchall()

        seen = set()

        for da, db, status, active, evidence in ingredient_rows:
            key = (da, db)

            if key in seen:
                continue
            seen.add(key)

            forward = da in codes[a] and db in codes[b]
            if forward:
                ma, mb = codes[a][da], codes[b][db]
            else:
                ma, mb = codes[a][db], codes[b][da]

            item = {
                "type": "INGREDIENT",
                "dur_codes": [da, db],
                "evidence": evidence,
                "mapping_statuses": [ma["status"], mb["status"]],
            }

            if (
                status == "APPROVED"
                and active
                and ma["status"] == "APPROVED"
                and mb["status"] == "APPROVED"
            ):
                confirmed.append(item)
            else:
                pending.append(item)

    # 4. 최종 결과
    if confirmed:
        status = "MATCHED_RULE"
    elif pending or not codes[a] or not codes[b]:
        status = "UNCERTAIN"
    else:
        status = "NO_MATCH_IN_AVAILABLE_RULES"

    return {
        "status": status,
        "coverage": "INCOMPLETE",
        "product_ids": [a, b],
        "matched_rules": confirmed,
        "candidate_rules": pending,
        "candidate_d_codes": {
            str(a): sorted(codes[a]),
            str(b): sorted(codes[b]),
        },
        "message": (
            "미승인 후보 또는 확인되지 않은 범위가 있습니다. "
            "일치하는 규칙이 없더라도 병용 안전성을 의미하지 않습니다."
        ),
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: python ddi_engine_v2.py PRODUCT_ID_A PRODUCT_ID_B"
        )

    with psycopg.connect(
        os.environ["DATABASE_URL"],
        connect_timeout=10
    ) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            result = check_ddi(conn, sys.argv[1], sys.argv[2])
            print(json.dumps(
                result, ensure_ascii=False, indent=2, default=str
            ))
