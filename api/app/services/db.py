"""DB 연결 + 마이그레이션. DATABASE_URL 없거나 연결 실패면 '저장 없이' 동작한다 (로컬 테스트·DB 없는 환경).
앱 시작 시 migrations/*.sql 을 파일명 순으로 적용하고 schema_migrations 에 기록한다."""
import os, logging, pathlib
from contextlib import contextmanager

log = logging.getLogger("carepong.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "migrations"
_pool = None

def available() -> bool:
    return _pool is not None

def init() -> None:
    """main.py lifespan 에서 1회. 실패해도 앱은 뜬다 (저장만 안 됨)."""
    global _pool
    if not DATABASE_URL:
        log.warning("DATABASE_URL 없음 — 저장 없이 동작"); return
    try:
        from psycopg_pool import ConnectionPool
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=True, timeout=5)
        migrate()
        log.info("DB 연결·마이그레이션 완료")
    except Exception as e:
        log.error("DB 연결 실패 — 저장 없이 동작: %s", e); _pool = None

def close() -> None:
    global _pool
    if _pool: _pool.close(); _pool = None

@contextmanager
def conn():
    with _pool.connection() as c:
        yield c

def migrate() -> None:
    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        raise RuntimeError(f"migrations 폴더에 .sql 이 없음: {MIGRATIONS} (Dockerfile 의 COPY migrations 확인)")
    with conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r[0] for r in c.execute("SELECT filename FROM schema_migrations")}
        for f in files:
            if f.name in done: continue
            c.execute(f.read_text(encoding="utf-8"))
            c.execute("INSERT INTO schema_migrations(filename) VALUES (%s)", (f.name,))
            log.info("migration 적용: %s", f.name)

def ensure_user(cp_number: str, device_id: str = "unknown", app_version: str = "0.0.0") -> str | None:
    """cp_number 로 users 행을 찾거나 만든다. user_id(str) 반환. DB 없으면 None.
    D07 토큰 인증 붙기 전까지 X-User-Id 헤더 값 = cp_number 로 취급."""
    if not available(): return None
    with conn() as c:
        row = c.execute("""INSERT INTO users(cp_number, device_id, app_version) VALUES (%s,%s,%s)
                           ON CONFLICT (cp_number) DO UPDATE SET last_seen_at = now()
                           RETURNING user_id""", (cp_number, device_id, app_version)).fetchone()
        return str(row[0])

def ensure_session(session_id: str, user_id: str, at, app_version: str | None = None) -> None:
    with conn() as c:
        c.execute("""INSERT INTO sessions(session_id, user_id, started_at, ended_at, app_version) VALUES (%s,%s,%s,%s,%s)
                     ON CONFLICT (session_id) DO UPDATE SET ended_at = EXCLUDED.ended_at""",
                  (session_id, user_id, at, at, app_version))
