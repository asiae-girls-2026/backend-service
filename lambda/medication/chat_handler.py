import json
import logging
import os
import re

import boto3
from botocore.config import Config

# 이미 Lambda에서 정상 동작하는 DDI 검사 함수를 재사용한다.
from lambda_function import handler as ddi_handler
from ddi_presentation import present_pair

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "apac.amazon.nova-lite-v1:0"
)

bedrock = boto3.client(
    "bedrock-runtime",
    region_name="ap-northeast-2",
    config=Config(
        connect_timeout=5,
        read_timeout=45,
        retries={"max_attempts": 1}
    )
)

SYSTEM_PROMPT = """
당신은 케어퐁의 의약품 상호작용 정보 안내 도우미입니다.

[역할]
- 사용자 질문을 이해하고 DDI 도구의 검사 결과를 한국어로 설명합니다.
- 상호작용 여부와 근거는 반드시 DDI 도구 결과를 기준으로 판단합니다.
- 직접 의학적 진단이나 처방 결정을 내리지 않습니다.

[응답 원칙]
1. 사용자에게 의약품의 제품명을 사용하여 설명합니다.
2. 확인된 DUR 근거와 상호작용 사유를 우선 설명합니다.
3. 의학 용어는 필요한 경우 쉽게 풀어 설명합니다.
4. 도구 결과에 없는 부작용, 금기사항, 위험을 만들어내지 않습니다.
5. 확실한 근거와 검토 중인 후보 근거를 구분합니다.
6. confirmed_rules가 비어 있다는 사실만으로
   official_product_evidence를 무시하지 않습니다.
7. official_product_evidence가 확인된 경우에는
   공식 DUR 원본에서 확인된 사실을 설명할 수 있습니다.
8. review_status가 PENDING이거나 scope_verified가 false인
   후보 규칙은 검증 완료 규칙이라고 설명하지 않습니다.
9. coverage가 INCOMPLETE인 경우
   모든 상호작용에 대한 검사가 완료된 것처럼 표현하지 않습니다.
10. 검사 결과가 UNCERTAIN이거나 근거가 없더라도
    안전성이 확인되었다고 표현하지 않습니다.
11. 도구 실행이 실패했다면 검사에 성공한 것처럼 답변하지 않습니다.

[프론트엔드 공통 안내]
- 의사·약사 상담 권고 문구는 프론트엔드에서 별도로 표시합니다.
- 복용 중단, 용량 변경 금지에 대한 공통 안내도
  프론트엔드에서 별도로 표시합니다.
- 따라서 위 공통 안내 문구를 매번 반복하지 않습니다.
- 다만 근거의 검토 상태와 검사 범위 등
  해당 결과를 이해하는 데 필수적인 한계는 숨기지 않습니다.

[출력 형식]
- 한국어로 간결하고 자연스럽게 답변합니다.
- 가능한 한 2~4문장으로 설명합니다.
- 내부 제품 ID, rule_id 등은 불필요하면 표시하지 않습니다.
- thinking, response, XML 태그, 내부 추론 내용을 출력하지 않습니다.
- 사용자에게 보여줄 최종 답변 본문만 출력합니다.
"""

TOOLS = [
    {
        "toolSpec": {
            "name": "ddi_check",
            "description": (
                "케어퐁 RDS에 저장된 공식 DUR 근거를 "
                "기존 Python DDI 엔진으로 확인한다. "
                "실제 데이터베이스 제품 ID 두 개를 입력한다."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "product_a": {
                            "type": "integer",
                            "description": "첫 번째 의약품의 DB 제품 ID"
                        },
                        "product_b": {
                            "type": "integer",
                            "description": "두 번째 의약품의 DB 제품 ID"
                        }
                    },
                    "required": ["product_a", "product_b"],
                    "additionalProperties": False
                }
            }
        }
    }
]


def plain_text(message):
    return "".join(
        block.get("text", "")
        for block in message.get("content", [])
    ).strip()


def validate_event(event):
    question = event.get("message", "")
    if not isinstance(question, str):
        raise ValueError("message는 문자열이어야 합니다.")

    question = question.strip()

    if not question or len(question) > 1000:
        raise ValueError("message는 1~1000자로 입력하세요.")

    # 제품 ID는 자연어에서 추측하지 않고 명시된 값만 사용.
    a = event.get("product_a")
    b = event.get("product_b")

    if isinstance(a, bool) or isinstance(b, bool):
        raise ValueError("제품 ID는 정수여야 합니다.")

    try:
        a, b = int(a), int(b)
    except (TypeError, ValueError):
        raise ValueError(
            "현재 ddi_chat은 product_a, product_b가 필요합니다."
        )

    if a <= 0 or b <= 0 or a == b:
        raise ValueError("서로 다른 양의 정수 제품 ID를 입력하세요.")

    return question, a, b



