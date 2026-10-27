"""DDI multi-chat: preserve engine evidence; render mandatory limits deterministically."""
from ddi_multi import check_multi
from ddi_presentation import present_pairs


def product_name(raw, product_id):
    return (
        (raw.get("products") or {})
        .get(str(product_id), {})
        .get("name")
        or f"제품 ID {product_id}"
    )



def verified_rule(rule):
    """확인 가능한 승인 조건을 모두 충족한 규칙만 인정."""
    return (
        isinstance(rule, dict)
        and rule.get("active") is True
        and rule.get("scope_verified") is True
        and rule.get("review_status") in (
            "APPROVED", "VERIFIED", "CONFIRMED"
        )
    )



def collect_evidence(multi_result):
    official = []
    verified_ingredients = []
    pending_ingredients = []
    public_pairs = []
    seen_official = set()

    for pair in multi_result["pair_results"]:
        raw = pair["ddi_result"]
        a, b = pair["product_a"], pair["product_b"]
        names = [
            product_name(raw, a),
            product_name(raw, b),
        ]

        # 원본의 반복 message만 제외. 매핑·규칙 결과는 보존.
        clean_raw = {
            k: v for k, v in raw.items()
            if k != "message"
        }

        public_pairs.append({
            "product_a": a,
            "product_b": b,
            "names": names,
            "status": pair["status"],
            "coverage": pair["coverage"],
            "ddi_result": clean_raw,
        })

        # 1. 제품번호 기준 공식 DUR 원본
        for item in raw.get("official_product_evidence") or []:
            key = (
                tuple(sorted((a, b))),
                str(item.get("dur_seq")),
                tuple(sorted(item.get("item_seqs") or [])),
                item.get("prohibition_reason"),
            )
            if key in seen_official:
                continue
            seen_official.add(key)

            official.append({
                "product_ids": [a, b],
                "product_names": names,
                "source": item.get("source"),
                "dur_seq": item.get("dur_seq"),
                "reason": item.get("prohibition_reason"),
                "evidence_type": "OFFICIAL_PRODUCT_DUR",
            })

        # 2. 승인된 성분 규칙
        for rule in raw.get("confirmed_rules") or []:
            if verified_rule(rule):
                verified_ingredients.append({
                    "product_ids": [a, b],
                    "product_names": names,
                    "rule": rule,
                    "evidence_type": "VERIFIED_INGREDIENT_RULE",
                })

        # 3. 미검증 성분 기반 매핑·규칙
        for rule in raw.get("candidate_rules") or []:
            if not isinstance(rule, dict):
                continue

            if rule.get("type") not in (
                "INGREDIENT_DUR_RULE",
                "PRODUCT_RULE",
                "DIRECT_DUR_EVIDENCE",
            ):
                continue

            pending_ingredients.append({
                "product_ids": [a, b],
                "product_names": names,
                "rule_type": rule.get("type"),
                "dur_codes": rule.get("dur_codes"),
                "review_status": rule.get("review_status"),
                "active": rule.get("active"),
                "scope_verified": rule.get("scope_verified"),
                "product_mapping_statuses": (
                    rule.get("product_mapping_statuses")
                ),
                "evidence": rule.get("evidence"),
                "classification": "UNVERIFIED_DO_NOT_ASSERT",
            })

    return (
        official,
        verified_ingredients,
        pending_ingredients,
        public_pairs,
    )



def converse_multi(event, ddi_handler, bedrock, model_id):
    """DDI conclusions and limitations are rendered from evidence, not generated."""
    question = event.get("message")
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
        raise ValueError("message는 1~1000자여야 합니다.")
    result = check_multi(event, ddi_handler)
    official, verified, pending, public_pairs = collect_evidence(result)
    presentation = present_pairs(public_pairs, result["coverage"])
    for pair, assessment in zip(public_pairs, presentation["pair_assessments"]):
        pair["assessment"] = assessment
    group_count = sum(len(a["evidence_groups"]) for a in presentation["pair_assessments"])
    return {
        "mode": "ddi_multi_chat", "model_id": model_id,
        "answer_source": "DETERMINISTIC_EVIDENCE_TEMPLATE",
        "product_ids": result["product_ids"], "product_count": result["product_count"],
        "pair_count": result["pair_count"], "coverage": result["coverage"],
        "evidence_summary": {
            "official_product_evidence_count": len(official),
            "official_evidence_group_count": group_count,
            "verified_ingredient_rule_count": len(verified),
            "pending_candidate_count": len(pending),
        },
        "pair_results": public_pairs,
        **presentation,
    }
