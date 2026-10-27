-- CarePong medication operations: 일정 timezone 보완 및 조회 인덱스.
-- 기존 복약/회차/이벤트 행은 삭제하지 않는다.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $$
BEGIN
  IF to_regclass('public.user_medication') IS NULL
     OR to_regclass('public.medication_occurrence') IS NULL
     OR to_regclass('public.medication_intake_event') IS NULL THEN
    RAISE EXCEPTION 'Medication phase2 tables are missing; apply 005 first';
  END IF;
END $$;

-- 기존 Electron 일정에는 timezone이 없었으므로 서비스 기본 시간대를 명시적으로 기록한다.
UPDATE public.user_medication
SET schedule_json = schedule_json || '{"timezone":"Asia/Seoul"}'::jsonb,
    updated_at = now()
WHERE schedule_json IS NOT NULL
  AND jsonb_typeof(schedule_json) = 'object'
  AND NOT (schedule_json ? 'timezone');

CREATE INDEX IF NOT EXISTS medication_occurrence_user_status_time_idx
  ON public.medication_occurrence(user_id, status, scheduled_at);

CREATE INDEX IF NOT EXISTS medication_intake_user_time_type_idx
  ON public.medication_intake_event(user_id, scheduled_at, event_type);

CREATE INDEX IF NOT EXISTS user_medication_active_dates_idx
  ON public.user_medication(user_id, active, start_date, end_date);
