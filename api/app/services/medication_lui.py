"""CarePong medication LUI orchestration. No empathy-chat modifications."""
from __future__ import annotations
import json
import os
import re
import uuid
import logging
from datetime import timezone, timedelta
from typing import Any

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from . import db
from ..routers.catalog import search_catalog
from ..routers.medications_live import MedicationRegisterIn, register_user_medication, list_user_medications

log = logging.getLogger('carepong.medication_lui')
KST = timezone(timedelta(hours=9))
INTENTS = {'DDI_CHECK','MY_MEDICATIONS','MY_MEDICATIONS_DDI','REMINDER_CREATE','MEDICATION_INFO','UNKNOWN'}
FUNCTION_NAME = os.getenv('MEDICATION_LAMBDA_NAME', 'carepong-medication-orchestrator')
MODEL_ID = os.getenv('MEDICATION_LUI_MODEL_ID', 'apac.amazon.nova-lite-v1:0')
REGION = os.getenv('AWS_REGION', 'ap-northeast-2')


def _runtime():
    import boto3
    return boto3.client('bedrock-runtime', region_name=REGION)


def _lambda():
    import boto3
    return boto3.client('lambda', region_name=REGION)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I).strip()
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError('JSON object expected')
    return obj


def analyze(message: str, previous_intent: str | None = None) -> dict:
    """LLM suggests structured slots. Server never treats them as authority."""
    prompt = ("한국어 의약품 챗봇의 의도/슬롯 분류기다. JSON 객체만 출력. "
              "intent는 DDI_CHECK, MY_MEDICATIONS, MY_MEDICATIONS_DDI, REMINDER_CREATE, "
              "MEDICATION_INFO, UNKNOWN 중 하나. "
              "drug_names는 명시적으로 등장한 제품명 문자열 배열(추측 금지). "
              "time은 확실한 시각일 때만 HH:MM, 아니면 null. "
              "repeat_days는 '매일'이면 [0,1,2,3,4,5,6], '평일'이면 [0,1,2,3,4], "
              "요일이 불명확하면 null. 0=월요일, 6=일요일. "
              "dose_text는 명시된 용량만, 없으면 null. "
              "등록약 목록을 요청하면 MY_MEDICATIONS, 등록약 병용검사면 MY_MEDICATIONS_DDI. "
              "모호한 '저녁', '아침', '식후'는 시각으로 변환하지 말 것. "
              "요청에서 의도 추정할 수 없으면 UNKNOWN. "
              "결과 형식: {\"intent\":\"...\",\"drug_names\":[],\"time\":null,\"repeat_days\":null,\"dose_text\":null}. "
              f"직전 의도:{previous_intent or '없음'}\n사용자 발화:{message}")
    try:
        res = _runtime().converse(modelId=MODEL_ID,
            messages=[{'role':'user','content':[{'text':prompt}]}],
            inferenceConfig={'maxTokens':350,'temperature':0})
        text = ''.join(c.get('text','') for c in res['output']['message']['content'])
        out = _extract_json(text)
    except Exception as exc:
        log.warning('Medication intent analysis unavailable: %s', type(exc).__name__)
        raise HTTPException(503, 'Natural-language analysis unavailable') from exc
    intent = out.get('intent', 'UNKNOWN')
    if intent not in INTENTS:
        intent = 'UNKNOWN'
    names = out.get('drug_names')
    names = names if isinstance(names, list) else []
    names = [str(n).strip()[:100] for n in names if isinstance(n,str) and n.strip()][:5]
    raw_time = out.get('time')
    time = raw_time if isinstance(raw_time,str) and re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',raw_time) else None
    days = out.get('repeat_days')
    if not (isinstance(days,list) and days and all(type(d) is int and 0 <= d <= 6 for d in days) and len(days) == len(set(days))):
        days = None
    dose = out.get('dose_text')
    return {'intent':intent,'drug_names':names,'time':time,'repeat_days':days,
            'dose_text':dose[:200] if isinstance(dose,str) else None}


