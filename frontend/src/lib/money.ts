/**
 * Money helpers. The API speaks integer cents; these convert to and from what people
 * read and type, using integer and string math only (never floating point).
 */

/** 123456 -> "$1,234.56", -450 -> "-$4.50". */
export function formatCents(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  const dollars = Math.trunc(abs / 100).toLocaleString("en-US");
  const rest = String(abs % 100).padStart(2, "0");
  return `${sign}$${dollars}.${rest}`;
}

/**
 * "1,234.5" -> 123450. Returns null for anything that isn't a valid amount with at most
 * two decimals, so a typo is reported instead of silently rounded.
 */
export function parseDollars(input: string): number | null {
  const text = input.trim().replace(/[$,\s]/g, "");
  const match = /^(-)?(\d+)(?:\.(\d{1,2}))?$/.exec(text);
  if (!match) return null;
  const [, minus, whole, fraction = ""] = match;
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(cents)) return null;
  return minus ? -cents : cents;
}

/** Cents as a plain editable string: 123450 -> "1234.50". */
export function centsToInput(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}${Math.trunc(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}
