SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE public.hff_product_ingredient
ALTER COLUMN normalized_name TYPE text;
