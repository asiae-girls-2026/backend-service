BEGIN;

ALTER TABLE public.med_product_ingredient
    ADD COLUMN IF NOT EXISTS mtral_sn text,
    ADD COLUMN IF NOT EXISTS tamt_seq text,
    ADD COLUMN IF NOT EXISTS source_qnt_raw text;

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_med_product_ingredient_source
ON public.med_product_ingredient
    (med_product_id, mtral_sn, tamt_seq);

COMMIT;
