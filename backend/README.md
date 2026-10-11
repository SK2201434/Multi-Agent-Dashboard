# Backend

## Rent billing policy (initial version)

* **Anchor:** each stay's cycles start on the tenant's **actual check-in
  date** (`stays.actual_check_in_at`, business timezone), not the planned
  date. Nothing is billed for any period before occupancy began.
* **Cycles:** cycle *k* is the half-open period
  `[anchor + k months, anchor + (k+1) months)`. Every cycle is computed from
  the original anchor, so a 31 January anchor gives 31 Jan, 28/29 Feb,
  31 Mar, 30 Apr, 31 May, and `period_end` always equals the next cycle's
  `period_start`.
* **Amount:** the full agreed monthly rent (the stay's rent snapshot) for
  every *started* cycle. There is no automatic proration.
* **Due date:** the first day of the cycle.
* **While checked in:** cycles keep being generated (up to 7 days ahead),
  even after the planned check-out date has passed (overstays are billed).
* **After actual check-out:** only cycles that started before the check-out
  date exist (so catch-up still bills a cycle started before leaving, but a
  tenant leaving exactly on a cycle day is not billed another month).
* **Never auto-billed:** reserved, cancelled and no-show stays.
* **Waivers/adjustments:** the owner can waive part or all of the
  *outstanding* rent through an audited adjustment. `amount_due` stays the
  original amount; `effective_amount_due = amount_due - amount_adjusted`;
  `outstanding = effective_amount_due - amount_paid`. A waiver can never
  exceed the outstanding balance (no credit/refund handling: reverse the
  payment instead). Adjustments and payment reversals are append-only.
* **Paid means verified:** only an owner-confirmed claim or an
  owner-recorded payment counts. Pending/rejected claims change nothing.
* **One pending claim** per tenant and obligation (database-enforced);
  after a rejection or confirmation a new one may be submitted.
* **Reminders:** owner when rent is due within 7 days, tenant within 2
  days, and **exactly one overdue notice per recipient per obligation**
  (not daily). Based on effective due and verified payments only.
* **Property deletion** is refused while a property has reserved/checked-in
  stays, pending payment claims or outstanding rent (collect or waive it
  first). After deletion the owner can still *read* the property's
  obligations, payments, claims and adjustments; changes stay closed.

## Scheduled rent job

Obligation generation and reminders are produced by a **batch job**. The
project has no in-process scheduler, so **nothing runs automatically until
you schedule this command** (daily is enough):

```powershell
cd D:\Satish\Multi-Agent-Dashboard\backend
python -m app.jobs.rent_jobs
```

It needs the same environment as the API (`DATABASE_URL`, `JWT_SECRET_KEY`,
optionally `BUSINESS_TIMEZONE`, default `Asia/Kolkata`), read from `.env` in
the working directory. On Windows schedule it with Task Scheduler (or cron
on Linux).

* **Idempotent:** re-running never duplicates an obligation or a
  notification (unique constraints + `ON CONFLICT DO NOTHING`).
* **Concurrent-safe:** rows are written in a fixed order so two instances
  queue instead of deadlocking. If PostgreSQL still reports a deadlock or
  serialization failure, that step is retried on a fresh session (max 4
  attempts, increasing backoff). Other errors, or exhausted retries, make
  the job exit non-zero; it never reports success after a failure.
* **Catch-up:** after an outage the next run creates every missed cycle
  (from the actual check-in date onward) and sends the reminders that are
  still relevant once.

## Tests

Tests run against a separate `<database>_test` PostgreSQL database that is
rebuilt from the Alembic migrations, never the development database:

```powershell
python -m pytest tests -q
```
