"""CarePong medication-only LUI API; separate from empathy chatbot."""
import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from ..services.raw_auth import require_raw_user
from ..services import medication_lui

log = logging.getLogger('carepong.medication_chat')
router = APIRouter(tags=['복약 LUI (2차)'])

async def _qa_safe_lui_call(fn, user_id, conversation_id, *args):
    try:
        return await run_in_threadpool(fn, user_id, *args)
    except HTTPException as exc:
        detail = str(exc.detail)
        if exc.status_code == 404 and 'expired' in detail.lower():
            code, message = 'CONVERSATION_EXPIRED', '이전 대화가 만료됐어요. 다시 시작해 주세요.'
        elif exc.status_code == 502:
            code, message = 'UPSTREAM_UNAVAILABLE', '상호작용 검사 요청을 완료하지 못했어요. 잠시 후 다시 시도해 주세요.'
        elif exc.status_code == 503:
            code, message = 'SERVICE_UNAVAILABLE', '현재 서비스에 연결하기 어려워요. 잠시 후 다시 시도해 주세요.'
        elif exc.status_code == 409:
            code, message = 'CONVERSATION_CONFLICT', '진행 중인 선택 또는 확인 단계를 완료하거나 취소해 주세요.'
        elif exc.status_code == 422:
            code, message = 'INVALID_REQUEST', '요청 내용을 확인해 주세요.'
        elif exc.status_code == 403:
            code, message = 'INVALID_SELECTION', '현재 대화에서 선택 가능한 제품이 아니에요.'
        else:
            code, message = 'REQUEST_FAILED', '요청을 처리하지 못했어요. 다시 확인해 주세요.'
        return JSONResponse(status_code=exc.status_code, content={
            'status': 'ERROR', 'response_type': 'text', 'error_code': code,
            'message': message, 'conversation_id': conversation_id, 'options': []})
    except Exception:
        request_id = str(uuid.uuid4())
        log.exception('Medication LUI request failed request_id=%s', request_id)
        return JSONResponse(status_code=500, content={
            'status': 'ERROR', 'response_type': 'text',
            'error_code': 'INTERNAL_ERROR',
            'message': '요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.',
            'conversation_id': conversation_id, 'request_id': request_id, 'options': []})


class MessageIn(BaseModel):
    message: str = Field(min_length=1,max_length=1000)
    conversation_id: str | None = None

class SelectionIn(BaseModel):
    conversation_id: str
    product_id: int | None = None
    user_medication_id: int | None = None

class ConversationIn(BaseModel):
    conversation_id: str

@router.post('/medication-chat/messages')
async def medication_message(body:MessageIn,user_id:str=Depends(require_raw_user)):
    return await _qa_safe_lui_call(medication_lui.message, user_id, body.conversation_id, body.message, body.conversation_id)

@router.post('/medication-chat/selections')
async def medication_selection(body:SelectionIn,user_id:str=Depends(require_raw_user)):
    return await _qa_safe_lui_call(medication_lui.select, user_id, body.conversation_id, body.conversation_id, body.product_id, body.user_medication_id)

@router.post('/medication-chat/confirm')
async def medication_confirm(body:ConversationIn,user_id:str=Depends(require_raw_user)):
    return await _qa_safe_lui_call(medication_lui.confirm, user_id, body.conversation_id, body.conversation_id)

@router.post('/medication-chat/cancel')
async def medication_cancel(body:ConversationIn,user_id:str=Depends(require_raw_user)):
    return await _qa_safe_lui_call(medication_lui.cancel, user_id, body.conversation_id, body.conversation_id)
