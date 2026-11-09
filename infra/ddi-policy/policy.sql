-- CarePong DDI mapping policy
-- Existing PostgreSQL implementation snapshot
-- Does not modify clinical source mappings.


CREATE TABLE IF NOT EXISTS
public.ddi_mapping_policy (
    has_m_code BOOLEAN NOT NULL,
    has_d_code BOOLEAN NOT NULL,
    has_rule BOOLEAN NOT NULL,
    action_code TEXT NOT NULL,
    result_policy TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (has_m_code, has_d_code, has_rule)
);

INSERT INTO public.ddi_mapping_policy (has_m_code,has_d_code,has_rule,action_code,result_policy) VALUES (TRUE,TRUE,TRUE,'CHECK_APPROVED_RULE','RULE_BASED') ON CONFLICT (has_m_code,has_d_code,has_rule) DO UPDATE SET action_code=EXCLUDED.action_code,result_policy=EXCLUDED.result_policy;
INSERT INTO public.ddi_mapping_policy (has_m_code,has_d_code,has_rule,action_code,result_policy) VALUES (TRUE,TRUE,FALSE,'NO_RULE_FOUND','UNCERTAIN') ON CONFLICT (has_m_code,has_d_code,has_rule) DO UPDATE SET action_code=EXCLUDED.action_code,result_policy=EXCLUDED.result_policy;
INSERT INTO public.ddi_mapping_policy (has_m_code,has_d_code,has_rule,action_code,result_policy) VALUES (TRUE,FALSE,TRUE,'RESOLVE_M_TO_D','PENDING_MAPPING') ON CONFLICT (has_m_code,has_d_code,has_rule) DO UPDATE SET action_code=EXCLUDED.action_code,result_policy=EXCLUDED.result_policy;
INSERT INTO public.ddi_mapping_policy (has_m_code,has_d_code,has_rule,action_code,result_policy) VALUES (TRUE,FALSE,FALSE,'RESOLVE_D_CODE','UNCERTAIN') ON CONFLICT (has_m_code,has_d_code,has_rule) DO UPDATE SET action_code=EXCLUDED.action_code,result_policy=EXCLUDED.result_policy;
INSERT INTO public.ddi_mapping_policy (has_m_code,has_d_code,has_rule,action_code,result_policy) VALUES (FALSE,TRUE,TRUE,'VERIFY_PRODUCT_D','RULE_BASED') ON CONFLICT (has_m_code,has_d_code,has_rule) DO UPDATE SET action_code=EXCLUDED.action_code,result_policy=EXCLUDED.result_policy;
INSERT INTO public.ddi_mapping_policy (has_m_code,has_d_code,has_rule,action_code,result_policy) VALUES (FALSE,TRUE,FALSE,'NO_RULE_FOUND','UNCERTAIN') ON CONFLICT (has_m_code,has_d_code,has_rule) DO UPDATE SET action_code=EXCLUDED.action_code,result_policy=EXCLUDED.result_policy;
INSERT INTO public.ddi_mapping_policy (has_m_code,has_d_code,has_rule,action_code,result_policy) VALUES (FALSE,FALSE,TRUE,'RESOLVE_PRODUCT_MAPPING','PENDING_MAPPING') ON CONFLICT (has_m_code,has_d_code,has_rule) DO UPDATE SET action_code=EXCLUDED.action_code,result_policy=EXCLUDED.result_policy;
INSERT INTO public.ddi_mapping_policy (has_m_code,has_d_code,has_rule,action_code,result_policy) VALUES (FALSE,FALSE,FALSE,'INSUFFICIENT_EVIDENCE','UNCERTAIN') ON CONFLICT (has_m_code,has_d_code,has_rule) DO UPDATE SET action_code=EXCLUDED.action_code,result_policy=EXCLUDED.result_policy;

-- Policy evaluator function
CREATE OR REPLACE FUNCTION public.ddi_evaluate_mapping_policy(p_a bigint, p_b bigint)
 RETURNS jsonb
 LANGUAGE sql
 STABLE
AS $function$
WITH flags AS (
    SELECT
        (
            SELECT COUNT(*) = 2
            FROM public.med_product
            WHERE med_product_id IN (p_a, p_b)
        ) AS products_valid,

        EXISTS (
            SELECT 1
            FROM public.med_product_ingredient
            WHERE med_product_id = p_a
              AND source_code ~ '^M[0-9]+$'
        ) AS m_a,

        EXISTS (
            SELECT 1
            FROM public.med_product_ingredient
            WHERE med_product_id = p_b
              AND source_code ~ '^M[0-9]+$'
        ) AS m_b,

        EXISTS (
            SELECT 1
            FROM public.med_dur_product_mapping
            WHERE med_product_id = p_a
        ) AS d_a,

        EXISTS (
            SELECT 1
            FROM public.med_dur_product_mapping
            WHERE med_product_id = p_b
        ) AS d_b,

        EXISTS (
            SELECT 1
            FROM public.med_dur_product_mapping a
            JOIN public.med_dur_product_mapping b
              ON a.med_product_id = p_a
             AND b.med_product_id = p_b
            JOIN public.med_dur_ingredient_rule_stage r
              ON (
                   (a.dur_code = r.dur_code_a
                    AND b.dur_code = r.dur_code_b)
                OR (a.dur_code = r.dur_code_b
                    AND b.dur_code = r.dur_code_a)
              )
            WHERE r.rule_type = 'DDI'
              AND r.review_status = 'APPROVED'
              AND r.active = TRUE
        ) AS has_rule
),
state AS (
    SELECT *,
           (m_a AND m_b) AS has_m_code,
           (d_a AND d_b) AS has_d_code
    FROM flags
)
SELECT jsonb_build_object(
    'product_ids', jsonb_build_array(p_a, p_b),
    'products_valid', s.products_valid,
    'product_a_has_m', s.m_a,
    'product_b_has_m', s.m_b,
    'product_a_has_d', s.d_a,
    'product_b_has_d', s.d_b,
    'has_m_code', s.has_m_code,
    'has_d_code', s.has_d_code,
    'has_rule', s.has_rule,
    'action_code',
        CASE WHEN NOT s.products_valid
             THEN 'INVALID_PRODUCTS'
             ELSE p.action_code END,
    'result_policy',
        CASE WHEN NOT s.products_valid
             THEN 'UNCERTAIN'
             ELSE p.result_policy END,
    'approved_scope_required', TRUE,
    'coverage', 'INCOMPLETE',
    'policy_only', TRUE
)
FROM state s
LEFT JOIN public.ddi_mapping_policy p
  ON p.has_m_code = s.has_m_code
 AND p.has_d_code = s.has_d_code
 AND p.has_rule = s.has_rule
$function$;

GRANT SELECT ON public.ddi_mapping_policy
TO carepong_medication_reader;

GRANT EXECUTE ON FUNCTION
public.ddi_evaluate_mapping_policy(BIGINT,BIGINT)
TO carepong_medication_reader;

