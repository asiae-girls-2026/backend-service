# model — 과부하 추론 서버

FastAPI 컨테이너 1개(ML EC2 :8001). `predict.py` 는 **혜윤 제공 파일 그대로**(model_v1.json 내장, 표준 라이브러리만). 모델 교체 = `predict.py` 교체 + `MODEL_VERSION` 환경변수 올림 + 재빌드.

| 주소 | 내용 |
|---|---|
| `GET /health` | `model_version` · `features`(16) · `cut_report`/`cut_intervene`(혜윤 컷) · `standardized_input: true` |
| `POST /predict` | `{features: {16피처}, standardized: false}` → `score`(소수 1자리)·`prob`·`level`·`imputed`·`model_version` |

- `standardized: true` 면 features 가 이미 표준화된 z-벡터(api 의 `kbd_personalize`, 11/6) — 내장 스케일러를 건너뛰고 계수에 바로 적용. 누락값은 0(=평균) 대치.
- 활동 0 창은 모델을 거치지 않고 `null` (api 가 처리).
- api 는 `/health` 에 `standardized_input` 이 없으면 원시 피처로 자동 폴백하므로, 옛 모델 컨테이너와도 호환된다.

테스트: `pytest` — `tests/validation_samples.json` 5건(혜윤 원본 함수 출력)과 동일한지. 모델 자체 설명은 혜윤 ML 인계서(10/13)·carepong-automation.
