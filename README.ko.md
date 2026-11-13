# CarePong backend-service

[English](README.md) | **한국어**

[![test](https://github.com/asiae-girls-2026/backend-service/actions/workflows/test.yml/badge.svg)](https://github.com/asiae-girls-2026/backend-service/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)

**케어퐁(CarePong)** 은 Windows 데스크톱에 사는 케어펫입니다. 키보드·마우스 사용 패턴(1분 집계 피처 16개, 원시 입력 없음)으로 과부하를 추론해 쉬어 가라고 말을 걸고, 복약 알림·자연어 약 등록·공감 챗을 제공합니다.
이 저장소는 그 **서버**입니다. 데스크톱 앱은 [carepong-app](https://github.com/asiae-girls-2026/carepong-app) 에 있습니다.

> 5인 학생 팀의 2개월 프로젝트(2026.09–11)를 학습·재사용 목적으로 공개합니다. 공개 서비스로 운영되지 않습니다.

## 구성

```
Electron 앱 ─ALB─▶ api (FastAPI) ─▶ model (FastAPI, 과부하 추론)
                      ├──▶ PostgreSQL 16 (RDS)
                      ├──▶ Amazon Bedrock   공감 챗 · 복약 자연어 파싱
                      ├──▶ AWS Lambda       약물 상호작용(DDI) 검사
                      └──▶ S3               원시 입력 백업
```

| 폴더 | 내용 |
|---|---|
| `api/` | 메인 API — 감지·팝업 정책·복약·공감 챗·복약 LUI. `migrations/` 가 기동 시 자동 적용 |
| `model/` | 과부하 추론 서버. 표준화(개인화) 입력 지원 |
| `collector/` | 입력 수집기(pynput). 1분 피처 16개를 JSON 으로 출력, 앱에 exe 로 동봉 |
| `infra/` | Docker Compose(로컬·AWS 오버라이드), DDI 정책 SQL |
| `etl/` `lambda/` `deploy/` | 의약품·건기식 적재, DUR/DDI 규칙 파이프라인(Prefect), DDI Lambda, systemd 유닛 |
| `scripts/` `docs/` | 운영 점검 스크립트, 문서 |

## 빨리 켜 보기

```bash
git clone https://github.com/asiae-girls-2026/backend-service.git && cd backend-service
cp .env.example .env            # 비워 둬도 뜬다 (로컬 postgres·model 컨테이너 사용)
docker compose -f infra/docker-compose.yml up --build
```

- Swagger: http://localhost:8000/docs · 서버 상태: http://localhost:8000/health · 모델 상태: http://localhost:8001/health
- 테스트: `cd api && pytest` · `cd model && pytest`
- Bedrock·Lambda·S3 는 AWS 자격 증명이 있을 때만 동작하고, 없으면 해당 기능이 503 으로 응답하며 나머지는 그대로 돈다.

## 문서

| 문서 | 내용 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 구성도, 요청 한 번의 경로, 배포 단위 |
| [docs/operations.md](docs/operations.md) | 환경변수, 데이터 흐름, 엔드포인트 44개, DB, EC2 배포 절차, 규칙 |
| [docs/medication-phase2-api.md](docs/medication-phase2-api.md) | 복약 API 명세 |
| [docs/DDI_Sections_Frontend_Contract.md](docs/DDI_Sections_Frontend_Contract.md) · [docs/ddi/](docs/ddi/) | 약물 상호작용(DDI) 결과 형식·규칙 파이프라인 |
| [docs/db-tables.md](docs/db-tables.md) | DB 테이블 설명 |
| [docs/api-changelog.md](docs/api-changelog.md) | API 변경 이력 |
| [scripts/README.md](scripts/README.md) | 운영 점검·배포 스크립트 |

## 관련 저장소

- [carepong-app](https://github.com/asiae-girls-2026/carepong-app) — Electron 데스크톱 앱
- [carepong-automation](https://github.com/asiae-girls-2026/carepong-automation) — 키보드 개인화(shrinkage) 기준선 갱신

## 기여·보안·라이선스

[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) · [MIT License](LICENSE)
