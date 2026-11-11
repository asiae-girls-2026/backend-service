"""CarePong medication LUI orchestration. No empathy-chat modifications."""
from __future__ import annotations
import json
from difflib import SequenceMatcher
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
INTENTS = {'DDI_CHECK','MY_MEDICATIONS','MY_MEDICATIONS_DDI','REMINDER_CREATE','MEDICATION_INFO','REMINDER_UPDATE','REMINDER_DELETE','MEDICATION_REPLACE','UNKNOWN'}
FUNCTION_NAME = os.getenv('MEDICATION_LAMBDA_NAME', 'carepong-medication-orchestrator')
MODEL_ID = os.getenv('MEDICATION_LUI_MODEL_ID', 'apac.amazon.nova-lite-v1:0')
REGION = os.getenv('AWS_REGION', 'ap-northeast-2')


def _runtime():
    import boto3
    from botocore.config import Config
    return boto3.client('bedrock-runtime', region_name=REGION,
        config=Config(connect_timeout=3, read_timeout=15,
                      retries={'total_max_attempts': 1}))


def _lambda():
    import boto3
    from botocore.config import Config
    return boto3.client('lambda', region_name=REGION,
        config=Config(connect_timeout=3, read_timeout=40,
                      retries={'total_max_attempts': 1}))


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I).strip()
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError('JSON object expected')
    return obj


# CarePong LUI QA hardening (v2). No automatic product selection or DDI assertion.
_WEEKDAY_LABELS = ('월', '화', '수', '목', '금', '토', '일')
_TIME_SUGGESTIONS = {
    '아침': ('07:00', '08:00', '09:00'),
    '오전': ('07:00', '08:00', '09:00'),
    '점심': ('12:00', '12:30', '13:00'),
    '저녁': ('18:00', '19:00', '20:00'),
    '밤': ('20:00', '21:00', '22:00'),
    '자기 전': ('21:00', '22:00', '23:00'),
}

def _day_labels(days):
    return [_WEEKDAY_LABELS[n] + '요일' for n in sorted(days)]

def _parse_explicit_days(text):
    # This service uses Python weekday: Monday=0, Sunday=6.
    compact = re.sub(r'\s+', '', text)
    if re.search(r'격\s*주', text):
        return None
    if '매일' in compact:
        return list(range(7))
    if '평일' in compact:
        return list(range(5))
    if '주말' in compact:
        return [5, 6]

    range_pattern = r'(?<![가-힣])([월화수목금토일])(?:요일)?\s*(?:~|～|\-|부터)\s*([월화수목금토일])(?:요일)?(?:까지)?'
    m = re.search(range_pattern, text)
    if m:
        start, end = (_WEEKDAY_LABELS.index(x) for x in m.groups())
        return list(range(start, end + 1)) if start <= end else None

    day = r'[월화수목금토일](?:요일)?'
    connector = r'(?:\s*(?:,|，|·|/|및|하고|과|와|이랑|랑)\s*|\s+)'
    group = re.search(rf'(?<![가-힣])(?:매주\s*)?({day}(?:{connector}{day})+)', text)
    if group:
        return sorted({_WEEKDAY_LABELS.index(c) for c in re.findall(r'([월화수목금토일])(?:요일)?', group.group(1))})

    single = re.search(r'(?<![가-힣])(?:매주\s*)?([월화수목금토일])요일(?=은|는|에|마다|\s|$|,|\.)', text)
    if single:
        return [_WEEKDAY_LABELS.index(single.group(1))]
    return None

def _parse_explicit_time(text):
    match = re.search(r'(?:(오전|오후|아침|저녁|밤)\s*)?(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분?)?', text)
    if not match:
        match = re.search(r'(?:(오전|오후|아침|저녁|밤)\s*)?(\d{1,2}):(\d{2})', text)
    if not match:
        return None
    period, h, m = match.groups()
    h, m = int(h), int(m or 0)
    if not 0 <= m <= 59:
        return None
    if period in ('오전', '오후', '아침'):
        if not 1 <= h <= 12:
            return None
        if period == '오후' and h < 12:
            h += 12
        elif period in ('오전', '아침') and h == 12:
            h = 0
    elif period in ('저녁', '밤'):
        if not 1 <= h <= 23:
            return None
        if h <= 11:
            h += 12
    elif not 0 <= h <= 23:
        return None
    return f'{h:02d}:{m:02d}'

