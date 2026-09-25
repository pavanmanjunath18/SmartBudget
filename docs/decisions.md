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

## Stage 5 - Categorization

### Suggestion order: rules, then vendor cache, then LLM
1. Rules the user wrote (e.g. description contains "aws" -> Software). Checked by priority,
   lowest number first.
2. The vendor cache: the account the user last confirmed for this cleaned-up vendor name.
3. The LLM, only for transactions still unmatched, in one batched request.
Each step is cheaper and more trustworthy than the next, so the LLM sees as little as
possible. The fake LLM in the tests records its calls, and tests prove that rule and cache
matches never reach it.

### Nothing is posted without a person
Every source only creates a pending suggestion. The user accepts it, rejects it, or picks an
account directly. Accepting runs the same `categorize` function as picking manually, which
posts a journal entry through `ledger_service.record_entry` (so every Stage 3 rule applies):
money out debits the category and credits the bank; money in the reverse. Because amounts are
signed, both cases are the same two lines: bank gets `+amount`, category gets `-amount`.

### Only confirmed choices go into the vendor cache
The cache is written when a user posts a transaction, never from an LLM answer. A wrong
guess therefore can't spread to every future transaction from that vendor. The cache is per
organization, so one business's categories never leak into another's suggestions. The
update is a single `INSERT ... ON CONFLICT DO UPDATE`, safe when two requests race.

### "Accept and create rule"
Accepting with `create_rule: true` adds a `vendor equals <vendor>` rule, so the next import
of that vendor is matched by the rule. Rules are settings, not financial records, so they can
be edited and deleted freely.

### LLM behind a provider interface
`CategorySuggester.suggest(transactions, accounts) -> {transaction_id: account_id}`.
Implementations: `NoLLMSuggester` (default, no calls), `AnthropicSuggester`, and a fake in
tests. Tests never call a real LLM.
- The Anthropic provider uses structured outputs (`messages.parse` with a Pydantic schema),
  so the answer is JSON in a known shape.
- Its answers are still not trusted: an id is kept only if it is a transaction we asked about
  and an allowed account of this organization (not a bank account, not another org's). Tested
  with a deliberately misbehaving fake.
- Only the description, the cleaned vendor and the direction (in/out) are sent, not amounts or
  account numbers. The prompt tells the model to treat descriptions as data, not instructions.
- Any API error, connection error or refusal returns "no suggestions" and is logged. The LLM is
  a convenience; the review queue works without it.
- The model is a setting (`LLM_MODEL`, default `claude-opus-5`). A cheaper model such as Claude
  Haiku 4.5 can be configured if cost matters more than accuracy; that choice hasn't been
  measured here.

### Acceptance rate per source
Each suggestion keeps its source, status, who decided and when. `GET /suggestions/stats`
reports accepted / rejected / pending per source and `accepted / (accepted + rejected)`.
Choosing a different account than the pending suggestion counts as a rejection. No acceptance
numbers are claimed in these docs: they depend on real usage and haven't been measured.

### Posting twice is prevented with a row lock
`categorize` loads the bank transaction with `SELECT ... FOR UPDATE` and requires status
`for_review`. Two simultaneous requests for the same transaction are serialized: the second
sees `posted` and gets 409, so the same bank line can't create two journal entries.

## Stage 6 - Invoicing and accounts receivable

### Accrual accounting: income is recorded when the invoice is sent
Sending an invoice posts debit Accounts Receivable / credit income (one credit per income
account used on the lines). Recording a payment posts debit Bank / credit Accounts
Receivable. So revenue shows up in the month the work was billed, and the customer's debt is
visible on the balance sheet until it's paid. Both go through `ledger_service.record_entry`.

### Drafts are outside the ledger
A draft can be edited or deleted freely because nothing has been posted. Once sent, the
invoice can't be edited or deleted (tested). Voiding a sent invoice (a reversing entry plus
a `void` status) is a natural next step but was left out to keep the MVP scope.

### "Overdue" is computed, not stored
An invoice is overdue when it is sent, past its due date and still has a balance. That is
calculated when the invoice is read, so there is no nightly job to flip statuses and the
value can never be stale. Stored statuses are only draft, sent and paid.

### Partial payments; the invoice row is locked while paying
Payments can be partial; the invoice becomes `paid` when the balance due reaches 0. A
payment larger than the balance due is rejected. `record_payment` loads the invoice with
`SELECT ... FOR UPDATE`, so two payments recorded at the same moment are processed one after
the other and can't together exceed the balance.

