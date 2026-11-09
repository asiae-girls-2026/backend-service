"""Scoped DDI approvals for existing product/DUR D-code data.

No clinical inference, automatic approval, or canonical ingredient requirement.
Legacy evidence is retained; confirmed_rules is rebuilt from current approvals.
"""
import contextvars
import functools
import hashlib
import json

CAPTURE = contextvars.ContextVar('ddi_review_capture', default=None)
IGNORED = {'review_status','active','rule_release_id','ddi_review_metadata','created_at','updated_at'}
TABLES = {
    'PRODUCT_RULE': 'interaction_rule',
    'PRODUCT_D': 'med_dur_product_mapping',
    'D_PAIR': 'med_dur_ingredient_rule_stage',
}


def rows(conn, sql, params=()):
    values = conn.execute(sql, params).fetchall()
    return [v['data'] if isinstance(v, dict) else v[0] for v in values]


def source_hash(source):
    core = {k:v for k,v in source.items() if k not in IGNORED}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), default=str).encode()).hexdigest()


def scope_key(pair):
    pair = sorted(int(v) for v in pair)
    if len(pair) != 2 or pair[0] <= 0 or pair[0] == pair[1]:
        raise ValueError('서로 다른 두 제품 ID가 필요합니다.')
    return ':'.join(map(str,pair))


def load_source(conn, kind, key, lock=False):
    if kind not in TABLES: raise ValueError('잘못된 kind')
    table = TABLES[kind]
    if kind == 'PRODUCT_RULE':
        condition, params = 't.interaction_rule_id=%s', (int(key),)
    elif kind == 'PRODUCT_D':
        product, code = key.split(':')
        condition, params = 't.med_product_id=%s AND t.dur_code=%s', (int(product),code)
    else:
        a,b = key.split(':')
        condition = "((t.dur_code_a=%s AND t.dur_code_b=%s) OR (t.dur_code_a=%s AND t.dur_code_b=%s)) AND t.rule_type='DDI'"
        params = (a,b,b,a)
    result = rows(conn, f'SELECT to_jsonb(t) AS data FROM public.{table} t WHERE {condition}' + (' FOR UPDATE' if lock else ''), params)
    if len(result) != 1:
        raise ValueError(f'{kind}/{key}: 대상이 정확히 1행이어야 합니다(현재 {len(result)}행).')
    return result[0]


def product_info(conn, pair):
    result = rows(conn, '''SELECT jsonb_build_object('id',med_product_id,
        'item_seq',item_seq,'name',product_name) AS data FROM public.med_product
        WHERE med_product_id=ANY(%s)''', (list(pair),))
    if len(result) != 2: raise ValueError('대상 제품을 찾지 못했습니다.')
    return {str(x['id']):x for x in result}


def reasons(source):
    found = []
    def visit(value):
        if isinstance(value,str):
            try: value=json.loads(value)
            except (ValueError,TypeError): return
        if isinstance(value,dict):
            for k,v in value.items():
                if k.upper() in ('PROHBT_CONTENT','PROHIBITION_CONTENT','PROHIBITION_REASON') and isinstance(v,str) and v.strip():
                    if v.strip() not in found: found.append(v.strip())
                elif isinstance(v,(dict,list,str)): visit(v)
        elif isinstance(value,list):
            for v in value: visit(v)
    visit(source)
    return found


def eligible(approval, source, identities):
    return (approval.get('lifecycle')=='ACTIVE'
        and approval.get('release_status')=='ACTIVE'
        and approval.get('source_hash')==source_hash(source)
        and approval.get('product_snapshot')==identities)


