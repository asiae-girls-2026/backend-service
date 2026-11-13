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

## Quick start

```bash
git clone https://github.com/asiae-girls-2026/backend-service.git && cd backend-service
cp .env.example .env            # empty values are fine (local postgres + model containers)
docker compose -f infra/docker-compose.yml up --build
```

- Swagger: http://localhost:8000/docs · API health: http://localhost:8000/health · model health: http://localhost:8001/health
- Tests: `cd api && pytest` · `cd model && pytest`
- Bedrock, Lambda and S3 features need AWS credentials; without them those endpoints answer 503 and everything else keeps working.

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Diagram, the path of one request, deployment units (Korean) |
| [docs/operations.md](docs/operations.md) | Environment variables, data flows, all 44 endpoints, DB, EC2 deployment, rules (Korean) |
| [docs/medication-phase2-api.md](docs/medication-phase2-api.md) | Medication API reference |
| [docs/DDI_Sections_Frontend_Contract.md](docs/DDI_Sections_Frontend_Contract.md) · [docs/ddi/](docs/ddi/) | DDI result format and rule pipeline |
| [docs/db-tables.md](docs/db-tables.md) | Database tables |
| [docs/api-changelog.md](docs/api-changelog.md) | API change log |
| [scripts/README.md](scripts/README.md) | Operations and deployment scripts |

Most documents are written in Korean; the Swagger UI (`/docs`) is the API reference and is self-describing.

## Related repositories

- [carepong-app](https://github.com/asiae-girls-2026/carepong-app) — Electron desktop app
- [carepong-automation](https://github.com/asiae-girls-2026/carepong-automation) — keyboard personalization (shrinkage) baseline refresh

## Contributing, security, license

[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) · [MIT License](LICENSE)