### Quantities are decimals, money is still integer cents
`quantity` is `NUMERIC(10,2)` so "1.5 hours" works. A line amount is
`quantity x unit_price_cents`, rounded half-up to a whole cent once per line, and the invoice
total is the sum of the rounded lines. Tested with values that land exactly on half a cent.

### Invoice numbers: highest + 1, protected by a unique constraint
Numbers are per organization. Two drafts created at the same moment could compute the same
number; `UNIQUE(org_id, number)` rejects the second, which retries once with a fresh number.
Considered: a per-org counter row locked with `FOR UPDATE`. Skipped as more machinery than
this needs. Tradeoff: deleting the newest draft frees its number for reuse; deleting an older
draft leaves a gap.

### AR aging is computed from dates, and it ties out to the ledger
`GET /reports/ar-aging?as_of=` takes every sent or paid invoice issued on or before `as_of`
and subtracts only payments dated on or before `as_of`. An invoice paid next week still shows
as owed in a report for today. Buckets: current (not yet due), 1-30, 31-60, 61-90 and over 90
days past due. A test builds invoices in every bucket and checks that the aging total equals
the Accounts Receivable balance on the same date: the subledger and the ledger agree.

## Stage 7 - Reports

### Reports are SQL sums over the ledger, nothing is stored
Profit & loss, balance sheet, cash flow and monthly totals are each one or two `SUM ...
GROUP BY` queries over journal lines. There are no stored totals to fall out of sync, and a
reversal automatically cancels its original in every report. Accounts that net to zero in
the period are left out of the output.

### Balance sheet: current earnings instead of closing entries
Real bookkeeping closes income and expenses into retained earnings at year end. This MVP
doesn't post closing entries, so the balance sheet computes "current earnings" (all income
minus all expenses up to the date) and shows it in equity. The response includes
`is_balanced` (assets == liabilities + equity), and a test checks it on several dates.

### Cash flow: a simplified direct-method summary
For every entry that touches a bank account in the period, the entry's other lines explain
where the cash came from or went. Because each entry sums to zero, the cash effect of a
non-bank line is minus its amount. Effects are grouped by that account:
- income, expenses and accounts receivable -> operating
- liabilities and equity (credit card paydowns, owner money in and out) -> financing
- other assets -> investing
Transfers between two bank accounts have no other lines and net to nothing. This is a summary
for a small business owner, not a GAAP statement of cash flows (which uses the indirect method
and finer classifications). Tests check that opening cash + net change == closing cash.

### Tested against a hand-worked fixture
`tests/test_reports.py` posts a known two-month set of transactions (owner investment, rent,
credit card, invoice and partial collection, owner draw, a mistake plus its reversal, cash
sale, meals, and one entry outside the range). Every expected number in the assertions was
worked out by hand from that list.

### Monthly totals for the dashboard
`/reports/monthly` groups income and expenses by `date_trunc('month', entry_date)` in one
query, for the income-vs-expenses chart.

## Stage 8 - Reconciliation

### Matching instead of double counting
Some money reaches the ledger before its bank line is imported: a customer payment recorded
against an invoice, or a check written and entered by hand. When that bank line arrives,
categorizing it would record the money a second time. Instead it is *matched*: its status
becomes `matched` and it is linked to the existing entry. No new entry is created (tested).
- Candidates: entries with a line on the same bank account for exactly the same amount, dated
  within 7 days, and not already linked to another bank line. Closest date first.
- `bank_transactions.journal_entry_id` is UNIQUE, so one entry stands for one bank line,
  including when two requests race.
- A match can be undone until the transaction is reconciled; a categorization can't (its
  entry is immutable; fix it with a reversal).

### Reconciliation compares the statement balance with the ledger
The user enters the statement's ending date and balance. The summary shows the ledger balance
of that bank account on the same date, the difference, and two lists that explain it:
- `bank_only`: imported lines still in review (in the bank, not in the books yet);
- `ledger_only`: ledger lines on the bank account that no bank line is linked to (in the
  books, not yet seen by the bank, e.g. an uncleared check).
`ledger_only` starts at the first imported bank line: before any import there is nothing to
compare against, so an opening-balance entry is not flagged forever.

### Completing requires a zero difference
`POST /reconciliation/complete` refuses unless statement balance == ledger balance. It then
stamps `reconciled_at` on every linked bank line up to the statement date. Reconciled lines
can't be unmatched.

### Kept simple on purpose
There is no reconciliation-history table: the state is the `reconciled_at` timestamp on
each bank line. Considered: a `reconciliations` table (statement date, balance, who
completed it) and per-line clearing of ledger entries without bank lines. Skipped for the MVP;
both are straightforward to add later.

