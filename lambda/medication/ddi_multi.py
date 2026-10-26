"""사용자가 직접 선택한 2~5개 의약품의 모든 쌍을 검사한다."""

from itertools import combinations


def validate_product_ids(event):
    ids = event.get("product_ids")

    if not isinstance(ids, list):
        raise ValueError("product_ids는 배열이어야 합니다.")

    if not 2 <= len(ids) <= 5:
        raise ValueError("의약품은 2~5개까지 검사할 수 있습니다.")

    if any(type(product_id) is not int or product_id <= 0 for product_id in ids):
        raise ValueError("제품 ID는 양의 정수여야 합니다.")

    if len(set(ids)) != len(ids):
        raise ValueError("중복된 제품 ID는 사용할 수 없습니다.")

    return ids


def check_multi(event, ddi_handler):
    ids = validate_product_ids(event)
    pair_results = []

    for a, b in combinations(ids, 2):
        result = ddi_handler(
            {
                "mode": "ddi_check",
                "product_a": a,
                "product_b": b,
            },
            None,
        )

        if not isinstance(result, dict):
            raise RuntimeError(f"DDI 검사 결과 형식 오류: {a}, {b}")

        pair_results.append({
            "product_a": a,
            "product_b": b,
            "status": result.get("status"),
            "coverage": result.get("coverage"),
            "ddi_result": result,
        })

    return {
        "mode": "ddi_multi_check",
        "product_ids": ids,
        "product_count": len(ids),
        "pair_count": len(pair_results),
        "coverage": "INCOMPLETE"
            if any(
                item["coverage"] != "COMPLETE"
                for item in pair_results
            )
            else "COMPLETE",
        "pair_results": pair_results,
    }
