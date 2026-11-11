"""Deterministic DDI presentation; does not approve or modify source rules."""
VERSION = '20261111.7'
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
        limitations.append("일부 상호작용은 확인하지 못했어요.")
    if candidates or unverified_confirmed:
        limitations.append("아직 확인이 끝나지 않은 자료는 이번 결과에 포함하지 않았어요.")
    if rejected:
        limitations.append("선택한 약에 해당하는지 확인되지 않은 자료는 결과에서 제외했어요.")

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
    names = assessment['product_names']
    pair = f'{names[0]}와 {names[1]}'
    sentences = []
    for group in assessment['evidence_groups']:
        sentences.append(f'{pair}를 함께 복용하지 않도록 안내한 식약처 자료가 확인됐어요. '
                         f"자료에 적힌 이유: ‘{group['reason']}’.")
    for rule in assessment['verified_rules']:
        reasons = [text(x) for x in rule.get('prohibition_reasons', [])
                   if isinstance(x, str) and text(x)]
        if not reasons:
            reasons = [text(rule[k]) for k in ('evidence', 'description', 'reason')
                       if isinstance(rule.get(k), str) and text(rule[k])][:1]
        sentence = f'{pair}를 함께 복용할 때 주의할 내용을 확인했어요.'
        if reasons:
            sentence += ' 자료에 적힌 내용: ' + ' / '.join(f'‘{reason}’' for reason in unique(reasons)) + '.'
        sentences.append(sentence)
    return unique(sentences)


def present_pairs(public_pairs, coverage):
    """Render plain Korean; retain evidence selection and assessment status policy."""
    assessments = [pair_assessment(p['ddi_result'], p['product_a'], p['product_b'])
                   for p in public_pairs]
    confirmed = [a for a in assessments if a['evidence_groups'] or a['verified_rules']]
    limitations = unique([s for a in assessments for s in a['limitations']])
    if coverage not in COMPLETE:
        limitations = unique(limitations + ['일부 상호작용은 확인하지 못했어요.'])
    sentences = [s for a in confirmed for s in evidence_sentences(a)]
    unresolved = [a for a in assessments if not (a['evidence_groups'] or a['verified_rules'])]
    for assessment in unresolved:
        names = assessment['product_names']
        pair = f'{names[0]}와 {names[1]}'
        if assessment['assessment_status'] == 'UNRESOLVED_MAPPING':
            sentences.append(f'{pair}는 제품·성분 정보가 충분히 연결되지 않아 이번 상호작용 확인이 제한됐어요.')
        elif assessment['assessment_status'] == 'REFERENCE_ONLY':
            sentences.append(f'{pair}에 관련된 참고자료가 있지만, 선택한 두 약에 적용되는 주의 정보인지는 아직 확인되지 않았어요.')
        else:
            sentences.append(f'{pair}의 상호작용 주의 정보는 케어퐁이 이번에 확인 가능한 자료에서 확인되지 않았어요.')
    if unresolved:
        sentences.append('확인 범위가 제한되어 함께 복용해도 안전하다고 확정할 수는 없어요.')
        if any(a['assessment_status'] == 'UNRESOLVED_MAPPING' for a in unresolved):
            sentences.append('선택한 제품명과 함량이 실제 복용하는 약과 일치하는지 확인해 주세요.')
    if confirmed:
        missing = len(assessments) - len(confirmed)
        if missing:
            limitations.append(f'나머지 {missing}개 약 조합의 상호작용은 확정하지 못했어요. 안전하다는 뜻은 아니에요.')
        sentences.append('이 결과만으로 개인의 복용 가능 여부를 결정할 수는 없어요.')
    if not assessments:
        sentences.append('검사할 약의 정보를 확인하지 못했어요. 안전하다는 뜻은 아니에요.')
    if unresolved or not assessments:
        sentences.append('추가 확인은 건강보험심사평가원 ‘DUR 국민 체험관’에서 하거나, 약사에게 두 약의 정확한 제품명을 알려 문의해 주세요.')
    answer = ' '.join(unique(sentences + limitations))
    if confirmed:
        status = 'EVIDENCE_FOUND_WITH_LIMITATIONS' if limitations else 'EVIDENCE_FOUND'
    elif any(a['assessment_status'] == 'REFERENCE_ONLY' for a in assessments):
        status = 'REFERENCE_ONLY'
    elif any(a['assessment_status'] == 'UNRESOLVED_MAPPING' for a in assessments):
        status = 'UNRESOLVED_MAPPING'
    else:
        status = 'NO_CONFIRMED_EVIDENCE'
    result = {'answer': answer, 'assessment_status': status,
            'limitations': unique(limitations), 'pair_assessments': assessments,
            'presentation_version': VERSION}
    result['ddi_sections'] = build_ddi_sections(result)
    result['ddi_sections_version'] = 1
    return result


