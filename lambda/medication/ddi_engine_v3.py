"""CarePong DDI MVP v3.

Product rules, DUR product evidence, D-code ingredient candidates.
Read-only. Ingredient candidates never become confirmed solely
because their staging flags are approved.
"""
import json
import os
import sys

import psycopg


def check_ddi(conn, product_a, product_b):
    a, b = int(product_a), int(product_b)

    if a == b:
        raise ValueError("서로 다른 의약품 ID가 필요합니다.")

    products = conn.execute("""
        SELECT med_product_id, item_seq, product_name
        FROM public.med_product
        WHERE med_product_id IN (%s, %s)
    """, (a, b)).fetchall()

    info = {
        pid: {"item_seq": seq, "name": name}
        for pid, seq, name in products
    }

    if len(info) != 2:
        raise ValueError("존재하지 않는 의약품 ID가 포함되어 있습니다.")

    confirmed = []
    candidates = []
    reasons = []

    # 1. 기존 제품 기반 interaction_rule
    product_rules = conn.execute("""
        SELECT interaction_rule_id, review_status, active,
               severity, evidence_text, source_dataset
        FROM public.interaction_rule
        WHERE interaction_type = 'DDI'
          AND subject_type = 'PRODUCT'
          AND object_type = 'PRODUCT'
          AND (
              (subject_id = %s AND object_id = %s)
              OR (subject_id = %s AND object_id = %s)
          )
    """, (a, b, b, a)).fetchall()

    for rid, status, active, severity, evidence, source in product_rules:
        item = {
            "type": "PRODUCT_RULE",
            "rule_id": rid,
            "severity": severity,
            "evidence": evidence,
            "source": source,
            "review_status": status,
        }

        if status == "APPROVED" and active:
            confirmed.append(item)
        else:
            candidates.append(item)

    # 2. 정확한 품목쌍의 DUR 원본 근거
    direct_rows = conn.execute("""
        SELECT source_key, dur_seq,
               dur_code_a, dur_code_b,
               form_name_a, form_name_b,
               prohibition_content,
               review_status
        FROM public.med_dur_product_evidence
        WHERE (
            (product_a_id = %s AND product_b_id = %s)
            OR
            (product_a_id = %s AND product_b_id = %s)
        )
    """, (a, b, b, a)).fetchall()

    for row in direct_rows:
        key, seq, da, db, fa, fb, content, status = row
        candidates.append({
            "type": "DIRECT_DUR_EVIDENCE",
            "source_key": key,
            "dur_seq": seq,
            "dur_codes": [da, db],
            "form_names_in_source_order": [fa, fb],
            "evidence": content,
            "review_status": status,
            "scope_verified": False,
        })

    if direct_rows:
        reasons.append("DIRECT_DUR_EVIDENCE_REQUIRES_REVIEW")

    # 3. 제품별 D코드 매핑
    mappings = conn.execute("""
        SELECT med_product_id, dur_code, review_status,
               by_product, by_mcode
        FROM public.med_dur_product_mapping
        WHERE med_product_id IN (%s, %s)
    """, (a, b)).fetchall()

    codes = {a: {}, b: {}}

    for pid, code, status, by_product, by_mcode in mappings:
        codes[pid][code] = {
            "review_status": status,
            "by_product": bool(by_product),
            "by_mcode": bool(by_mcode),
        }

    if not codes[a]:
        reasons.append("PRODUCT_A_D_CODE_UNRESOLVED")
    if not codes[b]:
        reasons.append("PRODUCT_B_D_CODE_UNRESOLVED")

    # 4. DUR 성분쌍 후보 조회
    if codes[a] and codes[b]:
        all_codes = sorted(set(codes[a]) | set(codes[b]))

        ingredient_rows = conn.execute("""
            SELECT dur_code_a, dur_code_b,
                   review_status, active, evidence_example
            FROM public.med_dur_ingredient_rule_stage
            WHERE dur_code_a = ANY(%s)
              AND dur_code_b = ANY(%s)
        """, (all_codes, all_codes)).fetchall()

        for da, db, status, active, evidence in ingredient_rows:
            forward = da in codes[a] and db in codes[b]
            reverse = db in codes[a] and da in codes[b]

            if not (forward or reverse):
                continue

            ca, cb = (da, db) if forward else (db, da)
            ma, mb = codes[a][ca], codes[b][cb]

            candidates.append({
                "type": "INGREDIENT_DUR_RULE",
                "dur_codes": [da, db],
                "review_status": status,
                "active": active,
                "product_mapping_statuses": [
                    ma["review_status"], mb["review_status"]
                ],
                "evidence": evidence,
                "scope_verified": False,
            })

        if any(
            r["type"] == "INGREDIENT_DUR_RULE"
            for r in candidates
        ):
            reasons.append("INGREDIENT_SCOPE_REQUIRES_REVIEW")

    # 5. 안전한 결과 분류
    if confirmed:
        status = "MATCHED_RULE"
    elif candidates or not codes[a] or not codes[b]:
        status = "UNCERTAIN"
    else:
        status = "NO_MATCH_IN_AVAILABLE_RULES"

    return {
        "status": status,
        "coverage": "INCOMPLETE",
        "products": {
            str(a): info[a],
            str(b): info[b],
        },
        "confirmed_rules": confirmed,
        "candidate_rules": candidates,
        "candidate_d_codes": {
            str(a): sorted(codes[a]),
            str(b): sorted(codes[b]),
        },
        "reason_codes": sorted(set(reasons)),
        "message": (
            "확인된 범위 내 검사 결과입니다. "
            "미검출은 병용 안전성을 의미하지 않습니다. "
            "검토 대기 규칙은 확정 병용금기로 판정하지 않습니다."
        ),
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: python ddi_engine_v3.py PRODUCT_ID_A PRODUCT_ID_B"
        )

    with psycopg.connect(
        os.environ["DATABASE_URL"], connect_timeout=10
    ) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            result = check_ddi(conn, sys.argv[1], sys.argv[2])
            print(json.dumps(
                result, ensure_ascii=False,
                indent=2, default=str
            ))