def _time_suggestions(text):
    # Suggestions are never saved without explicit user choice.
    for word in ('자기 전', '아침', '오전', '점심', '저녁', '밤'):
        if word in text:
            return list(_TIME_SUGGESTIONS[word])
    return []

def _intent_from_text(text):
    # Routing is intentionally non-destructive: existing medication edits go to Settings.
    if re.search(r'병용|상호작용|같이\s*먹|함께\s*먹|먹어도\s*돼|금기', text):
        if re.search(r'내\s*약|등록(?:된|한)\s*약|복약\s*목록|먹고\s*있는\s*약', text):
            return 'MY_MEDICATIONS_DDI'
        return 'DDI_CHECK'
    if re.search(r'(?:다른|새|새로운)\s*약.{0,16}(?:바꿔|바꾸|교체|변경)|(?:약|의약품).{0,15}(?:다른|새로운)\s*약.{0,12}(?:변경|교체|바꿔|바꾸)', text):
        return 'MEDICATION_REPLACE'
    if re.search(r'등록\s*(?:한\s*(?:거|것|약)?|된\s*(?:거|것|약)?)?\s*(?:해제|삭제|취소)|(?:복약|약|알림)\s*(?:등록)?\s*(?:해제|삭제|중단|그만|꺼줘|끄기)|(?:등록된|등록한)\s*약.{0,15}(?:지워|삭제|해제)', text):
        return 'REMINDER_DELETE'
    if re.search(r'\d{1,2}\s*시(?:를|에서)?\s*\d{1,2}\s*시.{0,12}(?:바꿔|바꾸|변경|수정)|(?:시간|요일|일정).{0,20}(?:바꿔|변경|수정)|(?:바꿔|변경|수정).{0,20}(?:시간|요일|일정)|(?:복약|약|알림).{0,15}(?:시간|요일).{0,15}(?:바꿔|변경|수정)', text):
        return 'REMINDER_UPDATE'
    if re.search(r'등록(?:된|한)\s*약|내\s*약|복약\s*목록', text) and re.search(r'알려|보여|조회|목록|뭐|무엇', text):
        return 'MY_MEDICATIONS'
    if re.search(r'등록(?:해|할|하고|하자)|추가(?:해|할)|알림|챙겨|리마인드|설정해', text):
        return 'REMINDER_CREATE'
    return None

def _strip_drug_query(text):
    # Crop trailing request/temporal clauses; never infer a medical product.
    text = re.sub(r'[?!。！]+$', '', text.strip())
    text = re.split(r'\s*(?:같이\s*먹|함께\s*먹|병용|상호작용|먹어도\s*돼|등록해|등록할|등록하고|등록하자|추가해|추가할|알림|챙겨|리마인드|설정해)', text, maxsplit=1)[0]
    text = re.split(r'\s+(?=매주|매일|평일|주말|[월화수목금토일](?:요일|,|\s*[월화수목금토일])|아침|오전|오후|저녁|점심|밤|\d{1,2}시)', text, maxsplit=1)[0]
    return text.strip(' \t,，.은는을를이가 ')

def _fallback_drug_names(text, intent):
    if intent in ('MY_MEDICATIONS','MY_MEDICATIONS_DDI'):
        return []
    q = _strip_drug_query(text)
    if intent == 'DDI_CHECK':
        parts = re.split(r'(?:이랑|랑|하고|과|와|,| 및 )\s*', q)
        names = [p.strip() for p in parts if p.strip()]
        return names[:5] if len(names) >= 2 else ([q] if q else [])
    return [q] if q else []


# Additive response contract. Frontends that only render response_type=text still work.
_MANAGE_COPY = {
    'REMINDER_UPDATE': '복약 시간은 설정의 약 등록 관리에서 변경할 수 있어요.',
    'REMINDER_DELETE': '등록된 약은 설정의 약 등록 관리에서 해제할 수 있어요.',
    'MEDICATION_REPLACE': '기존 약의 등록을 해제한 뒤 새 약을 등록할 수 있어요. 먼저 약 등록 관리에서 기존 알림을 확인해 주세요.',
}

