"""Starts and controls the REAL Case API and escalation worker for the harness.

One stack per harness run (fresh database + fresh Kafka topic), reset between scenarios.
Processes are children of the harness, so a scenario can stop the worker, crash it with a
fail point, or restart the Case API, exactly like a real outage.
"""
from __future__ import annotations

import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[2]
API_JAR = ROOT / "case-api/target/case-api-0.1.0-SNAPSHOT.jar"
WORKER_JAR = ROOT / "escalation-worker/target/escalation-worker-0.1.0-SNAPSHOT.jar"


@dataclass
class StackConfig:
    pg_host: str = os.getenv("PGHOST", "localhost")
    pg_user: str = os.getenv("PGUSER", "caseguard")
    pg_password: str = os.getenv("PGPASSWORD", "caseguard")
    kafka: str = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
    api_port: int = 8094
    database: str = "caseguard_harness"
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def topic(self) -> str:
        return f"escalation.requested.harness-{self.run_id}"

    @property
    def jdbc_url(self) -> str:
        return f"jdbc:postgresql://{self.pg_host}:5432/{self.database}"

    @property
    def checkpoint_url(self) -> str:
        """Where the agent keeps conversations (same database, LangGraph's own tables)."""
        return f"postgresql://{self.pg_user}:{self.pg_password}@{self.pg_host}:5432/{self.database}"

    def dsn(self, database: str | None = None) -> str:
        return (f"host={self.pg_host} user={self.pg_user} password={self.pg_password} "
                f"dbname={database or self.database}")


class Stack:
    def __init__(self, config: StackConfig | None = None, log_dir: Path | None = None):
        self.cfg = config or StackConfig()
        self.log_dir = log_dir or Path(os.getenv("TMPDIR", "/tmp")) / f"caseguard-harness-{self.cfg.run_id}"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.api: subprocess.Popen | None = None
        self.worker: subprocess.Popen | None = None
        self._worker_starts = 0
        self._api_starts = 0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        for jar in (API_JAR, WORKER_JAR):
            if not jar.exists():
                raise RuntimeError(f"missing {jar}: build it with ./mvnw -q package -DskipTests")
        with psycopg.connect(self.cfg.dsn("postgres"), autocommit=True) as conn:
            conn.execute(f"drop database if exists {self.cfg.database}")
            conn.execute(f"create database {self.cfg.database}")
        self.start_api()          # runs the Flyway migrations + seed data
        self.start_worker()

    def stop(self) -> None:
        for p in (self.worker, self.api):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()

    @property
    def api_url(self) -> str:
        return f"http://localhost:{self.cfg.api_port}"

    def _env(self, fail_point: str | None) -> dict:
        env = dict(os.environ, DB_URL=self.cfg.jdbc_url, DB_USER=self.cfg.pg_user,
                   DB_PASSWORD=self.cfg.pg_password, KAFKA_BOOTSTRAP=self.cfg.kafka,
                   CASEGUARD_TOPICS_ESCALATION_REQUESTED=self.cfg.topic)
        env.pop("FAIL_POINT", None)
        if fail_point:
            env["FAIL_POINT"] = fail_point
        return env

    def start_api(self, fail_point: str | None = None) -> None:
        self.stop_api()
        self._api_starts += 1
        log = open(self.log_dir / f"case-api-{self._api_starts}.log", "w")
        env = self._env(fail_point) | {"PORT": str(self.cfg.api_port)}
        self.api = subprocess.Popen(["java", "-jar", str(API_JAR)], env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.api.poll() is not None:
                raise RuntimeError(f"case-api exited during startup, see {log.name}")
            try:
                if httpx.get(f"{self.api_url}/actuator/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError("case-api did not become healthy")

    def stop_api(self) -> None:
        if self.api and self.api.poll() is None:
            self.api.terminate()
            self.api.wait(timeout=20)

    def wait_api_exit(self, timeout: float = 60) -> int:
        return self.api.wait(timeout=timeout)

    def start_worker(self, fail_point: str | None = None) -> None:
        self.stop_worker()
        self._worker_starts += 1
        log = open(self.log_dir / f"worker-{self._worker_starts}.log", "w")
        env = self._env(fail_point) | {"SPRING_KAFKA_CONSUMER_GROUP_ID": f"worker-harness-{self.cfg.run_id}"}
        self.worker = subprocess.Popen(["java", "-jar", str(WORKER_JAR)], env=env, stdout=log, stderr=subprocess.STDOUT)
        # Ready = partitions assigned (Spring Kafka logs it), so no event is published before we listen.
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.worker.poll() is not None:
                raise RuntimeError(f"worker exited during startup, see {log.name}")
            if "partitions assigned" in Path(log.name).read_text(errors="ignore"):
                return
            time.sleep(0.5)
        raise RuntimeError("worker did not get its Kafka partitions")

    def stop_worker(self) -> None:
        if self.worker and self.worker.poll() is None:
            self.worker.terminate()
            self.worker.wait(timeout=20)

    def wait_worker_exit(self, timeout: float = 60) -> int:
        return self.worker.wait(timeout=timeout)

    def worker_logs(self) -> str:
        return "\n".join(p.read_text(errors="ignore") for p in sorted(self.log_dir.glob("worker-*.log")))

    # ------------------------------------------------------------------ data
    def sql(self, query: str):
        with psycopg.connect(self.cfg.dsn(), autocommit=True) as conn:
            return conn.execute(query).fetchone()[0]

    def execute(self, statement: str) -> None:
        with psycopg.connect(self.cfg.dsn(), autocommit=True) as conn:
            conn.execute(statement)

    def wait_until(self, query: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.sql(query):
                    return True
            except psycopg.Error:
                pass
            time.sleep(0.5)
        return False

    def reset(self) -> None:
        """Back to seed state between scenarios. Waits for in-flight work so nothing leaks over."""
        self.wait_until("select count(*) = 0 from outbox where published_at is null", timeout=10)
        self.wait_until("""select count(*) = 0 from escalations e
                           where e.status = 'REQUESTED'
                             and exists (select 1 from outbox o where o.aggregate_id = e.id)""", timeout=5)
        with psycopg.connect(self.cfg.dsn(), autocommit=True) as conn:
            conn.execute("truncate follow_up_tasks, processed_events, outbox, escalations, approvals")
            conn.execute("update cases set status = 'OPEN', version = 0")