def present_pair(raw, a, b):
    return present_pairs([{"product_a": a, "product_b": b, "ddi_result": raw}], raw.get("coverage"))


def build_ddi_sections(presentation):
    """Display-only sections built from the same verified assessments as answer."""
    assessments = presentation['pair_assessments']
    sections = []
    confirmed = []
    unresolved = []
    for assessment in assessments:
        names = assessment['product_names']
        pair = f'{names[0]}와 {names[1]}'
        metadata = {'product_ids': assessment['product_ids'], 'product_names': names}
        if assessment['evidence_groups'] or assessment['verified_rules']:
            confirmed.append(assessment)
            items = [group['reason'] for group in assessment['evidence_groups']]
            for rule in assessment['verified_rules']:
                reasons = [text(x) for x in rule.get('prohibition_reasons', [])
                           if isinstance(x, str) and text(x)]
                if not reasons:
                    reasons = [text(rule[k]) for k in ('evidence', 'description', 'reason')
                               if isinstance(rule.get(k), str) and text(rule[k])][:1]
                items.extend(reasons or ['함께 복용할 때 주의할 내용이 확인됐어요.'])
            sections.append(dict(metadata, type='EVIDENCE', title='확인된 내용',
                                 items=unique(items)))
        else:
            unresolved.append(assessment)
            status = assessment['assessment_status']
            if status == 'UNRESOLVED_MAPPING':
                message = f'{pair}는 제품·성분 정보가 충분히 연결되지 않아 이번 상호작용 확인이 제한됐어요.'
            elif status == 'REFERENCE_ONLY':
                message = f'{pair}에 관련된 참고자료가 있지만, 선택한 두 약에 적용되는 주의 정보인지는 아직 확인되지 않았어요.'
            else:
                message = f'{pair}의 상호작용 주의 정보는 케어퐁이 이번에 확인 가능한 자료에서 확인되지 않았어요.'
            sections.append(dict(metadata, type='RESULT', title='검사 결과', items=[message]))
    caution = []
    if confirmed:
        caution.append('이 결과만으로 개인의 복용 가능 여부를 결정할 수는 없어요.')
    if unresolved:
        caution.append('확인 범위가 제한되어 함께 복용해도 안전하다고 확정할 수는 없어요.')
        if any(a['assessment_status'] == 'UNRESOLVED_MAPPING' for a in unresolved):
            caution.append('선택한 제품명과 함량이 실제 복용하는 약과 일치하는지 확인해 주세요.')
    if not assessments:
        sections.append({'type': 'RESULT', 'title': '검사 결과',
                         'items': ['검사할 약의 정보를 확인하지 못했어요.']})
        caution.append('확인 범위가 제한되어 함께 복용해도 안전하다고 확정할 수는 없어요.')
    caution.extend(presentation['limitations'])
    if unresolved or not assessments:
        caution.append('추가 확인은 건강보험심사평가원 ‘DUR 국민 체험관’에서 하거나, 약사에게 두 약의 정확한 제품명을 알려 문의해 주세요.')
    if caution:
        sections.append({'type': 'CAUTION', 'title': '주의', 'items': unique(caution)})
    return sections
