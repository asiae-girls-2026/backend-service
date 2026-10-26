"""Deterministic DDI presentation; does not approve or modify source rules."""
VERSION = "20261026.1"
APPROVED = {"APPROVED", "VERIFIED", "CONFIRMED"}
COMPLETE = {"COMPLETE_FOR_AVAILABLE_RULES", "COMPLETE"}


def unique(items):
    return list(dict.fromkeys(items))


def text(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def approved(rule):
    return (isinstance(rule, dict) and rule.get("active") is True
            and rule.get("scope_verified") is True
            and rule.get("review_status") in APPROVED)


def pair_assessment(raw, a, b):
    """Source item-pair validation is independent of ingredient approval."""
    products = raw.get("products") or {}
    product = lambda i: products.get(str(i), products.get(i, {})) or {}
    names = [text(product(i).get("name")) or "선택한 의약품" for i in (a, b)]
    expected_items = sorted(str(product(i).get("item_seq") or "").strip() for i in (a, b))
    groups = {}
    rejected = 0
    for item in raw.get("official_product_evidence") or []:
        if not isinstance(item, dict):
            rejected += 1
            continue
        ids = item.get("product_ids") or []
        items = item.get("item_seqs") or []
        check = item.get("verification") or {}
        flags = check.get("checks") or {}
        valid = (
            raw.get("status") == "CONFIRMED_DUR_PRODUCT_MATCH"
            and item.get("source") == "MFDS_DUR"
            and len(ids) == 2 and {str(x) for x in ids} == {str(a), str(b)}
            and len(items) == 2 and all(expected_items)
            and sorted(str(x).strip() for x in items) == expected_items
            and text(str(item.get("dur_seq") or ""))
            and text(item.get("prohibition_reason"))
            and check.get("level") == "SOURCE_ITEM_PAIR_MATCH"
            and all(flags.get(k) is True for k in (
                "type_code_a", "item_seq_match", "mixture_item_seq_match"))
        )
        if not valid:
            rejected += 1
            continue
        # Collapse reversed rows only if reason AND product/form associations agree.
        forms = item.get("form_names") or []
        form_scope = tuple(sorted(zip((str(x) for x in items),
                                      (str(x) for x in forms)))) if len(forms) == 2 else ()
        reason = text(item["prohibition_reason"])
        key = (item["source"], tuple(expected_items), reason, form_scope)
        if key not in groups:
            groups[key] = {
                "evidence_type": "OFFICIAL_PRODUCT_DUR",
                "product_ids": [a, b], "product_names": names,
                "item_seqs": expected_items, "source": item["source"],
                "reason": reason, "dur_seqs": [], "source_record_count": 0,
                "verification_level": "SOURCE_ITEM_PAIR_MATCH",
                "form_scope": [{"item_seq": i, "form_name": f} for i, f in form_scope],
                "clinical_assessment": False,
            }
        group = groups[key]
        group["dur_seqs"] = unique(group["dur_seqs"] + [str(item["dur_seq"])])
        group["source_record_count"] += 1

    verified = [r for r in raw.get("confirmed_rules") or [] if approved(r)]
    unverified_confirmed = sum(not approved(r) for r in raw.get("confirmed_rules") or [])
    candidates = [r for r in raw.get("candidate_rules") or [] if isinstance(r, dict)]
    references = []
    for rule in candidates:
        evidence = rule.get("evidence")
        if rule.get("type") != "INGREDIENT_DUR_RULE" or not isinstance(evidence, dict):
            continue
        ia, ib, reason = (text(evidence.get(k)) for k in
                          ("ingredient_a", "ingredient_b", "prohibition_content"))
        if ia and ib and reason:
            entry = {"ingredients": sorted([ia, ib]), "reason": reason}
            if entry not in references:
                references.append(entry)

    limitations = []
    if raw.get("coverage") not in COMPLETE:
        limitations.append("검사 범위가 불완전하며 전체 상호작용을 확인한 결과는 아니에요.")
    if candidates or unverified_confirmed:
        limitations.append("별도로 남아 있는 미검토 규칙·매핑 후보는 확정 근거에 포함하지 않았어요.")
    if rejected:
        limitations.append("일부 제품 직접 근거는 원본 품목번호 대조 확인이 부족해 확정 설명에서 제외했어요.")

    if groups:
        status = "OFFICIAL_PRODUCT_EVIDENCE_FOUND"
    elif verified:
        status = "VERIFIED_RULE_MATCH"
    elif references or candidates:
        status = "REFERENCE_ONLY"
    elif raw.get("status") in {"UNRESOLVED_MAPPING", "PRODUCT_NOT_FOUND", "INVALID_PRODUCT"}:
        status = "UNRESOLVED_MAPPING"
    else:
        status = "NO_CONFIRMED_EVIDENCE"

    return {"product_ids": [a, b], "product_names": names,
            "assessment_status": status, "coverage": raw.get("coverage"),
            "limitations": unique(limitations), "evidence_groups": list(groups.values()),
            "verified_rules": verified, "references": references}


def evidence_sentences(assessment):
    names = assessment["product_names"]
    pair = f"{names[0]} / {names[1]}"
    sentences = []
    for group in assessment["evidence_groups"]:
        sentences.append(
            f"{pair}의 품목번호 조합에 대해 식약처 DUR 원본의 병용금기 기록이 확인됐어요. "
            f"원본에 기재된 사유: ‘{group['reason']}’.")
    for rule in assessment["verified_rules"]:
        reason = next((text(rule.get(k)) for k in ("evidence", "description", "reason")
                       if text(rule.get(k))), "")
        sentences.append(f"{pair}에서 검토·활성화 조건을 충족한 성분 규칙이 조회됐어요."
                         + (f" 규칙에 기재된 내용: ‘{reason}’." if reason else ""))
    return sentences


def present_pairs(public_pairs, coverage):
    """Return additive display fields; preserve all raw evidence and statuses."""
    assessments = [pair_assessment(p["ddi_result"], p["product_a"], p["product_b"])
                   for p in public_pairs]
    confirmed = [a for a in assessments if a["evidence_groups"] or a["verified_rules"]]
    limitations = unique([s for a in assessments for s in a["limitations"]])
    if coverage not in COMPLETE:
        limitations = unique(limitations + ["검사 범위가 불완전하며 전체 상호작용을 확인한 결과는 아니에요."])
    sentences = [s for a in confirmed for s in evidence_sentences(a)]
    if confirmed:
        missing = len(assessments) - len(confirmed)
        if missing:
            limitations.append(f"나머지 {missing}개 제품 조합에서는 확정 설명에 사용할 근거를 확인하지 못했으며, 안전하다는 뜻은 아니에요.")
    else:
        seen = set()
        for a in assessments:
            for ref in a["references"]:
                key = (tuple(ref["ingredients"]), ref["reason"])
                if key in seen:
                    continue
                seen.add(key)
                ia, ib = ref["ingredients"]
                sentences.append(f"식약처 DUR 성분 원본의 참고정보: {ia} / {ib} — ‘{ref['reason']}’.")
        if sentences:
            sentences.append("이 성분 참고정보가 선택한 제품에 적용되는지는 매핑·제형·투여경로 검토가 완료되지 않았어요.")
        else:
            sentences.append("현재 반환된 근거로는 선택한 제품의 상호작용을 확정할 수 없어요.")
        sentences.append("이는 상호작용이 없거나 병용 안전성이 확인됐다는 뜻은 아니에요.")
    if confirmed:
        sentences.append("이 결과는 개별 환자의 복용 가능 여부나 임상적 위험도를 확정한 판정은 아니에요.")
    answer = " ".join(unique(sentences + limitations))
    if confirmed:
        status = "EVIDENCE_FOUND_WITH_LIMITATIONS" if limitations else "EVIDENCE_FOUND"
    elif any(a["assessment_status"] == "REFERENCE_ONLY" for a in assessments):
        status = "REFERENCE_ONLY"
    elif any(a["assessment_status"] == "UNRESOLVED_MAPPING" for a in assessments):
        status = "UNRESOLVED_MAPPING"
    else:
        status = "NO_CONFIRMED_EVIDENCE"
    return {"answer": answer, "assessment_status": status,
            "limitations": unique(limitations), "pair_assessments": assessments,
            "presentation_version": VERSION}


def present_pair(raw, a, b):
    return present_pairs([{"product_a": a, "product_b": b, "ddi_result": raw}], raw.get("coverage"))
