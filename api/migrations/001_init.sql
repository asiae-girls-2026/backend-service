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

-- ── 복약·건기식 (정화 최신 설계) ──────────────────────────────────
-- 설계 기준: DB_테이블구조_설계.xlsx
-- 구현 방식만 backend-service 기준(PostgreSQL/psycopg/SQL migration)으로 반영.
-- 제품 Master → 통합 성분 Master → 제품-성분 N:M → 안전성/상호작용 Rule → 사용자 복약 목록 순서.
-- pg_trgm은 /v1/catalog/search 의 EXACT → CONTAINS → FUZZY 검색을 위한 운영 의존성.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 1) 의약품 제품 Master
CREATE TABLE IF NOT EXISTS med_product (
  med_product_id    bigserial PRIMARY KEY,
  item_seq          varchar(30) NOT NULL UNIQUE,    -- 품목기준코드
  product_name      varchar(300) NOT NULL,
  manufacturer_name varchar(300),
  etc_otc_code      varchar(50),
  dosage_form       varchar(100),
  route             varchar(100),
  permit_date       date,
  permit_status     varchar(30),
  source_updated_at timestamp,
  synced_at         timestamp NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_med_product_name
  ON med_product(product_name);

CREATE INDEX IF NOT EXISTS ix_med_product_name_trgm
  ON med_product USING gin (product_name gin_trgm_ops);


-- 2) 건강기능식품 제품 Master
CREATE TABLE IF NOT EXISTS hff_product (
  hff_product_id    bigserial PRIMARY KEY,
  prdlst_report_no  varchar(50) NOT NULL UNIQUE,    -- 품목제조번호
  product_name      varchar(300) NOT NULL,
  manufacturer_name varchar(300),
  function_text     text,
  intake_method     text,
  caution_text      text,
  raw_material_text text,
  source_updated_at timestamp,
  synced_at         timestamp NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_hff_product_name
  ON hff_product(product_name);

CREATE INDEX IF NOT EXISTS ix_hff_product_name_trgm
  ON hff_product USING gin (product_name gin_trgm_ops);


-- 3) 통합 표준 성분 Master
CREATE TABLE IF NOT EXISTS canonical_ingredient (
  ingredient_id     bigserial PRIMARY KEY,
  canonical_name_ko varchar(300) NOT NULL,
  canonical_name_en varchar(300),
  normalized_key    varchar(300) NOT NULL UNIQUE,
  domain_type       varchar(20) NOT NULL,           -- DRUG / HFF / BOTH
  ingredient_type   varchar(50),
  active            boolean NOT NULL DEFAULT true,
  created_at        timestamp NOT NULL DEFAULT now(),
  updated_at        timestamp NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingredient_alias (
  alias_id          bigserial PRIMARY KEY,
  ingredient_id     bigint NOT NULL REFERENCES canonical_ingredient(ingredient_id),
  alias_text        varchar(300) NOT NULL,
  normalized_alias  varchar(300) NOT NULL,
  source_dataset    varchar(50),
  mapping_method    varchar(30),                    -- EXACT / ALIAS / SEMANTIC / MANUAL
  confidence        decimal(5,4),
  status            varchar(20) NOT NULL            -- CANDIDATE / APPROVED / REJECTED
);

CREATE INDEX IF NOT EXISTS ix_ingredient_alias_ingredient
  ON ingredient_alias(ingredient_id);

CREATE INDEX IF NOT EXISTS ix_ingredient_alias_normalized
  ON ingredient_alias(normalized_alias);


-- 4) 제품-성분 N:M
CREATE TABLE IF NOT EXISTS med_product_ingredient (
  med_product_id        bigint NOT NULL REFERENCES med_product(med_product_id),
  ingredient_id         bigint NOT NULL REFERENCES canonical_ingredient(ingredient_id),
  source_ingredient_name varchar(300) NOT NULL,
  amount                decimal(18,6),
  unit                  varchar(50),
  source_code           varchar(100),
  mapping_status        varchar(20) NOT NULL,       -- MATCHED / REVIEW / FAILED
  PRIMARY KEY (med_product_id, ingredient_id)
);

