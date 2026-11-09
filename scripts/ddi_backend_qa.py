import os
import json
import sys
from collections import Counter

import boto3
import psycopg

FUNCTION = "carepong-medication-orchestrator"
REGION = "ap-northeast-2"

EXPECTED = {
    (True, True, True): "CHECK_APPROVED_RULE",
    (True, True, False): "NO_RULE_FOUND",
    (True, False, True): "RESOLVE_M_TO_D",
    (True, False, False): "RESOLVE_D_CODE",
    (False, True, True): "VERIFY_PRODUCT_D",
    (False, True, False): "NO_RULE_FOUND",
    (False, False, True): "RESOLVE_PRODUCT_MAPPING",
    (False, False, False): "INSUFFICIENT_EVIDENCE",
}

CASES = [
    ("MAPPED", [311, 326], "CHECK_APPROVED_RULE",
     "MATCHED_RULE"),
    ("MISSING_D", [3833, 129], "RESOLVE_D_CODE",
     "UNCERTAIN"),
    ("MISSING_M_D", [23045, 32950],
     "INSUFFICIENT_EVIDENCE", "UNCERTAIN"),
]

failures = []
stats = Counter()

def check(label, condition, detail=""):
    stats["PASS" if condition else "FAIL"] += 1
    print(
        ("PASS" if condition else "FAIL"),
        label, detail, flush=True
    )
    if not condition:
        failures.append(label)

def invoke(client, pair):
    response = client.invoke(
        FunctionName=FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps({
            "mode": "ddi_multi_chat",
            "product_ids": pair,
            "message": "두 의약품의 상호작용을 검사해줘"
        }, ensure_ascii=False).encode()
    )
    raw = response["Payload"].read()
    result = json.loads(raw)

    if response.get("FunctionError"):
        raise RuntimeError(
            "LAMBDA_FUNCTION_ERROR: " +
            str(result.get("errorMessage", result))[:250]
        )

    pair_results = result.get("pair_results") or []
    if len(pair_results) != 1:
        raise RuntimeError("EXPECTED_ONE_PAIR_RESULT")

    pr = pair_results[0]
    ddi = pr.get("ddi_result") or {}
    return result, pr, ddi

with psycopg.connect(
    os.environ["DATABASE_URL"],
    connect_timeout=15,
    options="-c default_transaction_read_only=on"
) as conn:
    client = boto3.client("lambda", region_name=REGION)

    print("\n=== 1. POLICY CONTRACT ===")
    rows = conn.execute("""
        SELECT has_m_code, has_d_code, has_rule,
               action_code
        FROM public.ddi_mapping_policy
    """).fetchall()

    actual = {(m, d, r): a for m, d, r, a in rows}

    for state, action in EXPECTED.items():
        check("POLICY_" + str(state),
              actual.get(state) == action,
              str(actual.get(state)))

    print("\n=== 2. RELEASE AND RULES ===")
    approved = conn.execute("""
        SELECT count(*)
        FROM public.med_dur_ingredient_rule_stage
        WHERE rule_type='DDI'
          AND review_status='APPROVED'
          AND active=TRUE
    """).fetchone()[0]

    releases = conn.execute("""
        SELECT count(*)
        FROM public.rule_release
        WHERE release_code =
          'carepong-ddi-internal-qa-20261109-v1'
          AND status='ACTIVE'
    """).fetchone()[0]

    check("APPROVED_RULES_346", approved == 346, str(approved))
    check("ACTIVE_RELEASE", releases == 1, str(releases))

    scopes = [
        (scope, list(map(int, scope.split(":"))))
        for (scope,) in conn.execute("""
            SELECT DISTINCT a.scope_pair
            FROM public.ddi_scoped_approval a
            JOIN public.rule_release r
              ON r.rule_release_id=a.rule_release_id
            WHERE a.target_type='D_PAIR'
              AND a.lifecycle='ACTIVE'
              AND r.status='ACTIVE'
            ORDER BY a.scope_pair
        """).fetchall()
    ]

    print("ACTIVE_SCOPES:", len(scopes))
    check("EXPECTED_48_SCOPES", len(scopes) == 48)

    print("\n=== 3. ACTUAL LAMBDA TESTS ===")
    for label, pair, expected_action, expected_status in CASES:
        try:
            result, pr, ddi = invoke(client, pair)
            action = ddi.get("mapping_action")
            status = pr.get("status")
            confirmed = len(ddi.get("confirmed_rules") or [])

            ok = (action == expected_action
                  and status == expected_status
                  and pr.get("coverage") == "INCOMPLETE")

            if expected_status == "MATCHED_RULE":
                ok = ok and confirmed > 0
            else:
                ok = ok and confirmed == 0

            check(label, ok,
                  f"action={action} status={status} "
                  f"confirmed={confirmed}")
            print("ANSWER:", result.get("answer"))

        except Exception as exc:
            check(label, False,
                  f"{type(exc).__name__}: {str(exc)[:250]}")

    print("\n=== 4. 48 PAIR REGRESSION ===")
    for scope, pair in scopes:
        try:
            result, pr, ddi = invoke(client, pair)
            confirmed = len(ddi.get("confirmed_rules") or [])

            ok = (
                ddi.get("mapping_action")
                   == "CHECK_APPROVED_RULE"
                and pr.get("status") == "MATCHED_RULE"
                and confirmed > 0
                and pr.get("coverage") == "INCOMPLETE"
            )
            check("REGRESSION_" + scope, ok,
                  f"confirmed={confirmed}")
        except Exception as exc:
            check("REGRESSION_" + scope, False,
                  f"{type(exc).__name__}: {str(exc)[:250]}")

    print("\n=== 5. MISSING PRODUCT CASE ===")
    missing = conn.execute("""
        SELECT to_regclass(
            'public.med_product'
        ) IS NOT NULL
    """).fetchone()[0]
    check("CATALOG_TABLE_AVAILABLE", missing)

    print("\n=== 6. LIMITATIONS ===")
    print("Policy contracts tested:", len(EXPECTED))
    print("Real Lambda cases:", len(CASES) + len(scopes))
    print("Unreachable branches:",
          "RESOLVE_M_TO_D, RESOLVE_PRODUCT_MAPPING")
    print("NOTE: Product-name search and LUI HTTP flow")
    print("      require separate API integration QA.")
    print("NOTE: Formulation/route suitability")
    print("      is not established by MATCHED_RULE.")

print("\n=== FINAL SUMMARY ===")
print("PASSED:", stats["PASS"])
print("FAILED:", stats["FAIL"])
print("RESULT:",
      "BACKEND_QA_PASS" if not failures
      else "BACKEND_QA_FAILED")

if failures:
    print("FAILED_CHECKS:", failures)
    sys.exit(1)