def _qa_error(conversation_id, error_code, message):
    return {'status': 'ERROR', 'response_type': 'text', 'error_code': error_code,
            'message': message, 'conversation_id': conversation_id, 'options': []}

def _qa_management_response(user_id, intent, conversation_id=None):
    if conversation_id:
        sid = conversation_id  # Do not mutate an in-progress registration session.
    else:
        state = {'user_id': user_id, 'intent': intent, 'status': 'COMPLETED',
                 'products': [], 'pending': []}
        sid = _new_session(user_id, state)
    return {'status': 'MANAGE_MEDICATION', 'intent': intent,
            'operation': intent, 'response_type': 'text',
            'message': _MANAGE_COPY[intent], 'conversation_id': sid, 'options': [],
            'action': {'type': 'OPEN_MEDICATION_MANAGEMENT',
                       'label': '약 등록 관리로 이동'}}

def _qa_schedule_error(text, sid):
    clocks = re.findall(r'(?<!\d)(?:\d{1,2}\s*시(?:\s*\d{1,2}\s*분?)?|\d{1,2}:\d{2})(?!\d)', text)
    if len(clocks) > 1 and not re.search(r'(?:시|분|\d)(?:를|에서|에서부터)\s*(?:오전|오후|아침|저녁|밤)?\s*\d', text):
        return _qa_error(sid, 'SCHEDULE_UNSUPPORTED',
            '여러 시각이나 요일마다 다른 시각의 일정은 약 등록 관리에서 설정해 주세요.')
    if re.search(r'격\s*주', text):
        return _qa_error(sid, 'SCHEDULE_UNSUPPORTED',
                         '격주 반복은 현재 지원하지 않아요. 매일 또는 요일별 매주 일정을 입력해 주세요.')
    if re.search(r'(?<![가-힣])(?:[월화수목금토일])(?:요일)?\s*(?:~|～|\-|부터)\s*(?:[월화수목금토일])(?:요일)?', text):
        if _parse_explicit_days(text) is None:
            return _qa_error(sid, 'SCHEDULE_PARSE_FAILED', '요일 범위를 정확히 이해하지 못했어요. 다시 입력해 주세요.')
    return None