def _search(name: str, item_type: str | None = 'DRUG') -> list[dict]:
    result = search_catalog(q=name, item_type=item_type, limit=3)
    return [{'product_id':i.product_id,'product_name':i.product_name,
             'manufacturer_name':i.manufacturer_name,'item_type':i.item_type} for i in result.items]


def _session(user_id: str, session_id: str, lock: bool = False) -> dict:
    suffix = ' FOR UPDATE' if lock else ''
    with db.conn() as c:
        row = c.execute('SELECT state FROM medication_lui_session WHERE session_id=%s AND user_id=%s AND expires_at>now()'+suffix,
                        (session_id,user_id)).fetchone()
        if row is None:
            raise HTTPException(404,'Conversation not found or expired')
        return row[0]


def _save(user_id: str, session_id: str, state: dict):
    with db.conn() as c:
        changed = c.execute('UPDATE medication_lui_session SET state=%s, updated_at=now() WHERE session_id=%s AND user_id=%s AND expires_at>now() RETURNING session_id',
                            (Jsonb(state),session_id,user_id)).fetchone()
        if not changed:
            raise HTTPException(404,'Conversation not found or expired')


def _new_session(user_id: str, state: dict) -> str:
    sid = str(uuid.uuid4())
    with db.conn() as c:
        c.execute("INSERT INTO medication_lui_session(session_id,user_id,state,expires_at) VALUES (%s,%s,%s,now()+interval '30 minutes')",
                  (sid,user_id,Jsonb(state)))
    return sid


def _reply(sid: str, state: dict, message: str, options: list | None = None, answer: str | None = None, **extra):
    data = {'conversation_id':sid,'status':state['status'],'intent':state['intent'],
            'response_type':'options' if options is not None else 'text',
            'message':message,'options':options or []}
    if answer is not None:
        data['answer']=answer
    data.update(extra)
    return data


def _pending_response(sid: str, state: dict):
    pending = state.get('pending', [])
    if pending:
        entry = pending[0]
        if not entry['options']:
            return _reply(sid,state, f"'{entry['query']}' 검색 결과가 없습니다. 정확한 제품명으로 다시 입력해주세요.")
        opts = [{'id':str(i['product_id']), 'label':i['product_name'], 'description':i.get('manufacturer_name') or ''}
                for i in entry['options']]
        return _reply(sid,state, f"'{entry['query']}'의 제품을 선택해주세요.", options=opts)
    if state['intent'] in {'DDI_CHECK','MY_MEDICATIONS_DDI'}:
        if not 2 <= len(state['products']) <= 5:
            return _reply(sid,state,'검사할 의약품을 2~5개 선택해 주세요.')
        state['status']='READY_FOR_CONFIRMATION'
        _save(state['user_id'],sid,state)
        opts=[{'id':'confirm','label':'DDI 검사 실행'},{'id':'cancel','label':'취소'}]
        names=', '.join(i['product_name'] for i in state['products'])
        return _reply(sid,state,f'선택한 의약품 {names}의 상호작용을 검사할까요?',options=opts)
    if state['intent']=='REMINDER_CREATE':
        if not state['products']:
            return _reply(sid,state,'등록할 의약품을 선택해주세요.')
        if not state.get('time'):
            state['status']='NEED_TIME'
            _save(state['user_id'],sid,state)
            return _reply(sid,state,'몇 시에 알림을 받을까요? 정확한 시간(예: 오후 8시)을 입력해주세요.')
        if state.get('repeat_days') is None:
            state['status']='NEED_DAYS'
            _save(state['user_id'],sid,state)
            return _reply(sid,state,'반복 요일을 알려주세요. 예: 매일, 평일, 월요일과 수요일')
        state['status']='READY_FOR_CONFIRMATION'
        _save(state['user_id'],sid,state)
        return _reply(sid,state,f"{state['products'][0]['product_name']}을(를) {state['time']}에 등록할까요?",
                      options=[{'id':'confirm','label':'알림 등록'},{'id':'cancel','label':'취소'}],
                      schedule={'times':[state['time']],'repeat_days':state['repeat_days'],
                                'dose_text':state.get('dose_text')})
    if state['intent']=='MEDICATION_INFO':
        state['status']='COMPLETED'
        _save(state['user_id'],sid,state)
        p=state['products'][0] if state['products'] else {}
        return _reply(sid,state,'제품 식별이 완료되었습니다. 상세 효능·용법 데이터 연동은 아직 제공하지 않습니다.',
                      product=p)
    return _reply(sid,state,'다시 질문해주세요.')


