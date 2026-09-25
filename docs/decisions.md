# Design decisions

A running log of decisions and the tradeoffs behind them. Newest stage at the bottom.

## Stage 1 - Project setup

### FastAPI + sync SQLAlchemy 2.0
FastAPI gives typed request/response handling and OpenAPI docs for free. SQLAlchemy is used in
its synchronous form: FastAPI runs sync endpoints in a thread pool, which is plenty for a
bookkeeping app, and sync code is simpler to write, debug and test than async.
Considered: async SQLAlchemy. Skipped because it adds complexity without a measured need.

### PostgreSQL, also in tests
Tests run against a real Postgres container (`db_test`), not SQLite. Later stages rely on
Postgres behaviour (unique constraints for import dedupe, `ON CONFLICT DO NOTHING`, date
functions in reports), and SQLite would behave differently. The test database uses `tmpfs`,
so it lives in memory and starts clean when the container restarts.

### One transaction per test, rolled back
Each test opens a transaction and rolls it back at the end, so tests never see each other's
data and there is no cleanup code. The session uses `join_transaction_mode="create_savepoint"`,
so application code that calls `commit()` still works inside a test.

### Migrations run in the test setup
The test fixture runs `alembic upgrade head` instead of `Base.metadata.create_all()`. That way
every test run also proves the migrations themselves work.

### Configuration from environment variables
All settings come from environment variables (with an optional `.env` file for local work)
through `pydantic-settings`. The same image can run locally, in CI and later on AWS by changing
env vars only. There is no insecure fallback for secrets; secrets are added in Stage 2.

### Health endpoint checks the database
`GET /api/v1/health` runs `SELECT 1`. It returns 503 if the database is unreachable, so a
container orchestrator or load balancer stops sending traffic to a broken instance.

### Host ports 5434 / 5435
Postgres containers are published on 5434 (dev) and 5435 (test) instead of 5432 to avoid
clashing with a locally installed Postgres.

## Stage 2 - Auth and organizations

### Tenancy model: organization is the tenant, users join through memberships
Books belong to an organization, not a user. A `memberships` table links users to orgs with a
role, with a unique constraint on (user_id, org_id). Signup creates the user and their first
organization in one transaction. Only the `owner` role is used for now; the column exists so a
bookkeeper or teammate could be added later without a schema redesign.

### The org is in the URL: `/api/v1/orgs/{org_id}/...`
Every org-scoped route uses one dependency, `get_org_context`, which checks the caller's
membership in the org from the URL. Services then filter every query by that `org_id`.
Considered: putting the active org inside the JWT. Skipped because switching orgs would need a
new token, and a URL is easier to see, log and test.

### Non-members get 404, not 403
A 403 would confirm that the organization exists. Returning the same 404 as for a missing id
means nobody can probe which org ids are in use. A test checks both responses are identical.

### JWT bearer tokens (stateless), 60-minute expiry, no refresh token
The token holds only the user id and expiry, signed with HS256. The server keeps no session
state, which suits running several API containers later. Tradeoffs: a token can't be revoked
before it expires, and there is no refresh token, so users log in again after an hour.
`JWT_SECRET_KEY` has no default: the app refuses to start without one (tested).

### Passwords hashed with argon2 (pwdlib)
Argon2id is the current recommended password hash. pwdlib is used because passlib is no
longer maintained.

### Login doesn't leak which emails exist
Wrong email and wrong password return the same 401 and message. When the email doesn't exist,
the code still runs one password verification against a dummy hash, so the response time is
about the same too.

### Emails are lower-cased before storing
The unique constraint on `users.email` is then effectively case-insensitive. Signup relies on
that constraint (catching the IntegrityError) rather than "check, then insert", so two
simultaneous signups with the same email can't both succeed.

### Services raise plain Python errors; one handler maps them to HTTP
Services raise `NotFoundError`, `ConflictError` or `AuthenticationError` and never import
FastAPI. `app/main.py` maps each to a status code in one place. That keeps business logic
testable without HTTP and makes error responses consistent.

### Deliberately not built yet
- Audit log (who changed what, when). All writes go through the services layer, so it can be
  added in one place later.
- Refresh tokens, logout/revocation, password reset, inviting other users to an org.

## Stage 3 - Chart of accounts and ledger

### Double-entry with one signed amount per line
Each journal line stores one `amount_cents` number: positive for a debit, negative for a credit.
"Debits equal credits" then simply means "the lines sum to 0", and every balance or report is
a plain `SUM()`. The API still shows separate debit and credit columns, like a bookkeeper would
expect; the conversion happens in one place (`schemas/journal.py`).
Considered: separate `debit` and `credit` columns. Skipped because every query would need
`debit - credit` and a check that only one of them is set.

### Money is integer cents (BIGINT), and the API only accepts whole-number cents
No floats anywhere. The request schema uses `StrictInt`, so `12.5` or `"1250"` is rejected
instead of silently converted. An upper limit ($10 billion) keeps values far from overflow.

