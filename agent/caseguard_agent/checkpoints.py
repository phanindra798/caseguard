"""Where conversation state lives between turns.

- No CASEGUARD_CHECKPOINT_DB: in memory (tests, quick demos). A restart forgets every conversation.
- CASEGUARD_CHECKPOINT_DB=postgresql://...: PostgreSQL. A conversation paused for approval survives
  an agent restart and resumes with the same operation id.
"""
from __future__ import annotations

import os

from langgraph.checkpoint.memory import MemorySaver


def make_checkpointer(url: str | None = None):
    url = url or os.getenv("CASEGUARD_CHECKPOINT_DB")
    if not url:
        return MemorySaver()
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(url, max_size=10, open=True,
                          kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    saver = PostgresSaver(pool)
    saver.setup()            # creates its tables on first use (idempotent)
    return saver