def _my_drugs(user_id: str) -> list[dict]:
    if not db.available():
        raise HTTPException(503,'Database unavailable')
    with db.conn() as c:
        rows = c.execute('''SELECT um.user_medication_id, um.med_product_id, mp.product_name
                            FROM user_medication um JOIN med_product mp ON mp.med_product_id=um.med_product_id
                            WHERE um.user_id=%s AND um.active=TRUE AND um.item_type='DRUG'
                            ORDER BY um.user_medication_id''',(user_id,)).fetchall()
    return [{'user_medication_id':r[0],'product_id':r[1],'product_name':r[2],'item_type':'DRUG'} for r in rows]


def message(user_id: str, text: str, conversation_id: str | None = None) -> dict:
    if not db.available():
        raise HTTPException(503,'Database unavailable')
    prev = _session(user_id,conversation_id) if conversation_id else None
    if prev and prev['status'] in {'NEED_TIME','NEED_DAYS'}:
        slots=analyze(text, prev['intent'])
        if prev['status']=='NEED_TIME' and slots['time']:
            prev['time']=slots['time']
        if prev['status']=='NEED_DAYS' and slots['repeat_days'] is not None:
            prev['repeat_days']=slots['repeat_days']
        if slots['time']:
            prev['time']=slots['time']
        if slots['repeat_days'] is not None:
            prev['repeat_days']=slots['repeat_days']
        _save(user_id,conversation_id,prev)
        return _pending_response(conversation_id,prev)
    if prev and prev['status'] in {'NEED_PRODUCT_NAME','SELECTING_PRODUCTS'}:
        # Search again if no candidate was found, or collect the next drug.
        if prev.get('pending') and prev['pending'][0]['options']:
            raise HTTPException(409,'Choose a displayed product or cancel first')
        slots = analyze(text, prev['intent'])
        names = slots['drug_names']
        if not names:
            # Many follow-up messages are just product names.
            names = [text.strip()[:100]]
        if prev['intent'] in {'REMINDER_CREATE','MEDICATION_INFO'}:
            names = names[:1]
        if prev.get('pending'):
            prev['pending'].pop(0)
        for name in names[:max(0,5-len(prev['products']))]:
            prev['pending'].append({'query':name,'options':_search(name, None if prev['intent']=='REMINDER_CREATE' else 'DRUG')})
        prev['status']='SELECTING_PRODUCTS'
        _save(user_id,conversation_id,prev)
        return _pending_response(conversation_id,prev)
    if prev and prev['status'] not in {'COMPLETED','CANCELLED'}:
        raise HTTPException(409,'Complete or cancel the current conversation first')
    slots=analyze(text)
    intent=slots['intent']
    if intent=='UNKNOWN':
        return {'conversation_id':None,'status':'NEED_INTENT','intent':intent,'response_type':'text',
                'message':'약 상호작용 검사, 등록약 조회, 복약 알림 등록 중 어떤 도움이 필요하신가요?','options':[]}
    state={'user_id':user_id,'intent':intent,'status':'SELECTING_PRODUCTS','products':[], 'pending':[],
           'message':text[:1000],'time':slots['time'],'repeat_days':slots['repeat_days'],
           'dose_text':slots['dose_text'],'client_medication_id':str(uuid.uuid4())}
    if intent in {'MY_MEDICATIONS','MY_MEDICATIONS_DDI'}:
        registered=_my_drugs(user_id)
        if intent=='MY_MEDICATIONS':
            state['status']='COMPLETED'
            sid=_new_session(user_id,state)
            # Include both DRUG and HFF from the existing registration API.
            all_registered=list_user_medications(user_id=user_id)
            return _reply(sid,state,'현재 등록된 복약·건기식 목록입니다.', medications=all_registered.get('items',[]))
        state['registered']=registered
        state['status']='SELECTING_REGISTERED'
        sid=_new_session(user_id,state)
        if len(registered)<2:
            return _reply(sid,state,'등록된 의약품이 2개 미만이어서 DDI 검사를 시작할 수 없습니다.')
        return _reply(sid,state,'검사할 등록 의약품을 2~5개 선택한 뒤 검사 대상을 확정해주세요.',
                      options=[{'id':str(x['user_medication_id']),'label':x['product_name']} for x in registered])
    names=slots['drug_names']
    if not names:
        state['status']='NEED_PRODUCT_NAME'
        sid=_new_session(user_id,state)
        return _reply(sid,state,'확인할 제품명을 알려주세요.')
    if intent=='REMINDER_CREATE' or intent=='MEDICATION_INFO':
        names=names[:1]
    for name in names:
        state['pending'].append({'query':name,'options':_search(name, None if intent=='REMINDER_CREATE' else 'DRUG')})
    sid=_new_session(user_id,state)
    return _pending_response(sid,state)


