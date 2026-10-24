import os
import sys
from itertools import combinations
import psycopg

sys.path.insert(0, "/tmp")
from ddi_engine_v3 import check_ddi


def types(result):
    return {r["type"] for r in result["candidate_rules"]}


def report(label, result):
    print(
        label,
        "| status:", result["status"],
        "| candidates:", len(result["candidate_rules"]),
        "| confirmed:", len(result["confirmed_rules"]),
        "| reasons:", result["reason_codes"],
    )


with psycopg.connect(
    os.environ["DATABASE_URL"],
    connect_timeout=10
) as conn:
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")

        # T1: 실제 품목별 DUR 근거
        direct = conn.execute("""
            SELECT product_a_id, product_b_id
            FROM public.med_dur_product_evidence
            ORDER BY product_a_id, product_b_id
            LIMIT 1
        """).fetchone()

        assert direct, "T1 테스트용 품목 근거가 없습니다."

        r1 = check_ddi(conn, *direct)
        report("T1 DIRECT", r1)

        assert "DIRECT_DUR_EVIDENCE" in types(r1)
        assert r1["status"] == "UNCERTAIN"
        assert not r1["confirmed_rules"]
        print("PASS T1")

        # T2: 실제 성분 기반 DUR 후보
        ingredient_pair = conn.execute("""
            SELECT a.med_product_id, b.med_product_id
            FROM public.med_dur_ingredient_rule_stage r
            JOIN public.med_dur_product_mapping a
              ON a.dur_code = r.dur_code_a
            JOIN public.med_dur_product_mapping b
              ON b.dur_code = r.dur_code_b
            WHERE a.med_product_id <> b.med_product_id
            ORDER BY a.med_product_id, b.med_product_id
            LIMIT 1
        """).fetchone()

        assert ingredient_pair, "T2 성분 후보 제품쌍이 없습니다."

        r2 = check_ddi(conn, *ingredient_pair)
        report("T2 INGREDIENT", r2)

        assert "INGREDIENT_DUR_RULE" in types(r2)
        assert r2["status"] == "UNCERTAIN"
        assert not r2["confirmed_rules"]
        print("PASS T2")

        # T3: 매핑이 없는 제품
        unmapped = conn.execute("""
            SELECT p.med_product_id
            FROM public.med_product p
            WHERE NOT EXISTS (
                SELECT 1
                FROM public.med_dur_product_mapping m
                WHERE m.med_product_id = p.med_product_id
            )
            ORDER BY p.med_product_id
            LIMIT 1
        """).fetchone()

        assert unmapped, "T3 매핑 없는 제품이 없습니다."

        r3 = check_ddi(conn, direct[0], unmapped[0])
        report("T3 UNMAPPED", r3)

        assert r3["status"] == "UNCERTAIN"
        assert any(
            "D_CODE_UNRESOLVED" in code
            for code in r3["reason_codes"]
        )
        assert not r3["confirmed_rules"]
        print("PASS T3")

        # T4: 매핑은 있으나 일치 규칙이 없는 실제 제품쌍
        mapped = [
            row[0] for row in conn.execute("""
                SELECT DISTINCT med_product_id
                FROM public.med_dur_product_mapping
                ORDER BY med_product_id
                LIMIT 100
            """)
        ]

        no_match = None

        for a, b in combinations(mapped, 2):
            r = check_ddi(conn, a, b)

            if r["status"] == "NO_MATCH_IN_AVAILABLE_RULES":
                no_match = (a, b, r)
                break

        assert no_match, (
            "T4에 적합한 제품쌍을 첫 100개에서 찾지 못했습니다."
        )

        a, b, r4 = no_match
        print("T4 제품 ID:", a, b)
        report("T4 NO_MATCH", r4)

        assert r4["coverage"] == "INCOMPLETE"
        assert not r4["confirmed_rules"]
        assert not r4["candidate_rules"]
        print("PASS T4")

        print("\n=== DDI MVP v3: ALL 4 TESTS PASSED ===")