## Stage 9 - Frontend

### Small, standard stack
React + TypeScript on Vite, TanStack Query for server data (caching, loading states and
refetching after a change), React Router for pages, Recharts for the one chart. No component
library and no global state library: the only shared client state is "who is logged in and
which organization is selected", which lives in one React context.

### Same origin through a proxy, so no CORS
The browser only talks to `/api/...` on the frontend's own origin. In development Vite
proxies that to the API on port 8000; in Docker, nginx does the same. The backend therefore
needs no CORS configuration at all.

### Money in the browser: integer and string math only
The UI shows and accepts dollars, but the API only takes integer cents. `src/lib/money.ts`
converts both ways with string parsing and integer arithmetic (never `parseFloat`), rejects
more than two decimals, and has unit tests (Vitest), including a round-trip test.

### Token in localStorage (tradeoff)
The JWT is kept in `localStorage` so a page reload keeps the user logged in. The downside:
any XSS bug could read it. The safer alternative is an httpOnly, SameSite cookie set by the
API, which also needs CSRF protection. Kept simple for the MVP and written down here; React
escapes rendered text by default, and the app never injects raw HTML.

### Pages
- Dashboard: cash in bank (sum of bank account balances), net income this month, what
  customers owe (AR aging total), items waiting for review, and income vs expenses for the
  last six months.
- Transactions: CSV upload with a preview that pre-fills the column mapping, the per-row error
  report, and the review queue. Each row shows its suggestion and source (rule, cache or LLM),
  and can be posted (optionally creating a rule), rejected, excluded, or matched to an existing
  ledger entry.
- Invoices: list with computed status, a draft form with line items, send, record payment,
  and the AR aging table.
- Reports: profit & loss, balance sheet (with the balanced check) and cash flow for chosen
  dates.

### Found while testing in the browser: matching needed a button
Clicking through the flow (invoice -> payment -> import the same deposit) showed that the
review queue only offered "Post", which would have booked the customer's payment as income a
second time. The matching API existed (Stage 8) but had no UI. Each review row now has
"Find match", which lists entries with the same amount on the same bank account within 7 days.

### Not built
A reconciliation screen, account management, rule management and journal entry screens.
All of those exist in the API (see /docs on the running server) but have no UI yet.

## Stage 10 - Quality and packaging

### Audit log: deliberately not built
A change log of who changed what and when is planned but left out on purpose (it will be
built live as a demo). The groundwork is in place: every write goes through a service
function, so logging can be added there without touching the routes, and ledger history is
already preserved because posted entries are immutable and corrections are reversals.

### Seed script uses the real services
`python -m app.scripts.seed` creates a demo studio with three months of activity: an
opening balance, categorization rules, three customers with paid, part-paid, overdue, current
and draft invoices, a bank statement imported through the CSV importer, deposits matched to
recorded payments, and a few transactions left in the review queue. It calls the same service
functions as the API, so the demo data obeys every rule, and a test runs it against the test
database and checks the books balance. Dates are relative to today so the dashboard always
has recent months. Running it again does nothing.

### Docker
- API image: Python slim, dependencies installed in their own layer, runs as a non-root user,
  applies migrations on start. With more than one replica, migrations should become a
  separate one-off task (e.g. an ECS task) instead of running on every container start.
- Web image: a Node build stage, then nginx serving the static files and proxying `/api` to
  the API container (same origin, so no CORS).
- `docker compose up --build` runs Postgres, the API and the web app (http://localhost:8080).
  The test database is behind a compose profile, so it only starts when asked for.
- The compose file has a local-only default for `JWT_SECRET_KEY` so the demo starts with one
  command; the app itself still refuses to start without a secret.

### CI (GitHub Actions)
Three jobs on every push and pull request: backend (ruff lint, ruff format check, pytest
against a Postgres service container), frontend (Vitest, type check and production build)
and docker (both images build). Tests never call a real LLM: the categorization tests use a
fake provider, and CI sets `LLM_PROVIDER=none` as a second guard.

## Stage 11 - README and docs

### API docs under /api
FastAPI's interactive docs moved from `/docs` to `/api/docs` (and the OpenAPI spec to
`/api/openapi.json`), so they are reachable through the same nginx proxy as the API in the
Docker setup instead of needing a second exposed port.

### Only claims that were checked
The README states only what the code and tests show: test counts come from the actual runs,
screenshots are of the seeded demo running in Docker, and there are no performance or
accuracy numbers because none were measured. Suggestion acceptance rates are tracked by the
app (`/suggestions/stats`) but depend on real usage, so none are quoted.
