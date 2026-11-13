# CarePong backend-service

**English** | [한국어](README.ko.md)

[![test](https://github.com/asiae-girls-2026/backend-service/actions/workflows/test.yml/badge.svg)](https://github.com/asiae-girls-2026/backend-service/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)

**CarePong** is a desktop care-pet for Windows. It infers cognitive overload from keyboard and mouse usage patterns (16 per-minute aggregate features, no raw input), nudges the user to take a break, and offers medication reminders, natural-language medication registration and an empathy chat.
This repository is the **server side**. The desktop app lives in [carepong-app](https://github.com/asiae-girls-2026/carepong-app).

> Built by a five-person student team over two months (Sep–Nov 2026) and published for learning and reuse. It is not operated as a public service.

## Architecture

```
Electron app ─ALB─▶ api (FastAPI) ─▶ model (FastAPI, overload inference)
                      ├──▶ PostgreSQL 16 (RDS)
                      ├──▶ Amazon Bedrock   empathy chat · medication NL parsing
                      ├──▶ AWS Lambda       drug–drug interaction (DDI) check
                      └──▶ S3               raw input backup
```

| Folder | Contents |
|---|---|
| `api/` | Main API — sensing, popup policy, medication, empathy chat, medication LUI. `migrations/` are applied on startup |
| `model/` | Overload inference server. Accepts standardized (personalized) input |
| `collector/` | Input collector (pynput). Emits 16 per-minute features as JSON; shipped with the app as an exe |
| `infra/` | Docker Compose (local + AWS override), DDI policy SQL |
| `etl/` `lambda/` `deploy/` | Drug/supplement ingestion, DUR/DDI rule pipeline (Prefect), DDI Lambda, systemd units |
| `scripts/` `docs/` | Operations scripts, documentation |

## Requirements

| What | Version / notes |
|---|---|
| Docker + Docker Compose v2 | Recommended. Brings up api, model and PostgreSQL in one command |
| Python 3.12 | For running without Docker. Dependencies in `api/requirements.txt` (FastAPI, psycopg3, boto3) |
| PostgreSQL 16 | The only supported database. Plain SQL, no ORM (`api/app/services/db.py`). DDL lives in `api/migrations/*.sql` and is applied automatically when the api starts |
| AWS account (optional) | Bedrock (empathy chat, medication parsing), Lambda (DDI), S3 (raw). Without it those features answer 503 |

Tested on: Windows 11 (development, Docker Desktop) · Ubuntu on EC2 **t3.micro** (api) + **t3.small** (model) + RDS **db.t4g.micro** (production, ~80 users, 400k one-minute windows).

## Quick start

```bash
git clone https://github.com/asiae-girls-2026/backend-service.git && cd backend-service
cp .env.example .env            # empty values are fine (local postgres + model containers)
docker compose -f infra/docker-compose.yml up --build
```

- Swagger: http://localhost:8000/docs · API health: http://localhost:8000/health · model health: http://localhost:8001/health
- Tests: `cd api && pytest` · `cd model && pytest`
- Bedrock, Lambda and S3 features need AWS credentials; without them those endpoints answer 503 and everything else keeps working.
- Without Docker: `cd api && pip install -r requirements.txt && uvicorn app.main:app --reload` (set `DATABASE_URL`; with no DB the api runs without persistence).

## Configuration

| To change | Edit |
|---|---|
| DB URL, model URL, AWS keys/ARN, daily chat limit | `.env` (see `.env.example`) |
| Ports, containers, AWS override | `infra/docker-compose.yml`, `infra/docker-compose.aws.yml` |
| Overload popup policy (cut 63, 20 of 30 min, 1-hour gap, 5–8/day, fixed slots) | constants at the top of `api/app/services/policy.py` |
| Policy values served to the app (`GET /v1/config`) | `api/app/routers/config.py` |
| Swap the inference model | replace `model/predict.py`, bump `MODEL_VERSION` |
| Add or change tables | add `api/migrations/NNN_*.sql` (applied in filename order, recorded in `schema_migrations`) |
| Collector features, window length | `collector/collector.py` (`--window` seconds) |

## Deployment (how we ran it)

One EC2 instance runs only the api container via `docker compose -f infra/docker-compose.yml -f infra/docker-compose.aws.yml up -d --build api`; the model runs on a second EC2, the database is RDS, an ALB sits in front. CI runs tests only; deployment is a manual paste of `scripts/deploy_api_paste.txt` into the EC2 console. Details in [docs/operations.md](docs/operations.md).

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Diagram, the path of one request, deployment units (Korean) |
| [docs/operations.md](docs/operations.md) | Environment variables, data flows, all 44 endpoints, DB, EC2 deployment, model server, operations scripts, rules (Korean) |
| [docs/medication-phase2-api.md](docs/medication-phase2-api.md) | Medication API reference |
| [docs/DDI_Sections_Frontend_Contract.md](docs/DDI_Sections_Frontend_Contract.md) · [docs/ddi/](docs/ddi/) | DDI result format and rule pipeline |
| [docs/db-tables.md](docs/db-tables.md) | Database tables |
| [docs/api-changelog.md](docs/api-changelog.md) | API change log |

Most documents are written in Korean; the Swagger UI (`/docs`) is the API reference and is self-describing.

## Related repositories

- [carepong-app](https://github.com/asiae-girls-2026/carepong-app) — Electron desktop app (client). Installers are on that repository's Releases tab
- [carepong-automation](https://github.com/asiae-girls-2026/carepong-automation) — keyboard personalization (shrinkage) baseline refresh. To be merged into this repository

## Contributing, security, license

[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) · [MIT License](LICENSE)
