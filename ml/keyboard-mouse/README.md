# ml/keyboard-mouse/

키보드·마우스 입력 기반 컨디션(스트레스) 예측 모델과 추론 코드 (1차).

- **출처 레포:** `asiae-girls-2026/keyboard-mouse-1st-modeling`
- **커밋:** `563d924`

| 파일 | 내용 |
|------|------|
| `model_v1.json` | 학습된 모델 (16피처 · 계수·표준화·isotonic 보정·판정 컷) |
| `stress_model.json` | 모델 설정/파라미터 번들 |
| `validation_samples.json` | 추론 검증용 샘플 입출력 |
| `predict.py` | 서버 추론용 단일 파일 (`predict(features) -> {stress_score, prob, ...}`, 표준 라이브러리만 사용) |

> 이 모델(`model_v1.json`)은 서비스용으로 `../../model/predict.py`에 이미 값이 내장되어 있습니다. 여기 파일은 원본 아티팩트/코드 보관용입니다.