### Balance rules live in one service function
`ledger_service.validate_lines` checks: at least two lines, no zero lines, the sum is exactly
0, and every account belongs to this organization and is active. It is the only code path
that creates ledger rows; bank import, invoices and payments will call `record_entry` too.
`record_entry` doesn't commit, so a later feature (e.g. "record payment") can post an entry
and update an invoice in one transaction.
A database CHECK also rejects zero-amount lines.

### Posted entries are immutable; fixes are reversing entries
There is no update or delete route for journal entries. As a safety net inside the app, an
SQLAlchemy event raises `ImmutableLedgerError` if any code tries to update or delete a
ledger row through the ORM (tested). A reversal is a new entry with every line's sign flipped;
the original stays, so the history shows both the mistake and the fix.
- An entry can be reversed at most once: `reverses_entry_id` is UNIQUE, which also catches
  two reversal requests racing each other.
- A reversal can't be reversed (post a new entry instead) and can't be dated before the
  original.
Considered: a Postgres trigger that blocks UPDATE/DELETE. Skipped to keep all rules in Python
where they're easy to read and test. Tradeoff: a raw SQL `UPDATE` would bypass the ORM guard.

### Balances in "natural" sign
The raw balance (debits minus credits) is negative for income, liability and equity
accounts. The API flips the sign for those, so $500 of sales shows as 500. The trial balance
instead shows each balance in a debit or credit column, and its two totals must match.

### Default chart of accounts, seeded explicitly
Every new organization gets 17 accounts numbered by type (1xxx assets ... 5xxx expenses).
Seeding is an explicit call in `org_service.add_organization`, not a signal, so it is obvious
when it happens. `subtype` marks the checking and accounts-receivable accounts that later
features need to find.

### Account rules
- The account type can't change after creation: that would silently move past activity to a
  different section of the reports.
- An account with a balance can't be deactivated, or its money would disappear from view.
- Codes are unique per organization (database constraint), not globally.

### Tenancy for ledger data
Journal lines carry `org_id` as well, so balance queries filter lines directly. Accounts or
entries of another organization are reported as "not found", both through their own URLs and
when referenced from inside your own org (e.g. posting to someone else's account id).

## Stage 4 - Bank import

### Parsing is pure; importing touches the database
`csv_service.py` only turns bytes into validated rows (no database), so it is unit tested
with plain inputs. `import_service.py` does the database work. Ideas kept from the SmartLedger
reference project: Excel BOM handling, `$`/comma/parentheses amounts, per-row error lists,
an import history record with counts.

### Column mapping instead of one fixed format
Banks export different headers, and some use one signed Amount column while others use
separate Withdrawal/Deposit columns. The upload takes a mapping (`date`, `description`, and
either `amount` or `debit` + `credit`). A preview endpoint shows headers and sample rows and
guesses the mapping from common header names.

### Dates: ISO and US formats are guessed, day-first is not
`03/04/2026` could be March 4 or April 3. Guessing wrong silently books transactions in the
wrong month, so only unambiguous defaults are tried (ISO, then US month-first) and a
day-first bank must pass `date_format: "%d/%m/%Y"`.

### Amounts: Decimal, and more than 2 decimals is an error
Amounts are parsed with `Decimal` and converted to integer cents. `-1.005` is reported as an
error rather than rounded, so an import never invents or loses a cent.

### Idempotency: fingerprint + unique constraint + ON CONFLICT DO NOTHING
Each valid row gets `sha256(account | date | amount | normalized description | occurrence)`.
`bank_transactions` has UNIQUE(org_id, account_id, fingerprint), and rows are inserted with
`ON CONFLICT DO NOTHING`, so the database itself skips rows that already exist. This also
holds when two uploads of the same file run at the same moment, which a "check first, then
insert" approach would get wrong.
- Occurrence number: the SmartLedger reference used (date, amount, description) and silently
  dropped real repeats, like two $4.50 coffees on the same day. Here the Nth identical row in a
  file gets occurrence N, so both coffees are kept, and re-importing the file produces the
  same fingerprints again.
- Known limitation: if one export cuts off in the middle of a day and the next export includes
  the whole day, identical same-day rows can shift occurrence numbers and one may be imported
  twice. Rare (it needs identical date, amount and description), and visible in review.
- The account is part of the key: the same coffee in two different bank accounts is two
  transactions.

### Two different normalizations
- For the fingerprint: only lower-case and collapse spaces. `CHECK #1041` and `CHECK #1042`
  must stay different.
- For the vendor (`normalized_vendor`): strip processor prefixes (`SQ *`, `TST*`, `PAYPAL *`,
  `POS`), store numbers and long digit runs. Used for categorization rules and the vendor cache
  in Stage 5, never for dedupe.

### Imported rows are not in the ledger yet
Imported rows land in `bank_transactions` with status `for_review`. They become journal
entries when the user categorizes them (Stage 5). Posting them immediately to an
"Uncategorized" account would need a reversing entry for every re-categorization, because
posted entries are immutable.

### Limits and storage
5 MB and 10,000 rows per file. Inserts are batched 1,000 rows at a time (Postgres allows
65,535 parameters per statement). The raw file is saved through a `FileStorage` interface
(local disk now, S3 later), keyed by the file's SHA-256. Only accounts marked as bank
accounts accept imports.
