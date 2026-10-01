"""약 물어보기 M04. 식약처 e약은요 API를 서버가 대신 호출 → API 키를 앱에 안 넣는다.
프로토타입의 searchApi()를 서버로 옮긴 것."""
from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(tags=["약 정보 (M04)"])

class DrugInfo(BaseModel):
    name: str
    company: str
    efficacy: str
    usage: str
    caution: str

@router.get("/drugs/search", response_model=list[DrugInfo], summary="[Stub] 약 이름으로 e약은요 검색 (서버 프록시)")
def search(q: str = Query(..., min_length=1, examples=["타이레놀"])):
    # TODO: DATA_GO_KR_KEY 있으면 httpx로 실호출. 지금은 목
    return [DrugInfo(name=f"{q}정", company="(목)", efficacy="두통·발열 완화", usage="1회 1정", caution="과량 복용 주의")]