CREATE INDEX IF NOT EXISTS ix_med_product_ingredient_ingredient
  ON med_product_ingredient(ingredient_id);

CREATE TABLE IF NOT EXISTS hff_product_ingredient (
  hff_product_id     bigint NOT NULL REFERENCES hff_product(hff_product_id),
  ingredient_id      bigint NOT NULL REFERENCES canonical_ingredient(ingredient_id),
  raw_ingredient_name varchar(500) NOT NULL,
  normalized_name    varchar(300),
  mapping_method     varchar(30),                   -- EXACT / ALIAS / SEMANTIC / MANUAL
  match_confidence   decimal(5,4),
  mapping_status     varchar(20) NOT NULL,          -- MATCHED / REVIEW / FAILED
  PRIMARY KEY (hff_product_id, ingredient_id)
);

CREATE INDEX IF NOT EXISTS ix_hff_product_ingredient_ingredient
  ON hff_product_ingredient(ingredient_id);


-- 5) 의약품 소비자 설명
CREATE TABLE IF NOT EXISTS med_patient_info (
  med_product_id     bigint PRIMARY KEY REFERENCES med_product(med_product_id),
  efficacy_text      text,
  usage_text         text,
  warning_text       text,
  interaction_text   text,
  side_effect_text   text,
  storage_text       text,
  source_updated_at  timestamp
);


-- 6) 건기식 기능성 원료 기준
CREATE TABLE IF NOT EXISTS hff_ingredient_standard (
  standard_id       bigserial PRIMARY KEY,
  ingredient_id     bigint NOT NULL REFERENCES canonical_ingredient(ingredient_id),
  source_type       varchar(20) NOT NULL,           -- I0040 / I2710
  approval_no       varchar(100),
  function_text     text,
  daily_intake_min  decimal(18,6),
  daily_intake_max  decimal(18,6),
  unit              varchar(50),
  caution_text      text,
  source_record_id  varchar(150)
);

CREATE INDEX IF NOT EXISTS ix_hff_ingredient_standard_ingredient
  ON hff_ingredient_standard(ingredient_id);


