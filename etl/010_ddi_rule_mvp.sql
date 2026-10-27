-- CarePong DDI MVP: existing table reuse. Run only after reviewing the ETL dry-run.
-- Nonunique lookup indexes only; no new business rows and no changed data.
SET lock_timeout = '3s';
SET statement_timeout = '30s';
CREATE INDEX IF NOT EXISTS idx_ddi_rule_lookup
  ON public.interaction_rule(subject_type, subject_id, object_type, object_id)
  WHERE active = true;
CREATE INDEX IF NOT EXISTS idx_ddi_rule_source
  ON public.interaction_rule(source_dataset, source_record_id);
CREATE INDEX IF NOT EXISTS idx_ddi_med_product_source
  ON public.med_product_ingredient(source_code, med_product_id);
CREATE INDEX IF NOT EXISTS idx_ddi_mapping_approved
  ON public.med_dur_code_mapping(dur_code, mtral_code)
  WHERE review_status = 'APPROVED';
