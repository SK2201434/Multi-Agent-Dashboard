"""Scheduled-job entry point (run daily from cron / Task Scheduler):

    python -m app.jobs.rent_jobs          # from the backend directory

There is no in-process scheduler in this project, so nothing runs until you
schedule this command. It performs two independent steps, each in its own
transaction:

1. generate missing monthly rent obligations (see rent_service for the
   billing policy);
2. send in-app reminders/overdue notices (see reminder_service).

Both steps are idempotent, deterministic in the order they write rows, and
safe to run concurrently with another instance. If PostgreSQL still aborts
a step with a deadlock or serialization failure, the step is retried on a
fresh session a bounded number of times; if it keeps failing the error is
raised (non-zero exit) - it is never reported as success.
"""

import time
from collections.abc import Callable
from typing import TypeVar

from psycopg import errors as pg_errors
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services import reminder_service, rent_service

T = TypeVar("T")

MAX_ATTEMPTS = 4
BASE_DELAY_SECONDS = 0.2
_TRANSIENT = (pg_errors.DeadlockDetected, pg_errors.SerializationFailure)


def is_transient(exc: BaseException) -> bool:
    return isinstance(exc, DBAPIError) and isinstance(exc.orig, _TRANSIENT)


def run_with_retry(
    step: Callable[[Session], T],
    *,
    attempts: int = MAX_ATTEMPTS,
    base_delay: float = BASE_DELAY_SECONDS,
    session_factory: Callable[[], Session] = SessionLocal,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run step(session) in its own transaction; retry only transient
    deadlock/serialization failures, on a fresh session, with backoff.
    Anything else, or exhausting the attempts, propagates."""
    for attempt in range(1, attempts + 1):
        session = session_factory()
        try:
            return step(session)
        except DBAPIError as exc:
            session.rollback()
            if not is_transient(exc) or attempt == attempts:
                raise
            sleep(base_delay * attempt)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    raise AssertionError("unreachable")  # pragma: no cover


def run_once() -> tuple[int, dict[str, int]]:
    created = run_with_retry(rent_service.generate_monthly_obligations)
    sent = run_with_retry(reminder_service.run_sweep)
    return created, sent


def main() -> None:
    created, sent = run_once()
    print(f"obligations_created={created} notifications={sent}")


if __name__ == "__main__":
    main()