def approved_for_pair(conn, a, b):
    pair = sorted([int(a),int(b)])
    key = scope_key(pair)
    identities = product_info(conn,pair)
    approvals = rows(conn, '''SELECT to_jsonb(a)||jsonb_build_object(
        'release_status',r.status,'release_code',r.release_code) AS data
        FROM public.ddi_scoped_approval a JOIN public.rule_release r
        ON r.rule_release_id=a.rule_release_id
        WHERE a.scope_pair=%s AND a.lifecycle='ACTIVE' AND r.status='ACTIVE' ''',(key,))
    valid = []
    for approval in approvals:
        # Missing or changed source rows invalidate approvals; DB errors propagate.
        try: source=load_source(conn,approval['target_type'],approval['target_key'])
        except ValueError: continue
        if not eligible(approval,source,identities): continue
        if source.get('review_status')!='APPROVED': continue
        if approval['target_type'] in ('PRODUCT_RULE','D_PAIR') and source.get('active') is not True: continue
        if approval['target_type']=='PRODUCT_RULE' and source.get('rule_release_id')!=approval['rule_release_id']: continue
        valid.append((approval,source))
    active_maps = {(int(s['med_product_id']),s['dur_code']):r for r,s in valid if r['target_type']=='PRODUCT_D'}
    matches = []
    for approval,source in valid:
        kind=approval['target_type']; mapping_ids=[]
        if kind=='PRODUCT_RULE':
            if source.get('interaction_type')!='DDI' or source.get('subject_type')!='PRODUCT' or source.get('object_type')!='PRODUCT': continue
            if sorted([source['subject_id'],source['object_id']])!=pair: continue
        elif kind=='D_PAIR':
            da,db=source['dur_code_a'],source['dur_code_b']
            forward=((pair[0],da),(pair[1],db))
            reverse=((pair[0],db),(pair[1],da))
            links=next((links for links in (forward,reverse) if all(x in active_maps for x in links)),None)
            if links is None: continue
            mapping_ids=[active_maps[x]['approval_id'] for x in links]
        else: continue
        matches.append({
            'type':'PRODUCT_RULE' if kind=='PRODUCT_RULE' else 'INGREDIENT_DUR_RULE',
            'approval_id':approval['approval_id'],
            'interaction_rule_id':source.get('interaction_rule_id'),
            'dur_codes': [source['dur_code_a'],source['dur_code_b']] if kind=='D_PAIR' else [],
            'product_ids':pair,'product_names':[identities[str(x)]['name'] for x in pair],
            'review_status':'APPROVED','active':True,'scope_verified':True,
            'source_unchanged':True,'rule_release_id':approval['rule_release_id'],
            'release_code':approval['release_code'],'release_status':'ACTIVE',
            'applied_scope':{'type':'EXACT_PRODUCT_PAIR','product_ids':pair},
            'mapping_approval_ids':mapping_ids,'evidence_ref':approval['evidence_ref'],
            'prohibition_reasons':reasons(source), 'clinical_assessment':False,
        })
    return matches


def wrap_engine(original):
    @functools.wraps(original)
    def checked(conn,product_a,product_b,*args,**kwargs):
        result=original(conn,product_a,product_b,*args,**kwargs)
        if not isinstance(result,dict): raise TypeError('DDI 결과가 dict가 아닙니다.')
        result=dict(result)
        policy_row=conn.execute(
            'SELECT public.ddi_evaluate_mapping_policy(%s,%s)',
            (product_a,product_b)
        ).fetchone()
        if not policy_row or not isinstance(policy_row[0],dict):
            raise RuntimeError('DDI_MAPPING_POLICY_UNAVAILABLE')
        policy=policy_row[0]
        action=policy.get('action_code')
        if not action:
            raise RuntimeError('DDI_MAPPING_POLICY_NOT_FOUND')

        result['mapping_policy']=policy
        result['mapping_action']=action
        result['mapping_resolution_required']=action in (
            'RESOLVE_M_TO_D',
            'RESOLVE_D_CODE',
            'RESOLVE_PRODUCT_MAPPING',
            'INSUFFICIENT_EVIDENCE'
        )
        confirmed=approved_for_pair(conn,product_a,product_b)
        result['confirmed_rules']=confirmed
        result['approved_rule_count']=len(confirmed)
        result['approval_contract_version']='ddi-scoped-v2'
        if confirmed:
            result['status']='MATCHED_RULE'
            result['message']='승인된 제품쌍 적용 범위와 활성 릴리스에 일치하는 규칙이 있습니다.'
        elif result.get('status')=='MATCHED_RULE':
            result['status']='UNCERTAIN'
        # Do not infer complete coverage from a single approved match.
        result['coverage']='INCOMPLETE'
        capture=CAPTURE.get()
        if capture is not None:
            pair=sorted([int(product_a),int(product_b)])
            identities=product_info(conn,pair)
            capture[scope_key(pair)]={'product_ids':pair,
                'product_names':[identities[str(x)]['name'] for x in pair],'ddi_result':result}
        return result
    return checked