def build_model_evidence(result):
    """DDI 원본을 변경하지 않고 LLM 입력만 축소한다."""
    products = result.get("products") or {}
    official = result.get("official_product_evidence") or []
    candidates = result.get("candidate_rules") or []

    evidence = []
    seen = set()

    for item in official:
        reason = item.get("prohibition_reason", "")
        key = (item.get("source"), reason)

        if key in seen:
            continue

        seen.add(key)
        evidence.append({
            "source": item.get("source"),
            "reason": reason,
            "dur_seq": item.get("dur_seq"),
        })

    return {
        "status": result.get("status"),
        "coverage": result.get("coverage"),
        "products": {
            str(k): v.get("name", "")
            for k, v in products.items()
        },
        "official_product_evidence": evidence,
        "confirmed_rule_count": len(
            result.get("confirmed_rules") or []
        ),
        "pending_candidate_count": sum(
            1 for c in candidates
            if c.get("review_status") == "PENDING"
        ),
        "reason_codes": result.get("reason_codes") or [],
        "interpretation_note": (
            "공식 제품쌍 근거와 내부 검토 완료 규칙을 구분할 것. "
            "검사 범위가 불완전하면 완전한 안전성 검사로 표현하지 말 것. "
            "상담 권고 공통 문구는 프론트엔드에서 별도로 표시함."
        ),
    }



def evidence_fallback(result):
    ids = list((result.get("products") or {}).keys())
    if len(ids) != 2:
        return "제품 식별정보가 불완전해 상호작용을 확정할 수 없어요. 안전하다는 뜻은 아니에요."
    return present_pair(result, ids[0], ids[1])["answer"]



def clean_generated_answer(raw_text, result):
    # Source-based conclusions and limits must never depend on LLM compliance.
    return evidence_fallback(result)

def converse_chat(event):
    question, a, b = validate_event(event)

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "text": (
                        f"질문: {question}\n"
                        f"확인할 제품 ID: {a}, {b}\n"
                        "반드시 ddi_check 도구를 호출해 "
                        "상호작용 근거를 확인하세요."
                    )
                }
            ]
        }
    ]

    # 실제 Converse Tool Use를 수행한다.
    first = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=messages,
        toolConfig={
            "tools": TOOLS,
            "toolChoice": {"tool": {"name": "ddi_check"}}
        },
        inferenceConfig={
            "maxTokens": 500,
            "temperature": 0.1
        }
    )

    assistant_message = first["output"]["message"]
    tool_requests = [
        block["toolUse"]
        for block in assistant_message.get("content", [])
        if "toolUse" in block
    ]

    if len(tool_requests) != 1:
        raise RuntimeError(
            "Bedrock에서 정확히 하나의 ddi_check 요청을 반환하지 않았습니다."
        )

    tool = tool_requests[0]

    if tool.get("name") != "ddi_check":
        raise RuntimeError("허용되지 않은 도구 호출입니다.")

    args = tool.get("input") or {}

    # LLM이 도구 인자를 변경해도 입력한 실제 제품 ID만 검사.
    requested = {str(args.get("product_a")), str(args.get("product_b"))}
    expected = {str(a), str(b)}

    if requested != expected:
        raise RuntimeError("모델이 다른 제품 ID를 요청했습니다.")

    # 앞 단계에서 성공한 원래의 DDI Lambda 핸들러 사용.
    result = ddi_handler(
        {
            "mode": "ddi_check",
            "product_a": a,
            "product_b": b
        },
        None
    )

    # boto3의 JSON 입력에 사용할 수 있도록 변환한다.
    clean_result = json.loads(
        json.dumps(result, ensure_ascii=False, default=str)
    )

    # Preserve the first Converse tool-use step; final wording is deterministic.
    presentation = present_pair(clean_result, a, b)
    answer = presentation["answer"]

    return {
        "mode": "ddi_chat",
        "model_id": MODEL_ID,
        "tool_called": "ddi_check",
        "product_a": a,
        "product_b": b,
        "ddi_status": clean_result.get("status"),
        "coverage": clean_result.get("coverage"),
        "answer": answer,
        "ddi_result": clean_result,
        "answer_source": "DETERMINISTIC_EVIDENCE_TEMPLATE",
        **presentation,
    }


def handler(event, context):
    mode = event.get("mode", "ddi_check")

    if mode == "ddi_check":
        # 기존 성공한 DDI 전용 경로 유지
        return ddi_handler(event, context)

    if mode == "ddi_chat":
        return converse_chat(event)

    if mode == "ddi_multi_check":
        return ddi_handler(event, context)

    if mode == "ddi_multi_chat":
        from ddi_multi_chat import converse_multi
        return converse_multi(
            event, ddi_handler, bedrock, MODEL_ID
        )

    raise ValueError("지원하지 않는 mode입니다.")

# BEGIN CAREPONG DDI SCOPED V2
from ddi_review_runtime import wrap_handler as _ddi_scoped_wrap_handler
handler = _ddi_scoped_wrap_handler(handler)
# END CAREPONG DDI SCOPED V2
