# CarePong backend-service

[English](README.md) | **한국어**

[![test](https://github.com/asiae-girls-2026/backend-service/actions/workflows/test.yml/badge.svg)](https://github.com/asiae-girls-2026/backend-service/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)

**케어퐁(CarePong)** 은 Windows 데스크톱에 사는 케어펫입니다. 키보드·마우스 사용 패턴(1분 집계 피처 16개. 입력한 글자·창 제목은 수집하지 않음)으로 과부하를 추론해 쉬어 가라고 말을 걸고, 복약 알림·자연어 약 등록·공감 챗을 제공합니다.
이 저장소는 그 **서버**입니다. 데스크톱 앱은 [carepong-app](https://github.com/asiae-girls-2026/carepong-app) 에 있습니다.

> 학생 팀 프로젝트(2026년 9–11월)를 학습·재사용 목적으로 공개합니다. 공개 서비스로 운영되지 않습니다.

## 구성

```
Electron 앱 ─ALB─▶ api (FastAPI) ─▶ model (FastAPI, 과부하 추론)
                      ├──▶ PostgreSQL 16 (RDS)
                      ├──▶ Amazon Bedrock   공감 챗 · 복약 자연어 파싱
                      ├──▶ AWS Lambda       약물 상호작용(DDI) 검사
                      └──▶ S3               입력 다이내믹스 배치 보관(ML 재학습용)
```

| 폴더 | 내용 |
|---|---|
| `api/` | 메인 API — 감지·팝업 정책·복약·공감 챗·복약 LUI. `migrations/` 가 기동 시 자동 적용 |
| `model/` | 과부하 추론 서버. 표준화(개인화) 입력 지원 |
| `collector/` | 입력 수집기(pynput). 1분 피처 16개를 JSON 으로 출력, 앱에 exe 로 동봉 |
| `infra/` | Docker Compose(로컬·AWS 오버라이드), DDI 정책 SQL |
| `etl/` `lambda/` `deploy/` | 의약품·건기식 적재, DUR/DDI 규칙 파이프라인(Prefect), DDI Lambda, systemd 유닛 |
| `scripts/` `docs/` | 운영 점검 스크립트, 문서 |

## 필요한 것

| 구분 | 버전·내용 |
|---|---|
| Docker + Docker Compose v2 | 권장 경로. 이것만 있으면 api·model·PostgreSQL 이 한 번에 뜬다 |
| Python 3.12 | Docker 없이 돌릴 때. 의존성은 `api/requirements.txt`(FastAPI·psycopg3·boto3) |
| PostgreSQL 16 | 유일하게 지원하는 DB. ORM 없이 SQL 을 직접 쓰며(`api/app/services/db.py`), DDL 은 `api/migrations/*.sql` 에 있고 api 기동 시 자동 적용된다 |
| AWS 계정 (선택) | Bedrock(공감 챗·복약 파싱), Lambda(DDI), S3(raw 배치). 없으면 해당 기능만 오류 응답하고 나머지는 동작 |

돌려 본 환경: Windows 11(개발, Docker Desktop) · Ubuntu EC2 **t3.micro**(api) + **t3.small**(model) + RDS **db.t4g.micro**(운영, 내부 베타 약 30명·1분 창 약 40만 건).

## 빨리 켜 보기

```bash
git clone https://github.com/asiae-girls-2026/backend-service.git && cd backend-service
cp .env.example .env            # 비워 둬도 뜬다 (로컬 postgres·model 컨테이너 사용)
docker compose -f infra/docker-compose.yml up --build
```

- Swagger: http://localhost:8000/docs · 서버 상태: http://localhost:8000/health · 모델 상태: http://localhost:8001/health
- 테스트: `cd api && pytest` · `cd model && pytest`
- Bedrock·Lambda·S3 는 AWS 자격 증명이 있을 때만 동작한다. 없으면 공감 챗·복약 파싱은 503, DDI 검사는 502, raw 업로드는 실패로 응답하고 나머지(감지·복약 일정·펫)는 그대로 돈다.
- Docker 없이: `cd api && pip install -r requirements.txt && uvicorn app.main:app --reload` (DB 는 `DATABASE_URL` 로 연결, 없으면 저장 없이 동작).

## 바꿔 쓰려면 (설정 파일)

| 바꾸고 싶은 것 | 파일 |
|---|---|
| DB 주소, 모델 서버 주소, AWS 키·ARN, 하루 챗 상한 | `.env` (`.env.example` 참고) |
| 포트, 컨테이너 구성, 운영(AWS) 오버라이드 | `infra/docker-compose.yml`, `infra/docker-compose.aws.yml` |
| 과부하 팝업 정책 (컷 63, 30분 중 20분, 1시간 텀, 하루 5~8회, 고정 시각) | `api/app/services/policy.py` 상단 상수 |
| 앱에 내려주는 정책값 (`GET /v1/config`) | `api/app/routers/config.py` |
| 추론 모델 교체 | `model/predict.py` 교체 + `MODEL_VERSION` 환경변수 |
| 테이블 추가·변경 | `api/migrations/NNN_*.sql` 추가 (파일명 순 적용, `schema_migrations` 에 기록) |
| 수집 피처·창 길이 | `collector/collector.py` (`--window` 초) |

## 배포 (우리가 한 방식)

EC2 한 대에서 `docker compose -f infra/docker-compose.yml -f infra/docker-compose.aws.yml up -d --build api` 로 api 컨테이너만 띄우고, 모델은 다른 EC2, DB 는 RDS, 앞에 ALB. CI 는 테스트까지만 돌고 배포는 `scripts/deploy_api_paste.txt` 를 EC2 콘솔에 붙여넣는 수동 절차다. 자세한 건 [docs/operations.md](docs/operations.md).

## 문서

| 문서 | 내용 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 구성도, 요청 한 번의 경로, 배포 단위 |
| [docs/operations.md](docs/operations.md) | 환경변수, 데이터 흐름, 엔드포인트 44개, DB, EC2 배포 절차, 모델 서버, 운영 스크립트, 규칙 |
| [docs/medication-phase2-api.md](docs/medication-phase2-api.md) | 복약 API 명세 |
| [docs/DDI_Sections_Frontend_Contract.md](docs/DDI_Sections_Frontend_Contract.md) · [docs/ddi/](docs/ddi/) | 약물 상호작용(DDI) 결과 형식·규칙 파이프라인 |
| [docs/db-tables.md](docs/db-tables.md) | DB 테이블 설명 |
| [docs/api-changelog.md](docs/api-changelog.md) | API 변경 이력 |

## 관련 저장소

- [carepong-app](https://github.com/asiae-girls-2026/carepong-app) — Electron 데스크톱 앱(클라이언트). 설치 파일은 그 저장소의 Releases 탭
- [carepong-automation](https://github.com/asiae-girls-2026/carepong-automation) — 키보드 개인화(shrinkage) 기준선 갱신. 이 저장소로 합칠 예정

## 기여·보안·라이선스

[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) · [MIT License](LICENSE)