def analyze(message: str, previous_intent: str | None = None) -> dict:
    """LLM suggests structured slots. Server never treats them as authority."""
    prompt = ("한국어 의약품 챗봇의 의도/슬롯 분류기다. JSON 객체만 출력. "
              "intent는 DDI_CHECK, MY_MEDICATIONS, MY_MEDICATIONS_DDI, REMINDER_CREATE, "
              "MEDICATION_INFO, REMINDER_UPDATE, REMINDER_DELETE, MEDICATION_REPLACE, UNKNOWN 중 하나. "
              "drug_names는 명시적으로 등장한 제품명 문자열 배열(추측 금지). "
              "time은 확실한 시각일 때만 HH:MM, 아니면 null. "
              "repeat_days는 '매일'이면 [0,1,2,3,4,5,6], '평일'이면 [0,1,2,3,4], "
              "요일이 불명확하면 null. 0=월요일, 6=일요일. "
              "dose_text는 명시된 용량만, 없으면 null. "
              "등록약 목록을 요청하면 MY_MEDICATIONS, 등록약 병용검사면 MY_MEDICATIONS_DDI. "
              "모호한 '저녁', '아침', '식후'는 시각으로 변환하지 말 것. "
              "등록해줘/등록할게/챙겨줘는 REMINDER_CREATE, 같이 먹어도 돼/병용은 DDI_CHECK. "
              "문장에서는 제품명만 drug_names로 추출하고 시간/요일/동사를 제거할 것. "
              "예: 타이레놀 아침 7시에 등록할게 => REMINDER_CREATE, [타이레놀], 07:00. "
              "예: 아나프록시 월, 화요일 아침마다 등록해줘 => REMINDER_CREATE, [아나프록시], null, [0,1]. "
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
        out = {'intent':'UNKNOWN', 'drug_names':[], 'time':None, 'repeat_days':None, 'dose_text':None}
    intent = out.get('intent', 'UNKNOWN')
    if intent not in INTENTS:
        intent = 'UNKNOWN'
    local_intent = _intent_from_text(message)
    if local_intent in _MANAGE_COPY:
        intent = local_intent
    elif local_intent and (previous_intent is None or intent in ('UNKNOWN', 'MEDICATION_INFO') or local_intent in ('DDI_CHECK', 'MY_MEDICATIONS_DDI')):
        intent = local_intent
    if intent == 'UNKNOWN' and previous_intent in INTENTS:
        intent = previous_intent
    names = out.get('drug_names')
    names = names if isinstance(names, list) else []
    names = [str(n).strip()[:100] for n in names if isinstance(n,str) and n.strip()][:5]
    fallback_names = _fallback_drug_names(message, intent)
    if intent == 'DDI_CHECK' and len(fallback_names) >= 2:
        names = fallback_names
    if not names or any(re.search(r'\s(?:등록|알림|아침|오전|오후|저녁|매일|월요일|화요일|\d+시)', n) for n in names):
        names = fallback_names or names
    raw_time = out.get('time')
    time = raw_time if isinstance(raw_time,str) and re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',raw_time) else None
    explicit_time = _parse_explicit_time(message)
    if explicit_time is not None:
        time = explicit_time
    elif re.search(r'(?:(?:오전|오후|아침|저녁|밤)\s*)?\d{1,2}\s*시|\b\d{1,2}:\d{2}\b', message):
        time = None  # Invalid explicit time must not be silently repaired by the LLM.
    days = out.get('repeat_days')
    if not (isinstance(days,list) and days and all(type(d) is int and 0 <= d <= 6 for d in days) and len(days) == len(set(days))):
        days = None
    # Only explicitly stated weekdays may enter a saved schedule.
    days = _parse_explicit_days(message)
    # A model suggestion cannot supply a missing or ambiguous clock time.
    time = _parse_explicit_time(message)
    dose = out.get('dose_text')
    return {'intent':intent,'drug_names':names,'time':time,'repeat_days':days,
            'dose_text':dose[:200] if isinstance(dose,str) else None}


def _candidate_name_is_plausible(query: str, product_name: str) -> bool:
    # Fuzzy matches are only displayed as candidates, never silently selected.
    # Avoid unrelated medicine suggestions from loose pg_trgm matches.
    q = re.sub(r'\s+', '', query.casefold())
    name = re.sub(r'\s+', '', product_name.split('(')[0].casefold())
    if not q or not name:
        return False
    if q in name or name in q:
        return True
    stem = re.sub(r'(정|캡슐|시럽|연질캡슐|주사|주|설하정)$', '', name)
    return max(SequenceMatcher(None, q, name).ratio(),
               SequenceMatcher(None, q, stem).ratio()) >= 0.75


def _search(name: str, item_type: str | None = 'DRUG') -> list[dict]:
    query = name.strip()
    if not query:
        return []
    result = search_catalog(q=query, item_type=item_type, limit=20)
    options = [{'product_id':i.product_id, 'product_name':i.product_name,
                'manufacturer_name':i.manufacturer_name, 'item_type':i.item_type}
               for i in result.items
               if i.item_type != 'DRUG' or _candidate_name_is_plausible(query, i.product_name)]
    if options or len(query) < 3 or item_type == 'HFF':
        return options[:5]
    # Only use relaxed trigram query to find plausible, visibly labeled candidates.
    with db.conn() as c:
        rows = c.execute("""SELECT med_product_id, product_name, manufacturer_name,
                           word_similarity(%s, product_name) AS score
                           FROM public.med_product
                           WHERE word_similarity(%s, product_name) >= 0.30
                           ORDER BY score DESC, length(product_name), product_name
                           LIMIT 100""", (query, query)).fetchall()
    candidates = [{'product_id':r[0], 'product_name':r[1],
                   'manufacturer_name':r[2], 'item_type':'DRUG'}
                  for r in rows if _candidate_name_is_plausible(query, r[1])]
    return candidates[:5]


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
            state['status'] = 'NEED_PRODUCT_NAME'
            _save(state['user_id'], sid, state)
            return _reply(sid,state, f"'{entry['query']}'에 해당하는 후보를 찾지 못했습니다. 제품명을 다시 입력해 주세요. 확인 전에는 등록이나 안전성 판정을 하지 않습니다.")
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
            choices = state.get('time_suggestions') or []
            if choices:
                message = '시간이 지정되지 않았습니다. ' + ', '.join(choices) + ' 중 하나를 입력하거나 원하는 시각을 직접 입력해주세요.'
            else:
                message = '몇 시에 알림을 받을까요? 정확한 시간(예: 오후 8시)을 입력해주세요.'
            return _reply(sid,state,message,time_suggestions=choices)
        if state.get('repeat_days') is None:
            state['status']='NEED_DAYS'
            _save(state['user_id'],sid,state)
            return _reply(sid,state,'반복 요일을 알려주세요. 예: 매일, 평일, 월요일과 수요일', repeat_day_options=[{'label':'매일','repeat_days':list(range(7))},{'label':'평일','repeat_days':list(range(5))}])
        state['status']='READY_FOR_CONFIRMATION'
        _save(state['user_id'],sid,state)
        day_text = ', '.join(_day_labels(state['repeat_days']))
        return _reply(sid,state,f"{state['products'][0]['product_name']}을(를) {state['time']} / {day_text}에 등록할까요?",
                      options=[{'id':'confirm','label':'알림 등록'},{'id':'cancel','label':'취소'}],
                      schedule={'times':[state['time']],'repeat_days':state['repeat_days'],
                                'repeat_day_labels':_day_labels(state['repeat_days']),
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
    # A specific management request must not create, edit or delete a medication.
    route = _intent_from_text(text)
    if route in _MANAGE_COPY and not (prev and prev['status'] == 'READY_FOR_CONFIRMATION' and prev['intent'] == 'REMINDER_CREATE' and route == 'REMINDER_UPDATE'):
        return _qa_management_response(user_id, route, conversation_id)
    if route == 'REMINDER_CREATE' or (prev and prev['intent'] == 'REMINDER_CREATE'):
        invalid = _qa_schedule_error(text, conversation_id)
        if invalid:
            return invalid
    if prev and prev['status'] not in {'COMPLETED', 'CANCELLED'} and route in {'DDI_CHECK','MY_MEDICATIONS_DDI','MY_MEDICATIONS','REMINDER_CREATE'} and route != prev['intent']:
        return _qa_error(conversation_id, 'CONVERSATION_CONFLICT',
            '다른 요청을 시작하려면 현재 진행 중인 등록 또는 검사를 먼저 취소해 주세요.')
    if prev and prev['status'] == 'READY_FOR_CONFIRMATION' and prev['intent'] == 'REMINDER_CREATE':
        explicit_time = _parse_explicit_time(text)
        explicit_days = _parse_explicit_days(text)
        # The existing schema cannot store an individual time per weekday.
        if (explicit_time and explicit_days is not None
                and len(prev.get('repeat_days') or []) > 1
                and re.search(r'[월화수목금토일]요일\s*(?:은|는|만|에)', text)):
            return _qa_error(conversation_id, 'SCHEDULE_UNSUPPORTED',
                '요일마다 다른 복약 시간은 현재 지원하지 않아요. 전체 시간 변경이나 별도 등록을 이용해 주세요.')
        if explicit_time is not None or explicit_days is not None:
            if explicit_time is not None:
                prev['time'] = explicit_time
            if explicit_days is not None:
                prev['repeat_days'] = explicit_days
            _save(user_id, conversation_id, prev)
            return _pending_response(conversation_id, prev)
        return _qa_error(conversation_id, 'SCHEDULE_PARSE_FAILED',
            '변경할 요일과 시간을 정확히 이해하지 못했어요. 다시 입력해 주세요.')
    if prev and prev['status'] in {'NEED_TIME','NEED_DAYS'}:
        if re.search(r'(?:(?:오전|오후|아침|저녁|밤)\s*)?\d{1,2}\s*시|\b\d{1,2}:\d{2}\b', text) and _parse_explicit_time(text) is None:
            return _qa_error(conversation_id, 'SCHEDULE_PARSE_FAILED', '복약 시간을 정확히 이해하지 못했어요. 다시 입력해 주세요.')
        slots=analyze(text, prev['intent'])
        if prev['status']=='NEED_TIME' and slots['time']:
            prev['time']=slots['time']
        if prev['status']=='NEED_DAYS' and slots['repeat_days'] is not None:
            prev['repeat_days']=slots['repeat_days']
        if slots['time']:
            prev['time']=slots['time']
        if prev['status']=='NEED_TIME' and not slots['time']:
            prev['time_suggestions']=_time_suggestions(text) or prev.get('time_suggestions',[])
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
        if slots['time'] and prev['intent']=='REMINDER_CREATE':
            prev['time']=slots['time']
        if slots['repeat_days'] is not None and prev['intent']=='REMINDER_CREATE':
            prev['repeat_days']=slots['repeat_days']
        for name in names[:max(0,5-len(prev['products']))]:
            prev['pending'].append({'query':name,'options':_search(name, None if prev['intent']=='REMINDER_CREATE' else 'DRUG')})
        prev['status']='SELECTING_PRODUCTS'
        _save(user_id,conversation_id,prev)
        return _pending_response(conversation_id,prev)
    if prev and prev['status'] not in {'COMPLETED','CANCELLED'}:
        raise HTTPException(409,'Complete or cancel the current conversation first')
    slots=analyze(text)
    intent=slots['intent']
    if intent in _MANAGE_COPY:
        return _qa_management_response(user_id, intent)
    # Do not persist a schedule when a specified hour is malformed.
    if intent == 'REMINDER_CREATE' and re.search(r'(?:(?:오전|오후|아침|저녁|밤)\s*)?\d{1,2}\s*시|\b\d{1,2}:\d{2}\b', text) and _parse_explicit_time(text) is None:
        return _qa_error(None, 'SCHEDULE_PARSE_FAILED', '복약 시간을 정확히 이해하지 못했어요. 다시 입력해 주세요.')
    if intent=='UNKNOWN':
        return {'conversation_id':None,'status':'NEED_INTENT','intent':intent,'response_type':'text',
                'message':'약 상호작용 검사, 등록약 조회, 복약 알림 등록 중 어떤 도움이 필요하신가요?','options':[]}
    state={'user_id':user_id,'intent':intent,'status':'SELECTING_PRODUCTS','products':[], 'pending':[],
           'message':text[:1000],'time':slots['time'],'repeat_days':slots['repeat_days'],
           'dose_text':slots['dose_text'],'time_suggestions':_time_suggestions(text) if not slots['time'] else [],
           'client_medication_id':str(uuid.uuid4())}
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
    if intent in {'DDI_CHECK','MY_MEDICATIONS_DDI'}:
        ids=[int(x['product_id']) for x in state['products']]
        if not 2 <= len(ids) <= 5 or len(set(ids))!=len(ids):
            raise HTTPException(409,'Select 2-5 different medications')
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
        try:
            client=_lambda()
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
                      answer_source=result.get('answer_source'),
                      ddi_sections=result.get('ddi_sections', []),
                      ddi_sections_version=result.get('ddi_sections_version'))
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
        return _reply(sid,state,'복약 일정이 저장되었습니다. ' + ', '.join(_day_labels(state['repeat_days'])) + ' ' + state['time'] + '. 앱에서 알림 일정을 동기화해야 합니다.',
                      registration=result,requires_client_sync=True,
                      schedule={'times':[state['time']], 'repeat_days':state['repeat_days'],
                                'repeat_day_labels':_day_labels(state['repeat_days'])})
    raise HTTPException(409,'This intent has no confirmation action')


def cancel(user_id: str, sid: str) -> dict:
    state=_session(user_id,sid)
    state['status']='CANCELLED'
    _save(user_id,sid,state)
    return _reply(sid,state,'작업을 취소했습니다.')