def select(user_id: str, sid: str, product_id: int | None, registered_id: int | None) -> dict:
    state=_session(user_id,sid)
    if state['status']=='SELECTING_REGISTERED':
        if registered_id is None:
            raise HTTPException(422,'user_medication_id required')
        match=next((x for x in state.get('registered',[]) if x['user_medication_id']==registered_id),None)
        if match is None:
            raise HTTPException(403,'Not a candidate of this user session')
        if any(x['product_id']==match['product_id'] for x in state['products']):
            raise HTTPException(409,'Already selected')
        if len(state['products'])>=5:
            raise HTTPException(409,'Maximum five products')
        state['products'].append(match)
        _save(user_id,sid,state)
        return _reply(sid,state,f"{match['product_name']} 선택됨 ({len(state['products'])}개).",
                      options=[{'id':str(x['user_medication_id']),'label':x['product_name']}
                               for x in state['registered'] if not any(y['product_id']==x['product_id'] for y in state['products'])],
                      selected_products=state['products'], can_confirm=len(state['products'])>=2)
    if state['status'] not in {'SELECTING_PRODUCTS','NEED_PRODUCT_NAME'}:
        raise HTTPException(409,'Product selection is not active')
    if product_id is None or not state['pending']:
        raise HTTPException(422,'product_id required')
    current=state['pending'][0]
    picked=next((x for x in current['options'] if x['product_id']==product_id),None)
    if picked is None:
        raise HTTPException(403,'Product is not in offered candidates')
    if any(x['product_id']==product_id for x in state['products']):
        raise HTTPException(409,'Already selected')
    state['products'].append(picked)
    state['pending'].pop(0)
    _save(user_id,sid,state)
    return _pending_response(sid,state)


