"""Downloadable sample bank statements for the live demo.

Three small CSV files that each show something different when imported into the demo
account. They are generated (not stored) so their dates are always within the last month and
the dashboard's charts pick them up.

Every row here has a different description or amount from the rows in the demo seed
(app/services/demo_service.py), so importing a sample never collides with seeded data by
accident. The only deliberate duplicates are between the samples themselves (file 3 repeats
every row of file 1), and tests/test_demo.py checks all of this against the real importer.
"""

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta

from app.services.errors import NotFoundError


@dataclass(frozen=True)
class SampleDataset:
    key: str
    title: str
    description: str
    filename: str


SAMPLES = [
    SampleDataset(
        key="normal-month",
        title="1. A normal month",
        description=(
            "Standard Date / Description / Amount columns. Most rows match the demo's rules or "
            "earlier choices, so they're suggested automatically; a few new vendors stay for "
            "review."
        ),
        filename="smartbudget-sample-1-normal-month.csv",
    ),
    SampleDataset(
        key="messy-export",
        title="2. A messy bank export",
        description=(
            "Different column names, separate Withdrawals and Deposits columns, $ signs and "
            "commas, plus 5 bad rows. Shows column mapping and the per-row error report."
        ),
        filename="smartbudget-sample-2-messy-export.csv",
    ),
    SampleDataset(
        key="overlapping-export",
        title="3. Overlapping export",
        description=(
            "Repeats every row from file 1 and adds 6 new ones, including a big client payment "
            "and two identical same-day coffees. Import it after file 1: repeats are skipped, "
            "both coffees are kept."
        ),
        filename="smartbudget-sample-3-overlapping-export.csv",
    ),
]

# (days before today, description, amount as a bank shows it)
_Row = tuple[int, str, str]

# File 1. Comments say how the demo account's seed data will categorize each row.
_NORMAL_MONTH: list[_Row] = [
    (27, "ADOBE *CREATIVE CLOUD", "-62.99"),  # rule
    (26, "GITHUB INC", "-25.00"),  # rule
    (24, "COWORK SPACE RENT", "-875.00"),  # rule
    (23, "SQ *BLUE BOTTLE COFFEE #2210", "-7.25"),  # remembered vendor
    (21, "AWS EMEA", "-44.10"),  # rule
    (19, "COMCAST BUSINESS", "-92.49"),  # rule
    (17, "STAPLES 00512", "-83.76"),  # remembered vendor
    (15, "FIGMA", "-48.00"),  # rule
    (14, "MONTHLY SERVICE FEE", "-14.00"),  # rule
    (12, "UBER TRIP 9912877", "-31.20"),  # no match: stays in review
    (10, "NOTION LABS", "-12.00"),  # no match
    (8, "STRIPE PAYOUT", "1450.00"),  # no match (income)
    (6, "LINKEDIN ADS", "-175.00"),  # remembered vendor
    (3, "WEWORK DAY PASS", "-40.00"),  # no match
]

# File 3 = every row of file 1, plus these.
_EXTRA_ROWS: list[_Row] = [
    (29, "SQ *BLUE BOTTLE COFFEE #2210", "-5.50"),  # two identical coffees on the same day:
    (29, "SQ *BLUE BOTTLE COFFEE #2210", "-5.50"),  # both are real, both must be kept
    (20, "WIRE FROM LUMEN HEALTH", "4500.00"),  # big client payment, no match (income)
    (18, "DELTA AIR LINES", "-612.40"),  # remembered vendor
    (11, "REFUND AMAZON MKTP", "35.99"),  # no match (income)
    (2, "AWS EMEA", "-38.00"),  # rule
]


def _simple_csv(today: date, rows: list[_Row]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Date", "Description", "Amount"])
    for days_ago, description, amount in sorted(rows, key=lambda r: -r[0]):
        writer.writerow([(today - timedelta(days=days_ago)).isoformat(), description, amount])
    return out.getvalue()


def _money(cents: int, dollar_sign: bool) -> str:
    text = f"{cents // 100:,}.{cents % 100:02d}"
    return f"${text}" if dollar_sign else text


# File 2, oldest first. "ok" rows are valid; "bad" rows are broken on purpose.
# ("ok", days ago, payee, "out" | "in", cents, show a $ sign)
# ("bad", date text or days ago, payee, withdrawal text, deposit text)
_MESSY: list[tuple] = [
    ("ok", 26, "SQ *BLUE BOTTLE COFFEE #77", "out", 650, True),
    ("ok", 25, "GITHUB INC", "out", 2500, False),
    ("ok", 24, "DEPOSIT ACME CORP", "in", 275000, True),
    ("bad", "13/45/2026", "Mystery charge", "20.00", ""),  # impossible date
    ("ok", 22, "AWS EMEA", "out", 4410, False),
    ("ok", 20, "COMCAST BUSINESS", "out", 9249, True),
    ("bad", 15, "", "9.99", ""),  # empty description
    ("ok", 18, "ADOBE *CREATIVE CLOUD", "out", 6299, False),
    ("ok", 16, "DEPOSIT HARBOR COFFEE", "in", 120000, False),
    ("bad", 14, "DOUBLE ENTRY", "10.00", "10.00"),  # both a withdrawal and a deposit
    ("ok", 13, "UBER TRIP 7731920", "out", 1875, False),
    ("ok", 9, "STAPLES 00677", "out", 5420, True),
    ("bad", 8, "TOO PRECISE", "12.345", ""),  # three decimal places
    ("ok", 7, "LINKEDIN ADS", "out", 16000, False),
    ("ok", 4, "DEPOSIT LUMEN HEALTH", "in", 90000, False),
    ("bad", 3, "NO AMOUNT", "", ""),  # neither column filled in
    ("ok", 2, "COWORK SPACE RENT", "out", 87500, True),
]
_OPENING_BALANCE_CENTS = 845_000


def _messy_csv(today: date) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Posting Date", "Payee", "Withdrawals", "Deposits", "Balance"])
    balance = _OPENING_BALANCE_CENTS
    for row in _MESSY:
        if row[0] == "ok":
            _, days_ago, payee, kind, cents, dollar_sign = row
            day = (today - timedelta(days=days_ago)).strftime("%m/%d/%Y")
            balance += cents if kind == "in" else -cents
            money = _money(cents, dollar_sign)
            writer.writerow(
                [
                    day,
                    payee,
                    money if kind == "out" else "",
                    money if kind == "in" else "",
                    _money(balance, False),
                ]
            )
        else:
            _, when, payee, withdrawal, deposit = row
            day = (
                when
                if isinstance(when, str)
                else (today - timedelta(days=when)).strftime("%m/%d/%Y")
            )
            writer.writerow([day, payee, withdrawal, deposit, ""])
    # A leading byte-order mark, like files saved from Excel.
    return "﻿" + out.getvalue()


def get_sample(key: str) -> SampleDataset:
    for sample in SAMPLES:
        if sample.key == key:
            return sample
    raise NotFoundError("Sample dataset not found.")


def render(key: str, today: date) -> str:
    """The CSV text of one sample, with dates relative to `today`."""
    get_sample(key)
    if key == "normal-month":
        return _simple_csv(today, _NORMAL_MONTH)
    if key == "messy-export":
        return _messy_csv(today)
    return _simple_csv(today, _NORMAL_MONTH + _EXTRA_ROWS)


def row_count(key: str) -> int:
    """Number of data rows (not counting the header), good or bad."""
    text = render(key, date.today()).lstrip("﻿")
    return len(list(csv.reader(io.StringIO(text)))) - 1
