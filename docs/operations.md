# 운영 안내 (backend-service)

> 개발·운영자용 상세 문서. 요약은 [README.ko.md](../README.ko.md) / [README.md](../README.md).


## 폴더와 담당

```
backend-service/
├ api/           메인 API (FastAPI). app/routers/ 주소별 · app/services/ 추론·정책·DB·Bedrock·복약 LUI · migrations/*.sql · tests/   — Backend
├ model/         추론 서버 (FastAPI). predict.py = ML 모델 파일(model_v1.json 내장). 표준화 입력(standardized) 지원           — Backend(서빙)·ML(모델)
├ collector/     입력 수집기. pynput 전역 후킹 → 1분 피처 16개 JSON. 앱에 collector.exe 로 동봉                                  — Backend
├ infra/         docker-compose.yml(api·model·db) + docker-compose.aws.yml(EC2 오버라이드) · ddi-policy/(Data)                   — Backend / Data
├ scripts/       EC2 콘솔 붙여넣기용 점검·배포 스크립트, smoke.py. archive/ 는 1차 베타 점검분                                   — Backend (ddi_backend_qa.py·carepong_gold/·verify_ddi_sections.py 는 Data)
├ docs/          구성도·DB·API 변경 이력·복약 2차 API. archive/ 는 1차 전달물. ddi/ 는 Data                                      — Backend / Data
├ etl/           의약품·건기식 적재, DUR·DDI 규칙 생성 파이프라인(Prefect)                                                         — Data
├ lambda/        복약 DDI 검사·설명 Lambda (Bedrock Nova)                                                                           — Data
└ deploy/        EC2 systemd 유닛(복약 회차 생성 타이머·Prefect 갱신)                                                               — Data
```

Data 영역(etl·lambda·deploy·infra/ddi-policy·docs/ddi)의 설명·실행법은 각 폴더의 README 와 Data 담당에게. 이 README 는 api·model·collector·infra(compose)·scripts 를 다룬다.

## 켜기 (로컬)

```
cp .env.example .env            # 값은 비워도 뜬다 (DB 는 compose 의 postgres, 모델은 같은 compose 의 model)
docker compose -f infra/docker-compose.yml up --build
```

| 주소 | 보이는 것 |
|---|---|
| http://localhost:8000/docs | Swagger (전체 명세) |
| http://localhost:8000/health | `{"ok": true, "service": "api", "version": ...}` |
| http://localhost:8001/health | 모델 컨테이너 — `model_version`·컷(`cut_report`/`cut_intervene`)·`standardized_input: true` |

api 는 기동 시 `api/migrations/*.sql` 을 파일명 순으로 적용하고 `schema_migrations` 에 기록한다(테이블 30개). `DATABASE_URL` 이 없거나 DB 가 안 뜨면 **저장 없이** 동작하고 30초마다 재연결을 시도한다.

Docker 없이: `cd api && pip install -r requirements.txt && uvicorn app.main:app --reload`

### 환경변수

`../.env.example` 에 전부 적혀 있다. compose 가 `.env` 를 읽어 api 컨테이너에 넘긴다. 실제 값(RDS 주소, Bedrock ARN, 식약처 키)은 EC2 의 `.env` 에만 있고 저장소에는 없다.

| 묶음 | 변수 | 비고 |
|---|---|---|
| DB | `DATABASE_URL` | 없으면 저장 없이 동작 |
| 모델 | `MODEL_URL` | 로컬 `http://model:8001`, AWS 는 ML EC2 사설 IP |
| 개인화 훅 | `KBD_PERSONALIZE` | `0` 이면 끔 (기본 켬) |
| 공감챗 | `BEDROCK_PROMPT_ARN` `BEDROCK_REGION` `BEDROCK_HISTORY_VAR` `BEDROCK_CONNECT_TIMEOUT` `BEDROCK_READ_TIMEOUT` `CHAT_DAILY_MAX` `CHAT_FILTER_REPLY` | ARN 없으면 `/v1/chat` 503 → 앱 규칙 응답 폴백 |
| 복약 LUI·DDI | `MEDICATION_LUI_MODEL_ID` `MEDICATION_LAMBDA_NAME` `MEDICATION_DEFAULT_TIMEZONE` `MEDICATION_OCCURRENCE_HORIZON_DAYS` | Lambda 는 Data 담당 |
| 외부 키 | `DATA_GO_KR_KEY` `FOODSAFETY_KEY` | 제품 검색 프록시 |
| AWS | `AWS_REGION` (`AWS_ACCESS_KEY_ID`/`SECRET` 은 EC2 역할 사용 시 비움) | |

### 테스트

