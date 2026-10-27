"""Read-only DDI rule matching against existing interaction_rule.

Only ACTIVE + APPROVED product and ingredient rules produce a confirmed match.
REVIEW rules are never reported as contraindications. Unknown coverage is not safety.
"""
from itertools import combinations


def check_ddi(conn, product_ids):
    ids = sorted({int(v) for v in product_ids})
    if len(ids) < 2:
        return {'status': 'INSUFFICIENT_PRODUCTS', 'matches': [],
                'unresolved_products': ids, 'coverage': 'INCOMPLETE'}
    if len(ids) > 50:
        raise ValueError('At most 50 products per check')
    rows = conn.execute('''SELECT med_product_id FROM public.med_product
                           WHERE med_product_id = ANY(%s)''', (ids,)).fetchall()
    found = {int(row[0]) for row in rows}
    unresolved = sorted(set(ids) - found)
    known = sorted(found)
    ingredient_rows = conn.execute('''SELECT med_product_id, ingredient_id
       FROM public.med_product_ingredient
       WHERE med_product_id = ANY(%s) AND ingredient_id IS NOT NULL
         AND mapping_status = 'APPROVED' ''', (known,)).fetchall()
    ingredients = {}
    for pid, iid in ingredient_rows:
        ingredients.setdefault(int(pid), set()).add(int(iid))

    # Lookup all active product/ingredient rules at once, bounded by the request.
    flat_ingredients = sorted(set().union(*ingredients.values())) if ingredients else []
    rules = conn.execute('''SELECT interaction_rule_id, subject_type,subject_id,
           object_type,object_id,interaction_type,severity,action_type,
           evidence_text,source_dataset,source_record_id, target_text
       FROM public.interaction_rule
       WHERE active = true AND review_status = 'APPROVED'
         AND ((subject_type='PRODUCT' AND object_type='PRODUCT'
               AND subject_id = ANY(%s) AND object_id = ANY(%s))
           OR (subject_type='INGREDIENT' AND object_type='INGREDIENT'
               AND subject_id = ANY(%s) AND object_id = ANY(%s)))''',
       (known,known,flat_ingredients,flat_ingredients)).fetchall()
    matches = {}
    for a, b in combinations(known, 2):
        aa, bb = ingredients.get(a, set()), ingredients.get(b, set())
        for row in rules:
            rid, stype, sid, otype, oid, itype, severity, action, ev, src, rec, detail = row
            if stype == 'PRODUCT':
                applicable = (sid == a and oid == b) or (sid == b and oid == a)
            else:
                applicable = (sid in aa and oid in bb) or (sid in bb and oid in aa)
            if applicable:
                matches[(a,b,rid)] = {'product_ids':[a,b], 'rule_id':rid,
                   'interaction_type':itype,'severity':severity,'action_type':action,
                   'evidence_text':ev,'source_dataset':src,'source_record_id':rec,
                   'target_text':detail}
    # Product ingredients still REVIEW => incomplete. Even all approved does not
    # prove a medication pair safe in the real world.
    incomplete = bool(unresolved)
    counts = dict(conn.execute('''SELECT med_product_id, COUNT(*)
      FROM public.med_product_ingredient WHERE med_product_id = ANY(%s)
      GROUP BY med_product_id''', (known,)).fetchall())
    approved_counts = {k: len(v) for k,v in ingredients.items()}
    for pid in known:
        if not counts.get(pid) or approved_counts.get(pid, 0) < counts[pid]:
            incomplete = True
    return {'status': 'MATCHED_RULE' if matches else 'NO_MATCH_IN_AVAILABLE_RULES',
            'coverage': 'INCOMPLETE' if incomplete else 'LIMITED_TO_PUBLISHED_DUR_RULES',
            'matches': list(matches.values()),
            'unresolved_products': unresolved,
            'message': 'No matched rule does not establish combination safety.'}
