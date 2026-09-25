-- A second, empty database for the test suite, so a test run can wipe its
-- tables without touching the data you are using. Runs once, when the Docker
-- volume is first created (compose.yaml).
CREATE DATABASE joblens_test OWNER joblens;