```
cd api && pytest          # 정책·창·공감챗·복약 일정 (DB 없이)
cd model && pytest        # validation_samples.json 5건 재현
cd collector && pytest    # Windows 에서만 (pynput)
```
DB 통합 테스트: compose 켠 뒤 `DATABASE_URL=postgresql://carepong:carepong@localhost:5432/carepong pytest api/tests/test_db.py`
GitHub Actions(`.github/workflows/test.yml`)가 push/PR 마다 api·model 테스트를 돈다.

## 구성 (요약)

```
앱(Electron) ─ALB─▶ api (Backend EC2, 컨테이너 1개) ─▶ model (ML EC2 :8001)   과부하 추론
                      │                              ─▶ RDS PostgreSQL 16      저장·개인 기준선
                      │                              ─▶ Bedrock                공감챗 · 복약 LUI 파싱
                      │                              ─▶ Lambda(Data)           복약 DDI 검사
                      │                              ─▶ S3                     raw 입력 업로드
PPG 측정(폰) ─▶ Vercel 측정 웹 → Render 서버 ─▶ RDS/S3 직결 (DA 소유, 백엔드 미경유)
```
그림과 설명은 [architecture.md](architecture.md).

## 데이터 흐름

| 길 | 주기 | 주소 | 비고 |
|---|---|---|---|
| 감지 피처 16개 | **1분마다** (08:30~18:30, 활동 0 창 포함) | `POST /v1/windows` | 서버 추론 → `condition_score`·`prompt`·`prompt_type`. 11/6부터 직전 30분 창이 있으면 **개인화 z-벡터**(shrinkage, carepong-automation)로 판정하고 `model_version` 에 `+kbd` 접미 |
| 팝업 응답·체크인 | 즉시 | `POST /v1/self-reports` | `window_id` 로 예측과 짝 |
| 이벤트(마커 표) | 매일 08:30 또는 앱 시작 시 묶음 | `POST /v1/events/batch` | `client_event_id` 멱등 |
| 공감챗 | 메시지마다 | `POST /v1/chat` | Bedrock 프롬프트 v3, 원문 저장 없음, 하루 상한 |
| 복약 등록·회차·복용기록 | 바뀔 때 / 매일 | `/v1/users/me/medications*` | Bearer 토큰. 서버가 회차 생성, 앱이 알림 |
| 복약 자연어 등록(LUI) | 대화마다 | `/v1/medication-chat/*` | 문장 → 제품·시간·요일 되묻기 → 확인 → 등록. 오류는 한국어 `message`·`error_code` JSON |
| 원시 입력 백업 | 배치 | `/v1/raw/*` | S3 presigned 업로드 |
| 설정·펫 | 바뀔 때 | `/v1/routines` `/v1/pet` | 메모리 스텁. 원본은 앱 로컬 |

서버가 "띄울지" 판단하는 팝업은 **과부하 확인 하나**(`services/policy.py`: 과부하 1순위 · 고정 보충 11:30/14/15:30/17 · 1시간 텀 · 하루 5~8회). 복약·물·스트레칭·휴식 알림과 정기 체크인은 앱이 띄우고 서버는 기록만 받는다.

## 엔드포인트 (44개 = 구현 23 · 메모리 스텁 4 · Planned 17)

| 라우터 | 메서드·주소 | 상태 | 용도 |
|---|---|---|---|
| main | GET /health | 구현 | 생존·버전 |
| enroll | POST /v1/enroll | 구현 | CP 번호 등록 → Bearer 토큰 |
| windows | POST /v1/windows · /v1/windows/batch | 구현 | 1분 창 추론·팝업 판단 (배치 ≤240, 팝업 없음·개인화 없음) |
| self_reports | POST /v1/self-reports | 구현 | 팝업 응답·체크인 라벨 |
| events | POST /v1/events/batch | 구현 | 이벤트 묶음, 중복 ID 무시 |
| config | GET /v1/config | 구현 | 정책값(컷·상한·방해금지·간격) |
| chat | POST /v1/chat | 구현 | 공감챗 (Bedrock) |
| catalog | GET /v1/catalog/search | 구현(Data) | 약·건기식 통합 검색 |
| medications_live | GET/POST /v1/users/me/medications · GET …/today · GET …/medication-adherence · PATCH/DELETE …/{id} · POST …/{id}/intakes · POST …/medication-intakes/batch | 구현 | 복약 2차 운영 8개 ([medication-phase2-api.md](medication-phase2-api.md)) |
| medication_chat | POST /v1/medication-chat/messages · /selections · /confirm · /cancel | 구현 | 복약 LUI 4개 (파싱 Bedrock Nova, DDI 는 DDI Lambda) |
| raw | POST /v1/raw/upload-url · /v1/raw/batches | 구현 | 원시 입력 S3 업로드 |
| routines · pet | GET/PUT /v1/routines · GET/PUT /v1/pet | 메모리 스텁 | 앱 로컬이 원본. 서버 재시작 시 사라짐 |
| relief · routines · pet · medications | /v1/relief-sessions · /v1/routines/logs · /v1/pet/actions · /v1/medications/{id}/logs | Planned | 이벤트(`/events/batch`)로 대체됨 |
| drugs · usage | /v1/drugs/search · /v1/supplements/search · /v1/usage/daily | Planned | 검색은 `/v1/catalog/search` 가 대체 |
| telegram | /v1/telegram/pairings · /webhook | Planned | 1차 제외 확정 |
| ppg | /v1/ppg/sessions · /results · /results/latest | Planned→이관 | PPG 는 DA Render/Vercel 이 RDS 직결. 서버 스텁 미사용 |
| da | /v1/model/version · /model/reload · /windows/export · /baseline/{id} · /labels · /user/{id}/timeline | Planned | DA 운영 API. 모델 교체는 ML EC2 재빌드로 대신함 |

