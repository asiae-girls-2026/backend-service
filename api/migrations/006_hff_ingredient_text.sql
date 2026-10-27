-- C003 원료 원문 보존
-- 기존 데이터 삭제 없음

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE public.hff_product_ingredient
ALTER COLUMN raw_ingredient_name TYPE text;

-- 동일 제품·원료의 중복 적재 방지
CREATE UNIQUE INDEX hff_product_ingredient_dedupe_uq
ON public.hff_product_ingredient (
    hff_product_id,
    md5(raw_ingredient_name)
);
