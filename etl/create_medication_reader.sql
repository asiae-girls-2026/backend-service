CREATE ROLE carepong_medication_reader
LOGIN
PASSWORD 'carepong';

GRANT CONNECT ON DATABASE carepong_beta
TO carepong_medication_reader;

GRANT USAGE ON SCHEMA public
TO carepong_medication_reader;

GRANT SELECT ON ALL TABLES IN SCHEMA public
TO carepong_medication_reader;
