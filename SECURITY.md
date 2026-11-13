# Security Policy

CarePong is a student project and is **not** operated as a public service. The code is published for learning and reuse; there is no production deployment that accepts reports from the public.

## Reporting a vulnerability

If you find a security issue in this code (for example, a way to bypass the Bearer check, an injection in a SQL path, or a secret committed by mistake), please open a GitHub issue with the label `security`, or use GitHub's private vulnerability reporting on this repository if it is enabled. Do not include real user data in the report.

## What this project does to protect users

- The desktop app never sends raw keystrokes, text, or window titles. Only 16 per-minute aggregate features are uploaded.
- Chat messages (empathy chat, medication LUI) are forwarded to the LLM and **not stored**.
- Users are identified by an app-generated pseudonymous ID (`CP-XXXXXXXX`); no name or contact information is collected.
- Secrets live only in the server's `.env`. `.env.example` documents the variables without values.

---

## 보안 정책 (한국어)

케어퐁은 학생 프로젝트이며 공개 서비스로 운영되지 않습니다. 코드에서 보안 문제를 발견하면 `security` 라벨로 GitHub 이슈를 열어 주세요(실제 사용자 데이터는 넣지 마세요). 앱은 원시 키 입력·텍스트·창 제목을 보내지 않고, 챗 원문은 저장하지 않으며, 사용자는 앱이 만든 가명 ID 로만 식별됩니다.
