"""Unit tests for CSV parsing, fingerprints and vendor normalization (no database)."""

from datetime import date

import pytest

from app.services import csv_service
from app.services.csv_service import ColumnMapping, CsvFormatError, ParsedRow

MAPPING = ColumnMapping(date="Date", description="Description", amount="Amount")


@pytest.mark.parametrize(
    ("raw", "cents"),
    [
        ("12.34", 1234),
        ("-12.34", -1234),
        ("$1,234.50", 123450),
        ("(45.00)", -4500),
        ("45.00-", -4500),
        ("7", 700),
        ("0.1", 10),
        # The classic float bug: 0.1 + 0.2 != 0.3. Decimal parsing never hits it.
        ("0.30", 30),
    ],
)
def test_parse_amount(raw: str, cents: int) -> None:
    assert csv_service.parse_amount(raw) == cents


@pytest.mark.parametrize("raw", ["abc", "", "12.345", "1e5x", "NaN", "Infinity"])
def test_parse_amount_rejects_bad_values(raw: str) -> None:
    with pytest.raises(ValueError):
        csv_service.parse_amount(raw)


@pytest.mark.parametrize(
    ("raw", "fmt", "expected"),
    [
        ("2026-01-31", None, date(2026, 1, 31)),
        ("01/31/2026", None, date(2026, 1, 31)),
        ("31/01/2026", "%d/%m/%Y", date(2026, 1, 31)),
    ],
)
def test_parse_date(raw: str, fmt: str | None, expected: date) -> None:
    assert csv_service.parse_date(raw, fmt) == expected


def test_day_first_dates_are_not_guessed() -> None:
    with pytest.raises(ValueError):
        csv_service.parse_date("31/01/2026", None)


def test_parse_rows_reports_each_bad_row() -> None:
    rows = [
        {"Date": "2026-01-05", "Description": "Coffee", "Amount": "-4.50"},
        {"Date": "not a date", "Description": "", "Amount": "abc"},
        {"Date": "2026-01-06", "Description": "Refund", "Amount": "0.00"},
    ]

    parsed = csv_service.parse_rows(rows, MAPPING)

    assert parsed[0].is_valid and parsed[0].amount_cents == -450
    assert parsed[1].row_number == 3
    assert len(parsed[1].errors) == 3
    assert parsed[2].errors == ["Amount is zero"]


def test_debit_and_credit_columns() -> None:
    mapping = ColumnMapping(date="Date", description="Desc", debit="Withdrawal", credit="Deposit")
    rows = [
        {"Date": "2026-01-05", "Desc": "Rent", "Withdrawal": "1200.00", "Deposit": ""},
        {"Date": "2026-01-06", "Desc": "Client", "Withdrawal": "", "Deposit": "3000.00"},
        {"Date": "2026-01-07", "Desc": "Both", "Withdrawal": "1.00", "Deposit": "1.00"},
    ]

    parsed = csv_service.parse_rows(rows, mapping)

    assert [p.amount_cents for p in parsed[:2]] == [-120000, 300000]
    assert not parsed[2].is_valid


def test_mapping_must_name_existing_columns() -> None:
    with pytest.raises(CsvFormatError, match="not found"):
        MAPPING.validate(["Date", "Description", "Value"])
    with pytest.raises(CsvFormatError, match="amount"):
        ColumnMapping(date="Date", description="Description").validate(["Date", "Description"])


def test_decode_handles_excel_bom_and_latin1() -> None:
    assert csv_service.decode("\ufeffDate,Amount".encode()).startswith("Date")
    assert csv_service.decode("Caf\xe9".encode("latin-1")) == "Café"


def test_guess_mapping_from_common_headers() -> None:
    headers = ["Posting Date", "Payee", "Withdrawals", "Deposits", "Balance"]

    guess = csv_service.guess_mapping(headers)

    assert guess == {
        "date": "Posting Date",
        "description": "Payee",
        "debit": "Withdrawals",
        "credit": "Deposits",
    }


def _row(day: int, cents: int, desc: str) -> ParsedRow:
    return ParsedRow(
        row_number=0, posted_date=date(2026, 1, day), amount_cents=cents, description=desc
    )


def test_fingerprint_is_stable_and_ignores_case_and_spacing() -> None:
    a = csv_service.fingerprints(1, [_row(5, -450, "Blue  Bottle")])
    b = csv_service.fingerprints(1, [_row(5, -450, "BLUE BOTTLE")])

    assert a == b


def test_identical_rows_in_one_file_get_different_fingerprints() -> None:
    """Two real $4.50 coffees on the same day must both be kept."""
    rows = [_row(5, -450, "Blue Bottle"), _row(5, -450, "Blue Bottle")]

    first, second = csv_service.fingerprints(1, rows)

    assert first != second


def test_fingerprint_depends_on_account_date_amount_and_description() -> None:
    base = csv_service.fingerprints(1, [_row(5, -450, "Blue Bottle")])[0]

    assert csv_service.fingerprints(2, [_row(5, -450, "Blue Bottle")])[0] != base
    assert csv_service.fingerprints(1, [_row(6, -450, "Blue Bottle")])[0] != base
    assert csv_service.fingerprints(1, [_row(5, -451, "Blue Bottle")])[0] != base
    assert csv_service.fingerprints(1, [_row(5, -450, "Blue Bottles")])[0] != base


def test_check_numbers_stay_distinct_in_fingerprint() -> None:
    first = csv_service.fingerprints(1, [_row(5, -10000, "CHECK #1041")])
    second = csv_service.fingerprints(1, [_row(5, -10000, "CHECK #1042")])

    assert first != second


@pytest.mark.parametrize(
    ("description", "vendor"),
    [
        ("SQ *BLUE BOTTLE COFFEE #1234", "blue bottle coffee"),
        ("TST* SWEETGREEN 00123", "sweetgreen"),
        ("PAYPAL *ADOBE", "adobe"),
        ("POS AMAZON WEB SERVICES", "amazon web services"),
        ("GITHUB INC", "github inc"),
        ("PURCHASE AUTHORIZED ON 01/05 UBER TRIP 8837461", "uber trip"),
        ("  Zoom.us  ", "zoom.us"),
    ],
)
def test_normalize_vendor(description: str, vendor: str) -> None:
    assert csv_service.normalize_vendor(description) == vendor
