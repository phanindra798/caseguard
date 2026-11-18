-- Runs once when the Postgres container is first created: separate databases for tests.
create database caseguard_test owner caseguard;
create database caseguard_worker_test owner caseguard;