def confirm(user_id: str, sid: str) -> dict:
    state=_session(user_id,sid)
    if state['status']=='SELECTING_REGISTERED':
        if not 2 <= len(state['products']) <= 5:
            raise HTTPException(409,'Select 2-5 registered medications')
        # Re-check ownership/current active status immediately before executing
        latest={x['product_id'] for x in _my_drugs(user_id)}
        if not all(x['product_id'] in latest for x in state['products']):
            raise HTTPException(409,'Registered medications changed')
        state['status']='READY_FOR_CONFIRMATION'
    if state['status']!='READY_FOR_CONFIRMATION':
        raise HTTPException(409,'Conversation not ready to confirm')
    intent=state['intent']
    if intent=='REMINDER_CREATE' and (len(state['products'])!=1 or not state.get('time') or state.get('repeat_days') is None):
        raise HTTPException(409,'Product/time/days not confirmed')
    # Atomic claim: only one request can execute this session confirmation.
    with db.conn() as c:
        claimed=c.execute('''UPDATE medication_lui_session
             SET state=jsonb_set(state, '{status}', '"EXECUTING"'::jsonb), updated_at=now()
             WHERE session_id=%s AND user_id=%s AND expires_at>now()
             AND state->>'status' IN ('READY_FOR_CONFIRMATION','SELECTING_REGISTERED')
             RETURNING session_id''',(sid,user_id)).fetchone()
    if not claimed:
        raise HTTPException(409,'Confirmation already processed')
    state['status']='EXECUTING'
    if intent in {'DDI_CHECK','MY_MEDICATIONS_DDI'}:
        ids=[int(x['product_id']) for x in state['products']]
        if not 2 <= len(ids) <= 5 or len(set(ids))!=len(ids):
            raise HTTPException(409,'Select 2-5 different medications')
        client=_lambda()
        try:
            resp=client.invoke(FunctionName=FUNCTION_NAME,InvocationType='RequestResponse',
                Payload=json.dumps({'mode':'ddi_multi_chat','product_ids':ids,'message':state['message']},ensure_ascii=False).encode())
            result=json.loads(resp['Payload'].read())
        except Exception as exc:
            state['status']='READY_FOR_CONFIRMATION'
            _save(user_id,sid,state)
            log.exception('DDI Lambda invocation failed')
            raise HTTPException(502,'DDI analysis unavailable') from exc
        if resp.get('FunctionError') or not isinstance(result,dict) or not isinstance(result.get('answer'),str):
            state['status']='READY_FOR_CONFIRMATION'
            _save(user_id,sid,state)
            raise HTTPException(502,'DDI Lambda response invalid')
        state['status']='COMPLETED'
        _save(user_id,sid,state)
        return _reply(sid,state,result['answer'], answer=result['answer'],
                      coverage=result.get('coverage'),pair_results=result.get('pair_results'),
                      evidence_summary=result.get('evidence_summary'),
                      assessment_status=result.get('assessment_status'),
                      limitations=result.get('limitations', []),
                      pair_assessments=result.get('pair_assessments', []),
                      presentation_version=result.get('presentation_version'),
                      answer_source=result.get('answer_source'))
    if intent=='REMINDER_CREATE':
        if len(state['products'])!=1 or not state.get('time') or state.get('repeat_days') is None:
            raise HTTPException(409,'Product/time/days not confirmed')
        product=state['products'][0]
        body=MedicationRegisterIn(item_type=product['item_type'],product_id=product['product_id'],
                    client_medication_id=state['client_medication_id'],
                    times=[state['time']],repeat_days=state['repeat_days'],dose_text=state.get('dose_text'))
        try:
            result=register_user_medication(body=body,user_id=user_id)
        except Exception:
            # Original client_medication_id remains stable for safe retries.
            state['status']='READY_FOR_CONFIRMATION'
            _save(user_id,sid,state)
            raise
        state['status']='COMPLETED'
        _save(user_id,sid,state)
        return _reply(sid,state,'복약 일정이 저장되었습니다. 앱에서 알림 일정을 동기화해야 합니다.',
                      registration=result,requires_client_sync=True)
    raise HTTPException(409,'This intent has no confirmation action')


def cancel(user_id: str, sid: str) -> dict:
    state=_session(user_id,sid)
    state['status']='CANCELLED'
    _save(user_id,sid,state)
    return _reply(sid,state,'작업을 취소했습니다.')
