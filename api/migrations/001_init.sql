-- 케어퐁 DB 1차 스키마 — docs/db-tables.md (설계 v1, 2026-10-02) 그대로. PostgreSQL 16.
-- api 컨테이너가 켜질 때 services/db.py 가 migrations/*.sql 을 순서대로 적용한다 (적용 기록: schema_migrations).
-- 복약(medications·med_product) 저장 로직은 정화 담당 — 테이블은 여기서 같이 만든다.

CREATE TABLE IF NOT EXISTS schema_migrations (
  filename    text PRIMARY KEY,
  applied_at  timestamptz NOT NULL DEFAULT now()
);

-- ── 사용자·세션 ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
  user_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cp_number    varchar(12) NOT NULL UNIQUE,          -- 앱 로컬 발급 CP-XXXXXXXX
  device_id    varchar(64) NOT NULL,
  app_version  varchar(16) NOT NULL,
  os           varchar(16) NOT NULL DEFAULT 'windows',
  token_hash   varchar(128),                         -- D07 인증 확정 후 (정화)
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id  varchar(32) PRIMARY KEY,               -- 앱 발급 ses_… (앱 실행 1회)
  user_id     uuid NOT NULL REFERENCES users(user_id),
  started_at  timestamptz NOT NULL,
  ended_at    timestamptz,
  app_version varchar(16)
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

-- ── 감지 ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS windows (
  window_id       varchar(32) PRIMARY KEY,           -- 앱 발급 win_…
  user_id         uuid NOT NULL REFERENCES users(user_id),
  session_id      varchar(32) NOT NULL REFERENCES sessions(session_id),
  window_start    timestamptz NOT NULL,              -- 앱 시계 KST. 자정 리셋·일 집계 기준
  window_sec      smallint NOT NULL DEFAULT 60,
  features        jsonb NOT NULL,                    -- 판정 16개
  activity_zero   boolean NOT NULL,
  dnd             boolean NOT NULL DEFAULT false,
  condition_score numeric(4,1),                      -- 활동0·모델실패 null
  prob            numeric(6,4),
  level           varchar(10),
  model_version   varchar(16),
  imputed         text[],
  prompt_type     varchar(10) NOT NULL DEFAULT 'none',
  client_event_id varchar(64) NOT NULL UNIQUE,       -- 멱등키
  received_at     timestamptz NOT NULL DEFAULT now(),
  raw_url         text                               -- 원시 타이밍 S3 포인터 (2차)
);
CREATE INDEX IF NOT EXISTS ix_windows_user_start   ON windows(user_id, window_start);   -- 혜윤 🔴
CREATE INDEX IF NOT EXISTS ix_windows_user_session ON windows(user_id, session_id);

CREATE TABLE IF NOT EXISTS self_reports (
  report_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES users(user_id),
  window_id        varchar(32) REFERENCES windows(window_id),   -- 체크인은 null
  trigger          varchar(10) NOT NULL,             -- morning/spike/random/checkin
  yesno            smallint,                         -- 1 예 / 0 아니요 / null 무응답
  level_1to5       smallint,
  status           varchar(10) NOT NULL DEFAULT 'answered',     -- answered/dismissed/ignored/pending
  prompt_time      timestamptz,
  clicked_at       timestamptz,
  latency_sec      integer,
  question_version varchar(8) NOT NULL DEFAULT 'q1',
  answered_at      timestamptz NOT NULL,
  client_event_id  varchar(64) NOT NULL UNIQUE,
  received_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_self_reports_user_time ON self_reports(user_id, answered_at);
CREATE INDEX IF NOT EXISTS ix_self_reports_window    ON self_reports(window_id);

-- ── 이벤트 (채원 마커 표) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
  event_id        bigserial PRIMARY KEY,
  user_id         uuid NOT NULL REFERENCES users(user_id),
  client_event_id varchar(64) NOT NULL UNIQUE,
  event           varchar(40) NOT NULL,
  screen          varchar(8),
  occurred_at     timestamptz NOT NULL,
  props           jsonb NOT NULL DEFAULT '{}',
  app_version     varchar(16) NOT NULL,
  received_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_events_user_time  ON events(user_id, occurred_at);          -- 혜윤 🔴
CREATE INDEX IF NOT EXISTS ix_events_event_time ON events(event, occurred_at);

-- ── 일 집계 (서버 산출, 연우 시트14-②) ───────────────────────────
CREATE TABLE IF NOT EXISTS daily_usage (
  user_id uuid NOT NULL REFERENCES users(user_id),
  day     date NOT NULL,
  care_actions int NOT NULL DEFAULT 0, walks int NOT NULL DEFAULT 0, walks_refused int NOT NULL DEFAULT 0,
  chat_messages int NOT NULL DEFAULT 0, mood_checks int NOT NULL DEFAULT 0, hearts_filled smallint NOT NULL DEFAULT 0,
  med_taken int NOT NULL DEFAULT 0, med_missed int NOT NULL DEFAULT 0,
  water_count int NOT NULL DEFAULT 0, stretch_count int NOT NULL DEFAULT 0, rest_count int NOT NULL DEFAULT 0,
  relief_offered int NOT NULL DEFAULT 0, relief_accepted int NOT NULL DEFAULT 0, relief_completed int NOT NULL DEFAULT 0,
  pet_level smallint, pet_generation smallint, furniture_unlocked smallint, days_to_next_character int,
  active_minutes int NOT NULL DEFAULT 0, collector_active_min int NOT NULL DEFAULT 0, prompts_shown smallint NOT NULL DEFAULT 0,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, day)
);

-- ── 복약 / 건기식 ───────────────────────────────────────────────
-- 기준: 최신 복약·건기식 DB 설계
--
-- [중요]
-- med_product / hff_product / med_patient_info 는 기존 데이터 파이프라인(Alembic)이
-- 생성·적재하는 Catalog/Data 영역 테이블이다.
-- 현재 운영 RDS에도 이미 존재하며 실제 데이터가 적재되어 있으므로
-- backend-service 의 SQL migration 에서는 재정의/재생성하지 않는다.
--
-- 현재 운영 RDS 기준:
--   med_product.med_product_id : BIGINT
--   hff_product.hff_product_id : BIGINT
--
-- user_medication 은 최신 설계의 사용자 복용 등록 테이블을 따른다.
-- 단, 논리 설계서의 user_id BIGINT는 현재 운영 서버 users.user_id(UUID)와 연결해야 하므로
-- 물리 구현에서만 UUID로 맞춘다. 제품/복약 논리 구조는 최신 설계를 유지한다.

CREATE TABLE IF NOT EXISTS user_medication (
  user_medication_id bigserial PRIMARY KEY,
  user_id            uuid NOT NULL REFERENCES users(user_id),

  item_type          varchar(20) NOT NULL,
  med_product_id     bigint REFERENCES med_product(med_product_id),
  hff_product_id     bigint REFERENCES hff_product(hff_product_id),

  start_date         date,
  end_date           date,
  dose_text          varchar(300),
  schedule_json      jsonb,
  active             boolean NOT NULL DEFAULT true,

  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT ck_user_medication_item_type
    CHECK (item_type IN ('DRUG', 'HFF')),

  CONSTRAINT ck_user_medication_product_ref
    CHECK (
      (item_type = 'DRUG' AND med_product_id IS NOT NULL AND hff_product_id IS NULL)
      OR
      (item_type = 'HFF' AND hff_product_id IS NOT NULL AND med_product_id IS NULL)
    ),

  CONSTRAINT ck_user_medication_date_range
    CHECK (
      end_date IS NULL
      OR start_date IS NULL
      OR end_date >= start_date
    )
);

CREATE INDEX IF NOT EXISTS ix_user_medication_user
  ON user_medication(user_id);

CREATE INDEX IF NOT EXISTS ix_user_medication_user_active
  ON user_medication(user_id, active);

CREATE INDEX IF NOT EXISTS ix_user_medication_med_product
  ON user_medication(med_product_id)
  WHERE med_product_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_user_medication_hff_product
  ON user_medication(hff_product_id)
  WHERE hff_product_id IS NOT NULL;

-- ── 백업 스냅샷·정책 상태 ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS pet_state (
  user_id uuid PRIMARY KEY REFERENCES users(user_id),
  name varchar(12) NOT NULL, character_slot smallint NOT NULL DEFAULT 1, generation smallint NOT NULL DEFAULT 1,
  level smallint NOT NULL DEFAULT 1, xp int NOT NULL DEFAULT 0,
  happiness smallint NOT NULL DEFAULT 70, health smallint NOT NULL DEFAULT 70, energy smallint NOT NULL DEFAULT 70,
  hearts_total int NOT NULL DEFAULT 0, furniture_stage smallint NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS routine_settings (
  user_id uuid PRIMARY KEY REFERENCES users(user_id),
  water_goal_cups smallint NOT NULL DEFAULT 8, water_interval_min smallint NOT NULL DEFAULT 90,
  stretch_interval_min smallint NOT NULL DEFAULT 60, rest_interval_min smallint NOT NULL DEFAULT 75,
  dnd_schedule_enabled boolean NOT NULL DEFAULT true, dnd_start time NOT NULL DEFAULT '18:30', dnd_end time NOT NULL DEFAULT '08:30',
  pet_opacity smallint NOT NULL DEFAULT 100, pet_motion_minimal boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_prompt_state (
  user_id uuid PRIMARY KEY REFERENCES users(user_id),
  cur_date date, day_count smallint NOT NULL DEFAULT 0,
  last_prompt_at timestamptz, first_activity_at timestamptz,
  did_morning boolean NOT NULL DEFAULT false,
  ov_hist smallint[] NOT NULL DEFAULT '{}', slots_done text[] NOT NULL DEFAULT '{}',
  updated_at timestamptz NOT NULL DEFAULT now()
);