def approved_answer(captured):
    sentences=[]
    for record in captured.values():
        matches=record['ddi_result'].get('confirmed_rules') or []
        if not matches:
            official=record['ddi_result'].get('official_product_evidence') or []
            if official:
                names=record['product_names']
                sentences.append(f'{names[0]}와 {names[1]}는 직접 DUR 품목 근거가 있으나 서비스 승인 규칙과는 구분됩니다.')
                original=[]
                for evidence in official:
                    reason=evidence.get('prohibition_reason')
                    if reason and reason not in original:original.append(reason)
                if original:sentences.append('품목 원천 주의내용: '+' / '.join(original))
            pending=[]
            for candidate in record['ddi_result'].get('candidate_rules') or []:
                for reason in reasons(candidate):
                    if reason not in pending:pending.append(reason)
            if pending:
                names=record['product_names']
                sentences.append(f'{names[0]}·{names[1]} 관련 미검토 참고 원문(해당 제품쌍의 적용 확정 아님): '+' / '.join(pending))
            continue
        names=matches[0]['product_names']
        types=[]
        if any(m['type']=='PRODUCT_RULE' for m in matches): types.append('제품 규칙')
        if any(m['type']=='INGREDIENT_DUR_RULE' for m in matches): types.append('D코드 성분쌍 규칙')
        sentences.append(f"{names[0]}와 {names[1]}는 승인된 해당 제품쌍 적용 범위에서 {'·'.join(types)}에 일치합니다.")
        evidence=[]
        for m in matches:
            for reason in m['prohibition_reasons']:
                if reason not in evidence: evidence.append(reason)
        if evidence: sentences.append('원천 주의내용: '+' / '.join(evidence))
    if sentences:
        sentences.append('미검토 후보는 승인 근거에 포함하지 않았으며, 전체 검사 범위는 아직 불완전합니다.')
    return ' '.join(sentences)


def wrap_handler(original):
    @functools.wraps(original)
    def reviewed(event,context):
        mode=event.get('mode','ddi_check')
        if mode not in ('ddi_check','ddi_chat','ddi_multi_check','ddi_multi_chat'):
            return original(event,context)
        token=CAPTURE.set({})
        try:
            response=original(event,context)
            captured=CAPTURE.get()
            if not captured: raise RuntimeError('DDI_REVIEW_PATH_NOT_REACHED: 배포 경로에서 check_medications가 실행되지 않았습니다.')
            if not isinstance(response,dict) or 'body' in response:
                raise RuntimeError('DDI_REVIEW_RESPONSE_SHAPE_UNSUPPORTED')
            response=dict(response)
            matches=[m for v in captured.values() for m in v['ddi_result'].get('confirmed_rules',[])]
            response['ddi_review']={'contract_version':'ddi-scoped-v2','confirmed_rule_count':len(matches),
                'pairs':list(captured.values()),'coverage':'INCOMPLETE'}
            # Update existing nested pair structures too, so stale summaries cannot disagree.
            for pair in response.get('pair_results',[]):
                if isinstance(pair,dict) and 'product_a' in pair and 'product_b' in pair:
                    k=scope_key([pair['product_a'],pair['product_b']])
                    if k in captured:
                        pair['ddi_result']=captured[k]['ddi_result']
                        pair['status']=pair['ddi_result']['status']
                        pair['coverage']='INCOMPLETE'
                        pair['assessment']={'assessment_status':'APPROVED_RULE_MATCH' if pair['ddi_result']['confirmed_rules'] else 'NO_APPROVED_RULE_MATCH',
                            'confirmed_rule_count':len(pair['ddi_result']['confirmed_rules']),'coverage':'INCOMPLETE'}
            if 'ddi_result' in response and len(captured)==1:
                response['ddi_result']=next(iter(captured.values()))['ddi_result']
                response['ddi_status']=response['ddi_result']['status']
            if 'evidence_summary' in response and isinstance(response['evidence_summary'],dict):
                summary=response['evidence_summary']
                summary['verified_ingredient_rule_count']=sum(m['type']=='INGREDIENT_DUR_RULE' for m in matches)
                summary['approved_product_rule_count']=sum(m['type']=='PRODUCT_RULE' for m in matches)
            response['coverage']='INCOMPLETE'
            if matches:
                response['assessment_status']='APPROVED_RULE_MATCH'
                response['answer']=approved_answer(captured)
                response['answer_source']='DETERMINISTIC_APPROVED_EVIDENCE'
                if 'message' in response: response['message']=response['answer']
            # Keep top-level per-pair assessments consistent with pair_results.
            if isinstance(response.get('pair_results'),list) and 'pair_assessments' in response:
                response['pair_assessments']=[p['assessment'] for p in response['pair_results'] if isinstance(p,dict) and 'assessment' in p]
            return response
        finally:
            CAPTURE.reset(token)
    return reviewed
