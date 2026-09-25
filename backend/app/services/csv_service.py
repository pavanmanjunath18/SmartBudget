"""Parse bank CSV files into clean rows. Pure functions: no database access.

Banks export CSVs in different shapes, so the caller passes a ColumnMapping that says
which column holds the date, description and amount (or separate debit/credit columns).
Every row is validated on its own; a bad row is reported, not fatal.
"""

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10_000

# Tried in order when the mapping has no date_format. Day-first formats (31/01/2026) are
# not guessed, because "03/04/2026" is ambiguous; the user can pass "%d/%m/%Y" explicitly.
DEFAULT_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y"]

_HEADER_GUESSES = {
    "date": ["date", "posted date", "posting date", "transaction date", "trans date"],
    "description": ["description", "payee", "memo", "details", "name", "transaction"],
    "amount": ["amount", "transaction amount"],
    "debit": ["debit", "withdrawal", "withdrawals", "money out"],
    "credit": ["credit", "deposit", "deposits", "money in"],
}


class CsvFormatError(ValueError):
    """The file as a whole can't be read (encoding, missing columns, too big)."""


@dataclass(frozen=True)
class ColumnMapping:
    """Which CSV header holds each field. Use `amount`, or `debit` + `credit`."""

    date: str
    description: str
    amount: str | None = None
    debit: str | None = None  # money out, shown as a positive number
    credit: str | None = None  # money in
    date_format: str | None = None

    def validate(self, headers: list[str]) -> None:
        if self.amount is None and (self.debit is None or self.credit is None):
            raise CsvFormatError("Map either an amount column or both debit and credit columns.")
        wanted = [self.date, self.description, self.amount, self.debit, self.credit]
        missing = [h for h in wanted if h is not None and h not in headers]
        if missing:
            raise CsvFormatError(f"Columns not found in file: {missing}.")


@dataclass
class ParsedRow:
    row_number: int  # as the user sees it in a spreadsheet (header is row 1)
    posted_date: date | None = None
    amount_cents: int | None = None
    description: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def decode(content: bytes) -> str:
    """Decode an upload. Handles Excel's UTF-8 BOM; falls back to Latin-1."""
    if len(content) > MAX_FILE_BYTES:
        raise CsvFormatError("File is larger than 5 MB.")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def read_rows(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """Return (headers, rows) from CSV text."""
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    if not headers:
        raise CsvFormatError("The file is empty or has no header row.")
    reader.fieldnames = headers
    rows = [row for row in reader if any((v or "").strip() for v in row.values())]
    if len(rows) > MAX_ROWS:
        raise CsvFormatError(f"The file has more than {MAX_ROWS} rows.")
    return headers, rows


def guess_mapping(headers: list[str]) -> dict[str, str]:
    """Suggest a mapping from common header names, for the preview screen."""
    lowered = {h.lower(): h for h in headers}
    guess = {}
    for field_name, candidates in _HEADER_GUESSES.items():
        for candidate in candidates:
            if candidate in lowered:
                guess[field_name] = lowered[candidate]
                break
    return guess


def parse_amount(raw: str) -> int:
    """Turn "$1,234.50", "-12.00", "(12.00)" or "12.00-" into integer cents.

    Uses Decimal (never float) and refuses more than two decimal places instead of
    rounding, so no cent is ever invented or lost.
    """
    text = raw.strip().replace("$", "").replace(",", "").replace(" ", "")
    negative = False
    if text.startswith("(") and text.endswith(")"):
        text, negative = text[1:-1], True
    elif text.endswith("-"):
        text, negative = text[:-1], True
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError(f'Invalid amount "{raw}"') from None
    if not value.is_finite():
        raise ValueError(f'Invalid amount "{raw}"')
    if value.as_tuple().exponent < -2:
        raise ValueError(f'Amount "{raw}" has more than 2 decimal places')
    cents = int(value * 100)
    return -cents if negative else cents


def parse_date(raw: str, date_format: str | None) -> date:
    formats = [date_format] if date_format else DEFAULT_DATE_FORMATS
    for fmt in formats:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f'Invalid date "{raw}"')


def _amount_for(row: dict[str, str], mapping: ColumnMapping) -> int:
    if mapping.amount is not None:
        return parse_amount(row.get(mapping.amount) or "")
    debit = (row.get(mapping.debit or "") or "").strip()
    credit = (row.get(mapping.credit or "") or "").strip()
    if debit and credit:
        raise ValueError("Row has both a debit and a credit amount")
    if debit:
        return -abs(parse_amount(debit))
    if credit:
        return abs(parse_amount(credit))
    raise ValueError("Row has no amount")


def parse_rows(rows: list[dict[str, str]], mapping: ColumnMapping) -> list[ParsedRow]:
    """Validate and convert each row. Invalid rows keep their error messages."""
    parsed = []
    for index, row in enumerate(rows, start=2):
        result = ParsedRow(row_number=index)
        try:
            result.posted_date = parse_date(row.get(mapping.date) or "", mapping.date_format)
        except ValueError as exc:
            result.errors.append(str(exc))
        try:
            result.amount_cents = _amount_for(row, mapping)
            if result.amount_cents == 0:
                result.errors.append("Amount is zero")
        except ValueError as exc:
            result.errors.append(str(exc))
        result.description = " ".join((row.get(mapping.description) or "").split())[:500]
        if not result.description:
            result.errors.append("Description is empty")
        parsed.append(result)
    return parsed


def normalize_description(text: str) -> str:
    """Light normalization for the fingerprint: case and whitespace only.

    Deliberately NOT the aggressive vendor cleanup below: "CHECK #1041" and
    "CHECK #1042" must stay different here.
    """
    return " ".join(text.lower().split())


def fingerprints(account_id: int, rows: list[ParsedRow]) -> list[str]:
    """A stable id for each valid row, used to skip rows that were already imported.

    Built from account + date + amount + description + occurrence number. The
    occurrence number is how many identical rows came before it in the same file, so
    two real $4.50 coffees on the same day get different fingerprints, while
    re-importing the same file produces exactly the same fingerprints again.
    """
    seen: Counter[tuple[date, int, str]] = Counter()
    result = []
    for row in rows:
        key = (row.posted_date, row.amount_cents, normalize_description(row.description))
        occurrence = seen[key]
        seen[key] += 1
        raw = f"{account_id}|{key[0].isoformat()}|{key[1]}|{key[2]}|{occurrence}"
        result.append(hashlib.sha256(raw.encode()).hexdigest())
    return result


# Card processors and banks put these in front of the real merchant name.
_VENDOR_PREFIXES = re.compile(
    r"^(sq \*|sq\*|tst\* ?|paypal \*|pp\*|sp \* ?|pos |debit card purchase |"
    r"purchase authorized on \d{2}/\d{2} |recurring payment |ach (debit|credit) )+"
)


def normalize_vendor(description: str) -> str:
    """Best-effort merchant name for grouping and rules: "SQ *BLUE BOTTLE #123" -> "blue bottle".

    Used for categorization rules and the vendor cache (Stage 5), never for dedupe.
    """
    text = description.lower().strip()
    text = _VENDOR_PREFIXES.sub("", text)
    text = re.sub(r"#\s*\d+", " ", text)  # store numbers: "#1234"
    text = re.sub(r"\b[a-z]*\d{3,}[a-z\d]*\b", " ", text)  # ids and long numbers
    text = re.sub(r"[*]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -.,")
    return text or description.lower().strip()
