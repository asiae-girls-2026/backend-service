"""CarePong medication-only LUI API; separate from empathy chatbot."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from fastapi.concurrency import run_in_threadpool
from ..services.raw_auth import require_raw_user
from ..services import medication_lui

router = APIRouter(tags=['복약 LUI (2차)'])

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
    return await run_in_threadpool(medication_lui.message,user_id,body.message,body.conversation_id)

@router.post('/medication-chat/selections')
async def medication_selection(body:SelectionIn,user_id:str=Depends(require_raw_user)):
    return await run_in_threadpool(medication_lui.select,user_id,body.conversation_id,body.product_id,body.user_medication_id)

@router.post('/medication-chat/confirm')
async def medication_confirm(body:ConversationIn,user_id:str=Depends(require_raw_user)):
    return await run_in_threadpool(medication_lui.confirm,user_id,body.conversation_id)

@router.post('/medication-chat/cancel')
async def medication_cancel(body:ConversationIn,user_id:str=Depends(require_raw_user)):
    return await run_in_threadpool(medication_lui.cancel,user_id,body.conversation_id)