`api/app/routers/medications.py`(1차 메모리 약 등록) 는 2차 `medications_live` 로 대체되어 라우터 등록에서 빠져 있다.

## DB

PostgreSQL 16 (Amazon RDS). 이 저장소의 `api/migrations/001~008` 이 테이블 30개를 만들고(1차 10 · 복약·성분 20), 그 밖에 키보드 개인화 기준선 2개(carepong-automation), PPG 2개(ML), DUR·DDI 작업 테이블·LUI 세션 등이 RDS 에 있다 — 11/10 실측 47개.
표·컬럼·PK/FK 정리본은 팀 내부 자료(xlsx·DBML). 1차 설계 설명은 [db-tables.md](db-tables.md).

## 배포 (EC2 수동)

CI 는 테스트까지만 돈다. 배포는 사람이 EC2 콘솔에서 한 줄 붙여넣기로 한다(`scripts/*_paste.txt`, 메모장으로 열어 전체 복사).

1. `scripts/inspect_ec2_git_paste.txt` — EC2 체크아웃과 origin/main 차이 확인
2. `scripts/deploy_api_paste.txt` — `git merge origin/main` → `docker compose … up -d --build --force-recreate api` → `/health`. 실패 시 자동 `merge --abort`
3. 모델(ML EC2)은 `scripts/ml_ec2_model_update_paste.txt` 로 상태 확인 후 재빌드 (11/9)
4. 배포 뒤 `scripts/verify_kbd_hook_paste.txt`(개인화 훅 가동) · `scripts/smoke.py <ALB 주소>`(8개 스모크)

`.env` 변경(ARN·키)은 `--force-recreate` 가 있어야 반영된다. 워커는 1개(팝업 상태가 메모리) — 배포는 **08:30 전**이나 18:30 후.

## 관련 저장소

| 저장소 | 내용 | 담당 |
|---|---|---|
| [carepong-app](https://github.com/asiae-girls-2026/carepong-app) | Electron 앱. `app/src/main/api.ts` 가 이 서버를 호출 | App |
| [carepong-automation](https://github.com/asiae-girls-2026/carepong-automation) | 키보드 개인화(shrinkage) 기준선 갱신 cron·DEPLOY.md | ML |
| carepong-releases | 앱 설치 파일 배포 | App |
| PPG 측정 웹(Vercel)·서버(Render) | 생체 층. RDS/S3 직결 | ML·Design |
| 데이터레이크·ETL | 이 저장소 `etl/`·`lambda/` + 별도 S3/Glue | Data |

## 규칙

- 앱이 보내는 기록엔 전부 `client_event_id`. 재전송돼도 한 번만 기록
- 시각은 앱 시계(KST, ISO)로. 서버는 받은 시각을 따로 적는다
- 무응답(`timeout`)을 미복용으로 치지 않는다
- 주소·필드·이벤트명 바꾸려면 팀에 말하고 같이 바꾼다 (마커 표·대시보드가 이름을 본다). 바꾼 건 `api-changelog.md` 에 한 줄
- 원시 키 코드·텍스트·창 제목은 어디에도 넣지 않는다. 공감챗·복약 LUI 원문은 저장하지 않는다
- 팝업 상태가 메모리에 있으므로 DB 이관(`user_prompt_state`) 전엔 **워커 1개, 배포는 08:30 전**
- 사용자 CP 번호·접속 시각이 든 점검 결과 파일은 저장소에 올리지 않는다 (팀 내부 보관)