-- 7) DUR 정규화 안전성 Rule
CREATE TABLE IF NOT EXISTS med_safety_rule (
  rule_id               bigserial PRIMARY KEY,
  rule_type             varchar(50) NOT NULL,
  ingredient_id         bigint REFERENCES canonical_ingredient(ingredient_id),
  paired_ingredient_id  bigint REFERENCES canonical_ingredient(ingredient_id),
  target_group          varchar(100),
  condition_value       varchar(300),
  warning_text          text NOT NULL,
  source_record_id      varchar(150) NOT NULL,
  active                boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS ix_med_safety_rule_ingredient
  ON med_safety_rule(ingredient_id);

CREATE INDEX IF NOT EXISTS ix_med_safety_rule_paired
  ON med_safety_rule(paired_ingredient_id);


-- 8) 서비스 조합 Rule
CREATE TABLE IF NOT EXISTS interaction_rule (
  interaction_rule_id bigserial PRIMARY KEY,
  subject_type        varchar(30) NOT NULL,         -- INGREDIENT / DRUG_CLASS
  subject_id          bigint NOT NULL,
  object_type         varchar(30) NOT NULL,         -- INGREDIENT / DRUG_CLASS
  object_id           bigint NOT NULL,
  interaction_type    varchar(30) NOT NULL,         -- DRUG_DRUG / DRUG_HFF
  severity            varchar(30),
  action_type         varchar(30) NOT NULL,         -- CONTRAINDICATED / CAUTION 등
  evidence_text       text NOT NULL,
  source_dataset      varchar(50) NOT NULL,
  source_record_id    varchar(150) NOT NULL,
  review_status       varchar(20) NOT NULL,         -- APPROVED / PENDING / REJECTED
  active              boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS ix_interaction_rule_subject
  ON interaction_rule(subject_type, subject_id);

CREATE INDEX IF NOT EXISTS ix_interaction_rule_object
  ON interaction_rule(object_type, object_id);


-- 9) 사용자 복약/건기식 등록 목록
-- 최신 설계의 user_medication을 유지하되, 운영 서버 users.user_id가 UUID이므로
-- user_id의 물리 타입만 UUID로 맞춘다. 제품/스케줄 구조는 최신 설계를 그대로 사용한다.
CREATE TABLE IF NOT EXISTS user_medication (
  user_medication_id bigserial PRIMARY KEY,
  user_id            uuid NOT NULL REFERENCES users(user_id),
  item_type          varchar(20) NOT NULL,          -- DRUG / HFF
  med_product_id     bigint REFERENCES med_product(med_product_id),
  hff_product_id     bigint REFERENCES hff_product(hff_product_id),
  start_date         date,
  end_date           date,
  dose_text          varchar(300),
  schedule_json      json,
  active             boolean NOT NULL DEFAULT true,
  CONSTRAINT ck_user_medication_product
    CHECK (
      (item_type = 'DRUG' AND med_product_id IS NOT NULL AND hff_product_id IS NULL)
      OR
      (item_type = 'HFF' AND hff_product_id IS NOT NULL AND med_product_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_user_medication_user
  ON user_medication(user_id);

CREATE INDEX IF NOT EXISTS ix_user_medication_med_product
  ON user_medication(med_product_id);

CREATE INDEX IF NOT EXISTS ix_user_medication_hff_product
  ON user_medication(hff_product_id);


-- 10) 매핑 검수 운영
CREATE TABLE IF NOT EXISTS mapping_candidate (
  candidate_id        bigserial PRIMARY KEY,
  entity_type         varchar(30) NOT NULL,
  raw_value           varchar(500) NOT NULL,
  candidate_entity_id bigint,
  match_method        varchar(30) NOT NULL,         -- EXACT / ALIAS / SEMANTIC
  confidence          decimal(5,4),
  source_dataset      varchar(50) NOT NULL,
  status              varchar(20) NOT NULL,         -- PENDING / APPROVED / REJECTED
  created_at          timestamp NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mapping_audit (
  mapping_id          bigserial PRIMARY KEY,
  entity_type         varchar(30) NOT NULL,
  raw_value           varchar(500) NOT NULL,
  canonical_entity_id bigint NOT NULL,
  method              varchar(30) NOT NULL,         -- EXACT / ALIAS / SEMANTIC / MANUAL
  confidence          decimal(5,4),
  approved_by         varchar(100),
  approved_at         timestamp
);


-- 11) 원천 추적·동기화 운영
CREATE TABLE IF NOT EXISTS sync_run (
  sync_run_id      bigserial PRIMARY KEY,
  dataset_code     varchar(50) NOT NULL,
  started_at       timestamp NOT NULL,
  finished_at      timestamp,
  status           varchar(20) NOT NULL,            -- SUCCESS / FAILED / PARTIAL
  fetched_count    integer NOT NULL,
  inserted_count   integer NOT NULL,
  updated_count    integer NOT NULL,
  unchanged_count  integer NOT NULL,
  failed_count     integer NOT NULL,
  error_message    text
);

CREATE TABLE IF NOT EXISTS source_provenance (
  provenance_id    bigserial PRIMARY KEY,
  entity_type      varchar(30) NOT NULL,            -- PRODUCT / INGREDIENT / RULE
  entity_id        bigint NOT NULL,
  dataset_code     varchar(50) NOT NULL,
  source_record_id varchar(150) NOT NULL,
  source_updated_at timestamp,
  fetched_at       timestamp NOT NULL,
  raw_hash         varchar(64),
  sync_run_id      bigint REFERENCES sync_run(sync_run_id)
);

CREATE INDEX IF NOT EXISTS ix_source_provenance_entity
  ON source_provenance(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS ix_source_provenance_dataset
  ON source_provenance(dataset_code, source_record_id);


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
