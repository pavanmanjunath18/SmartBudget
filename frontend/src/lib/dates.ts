/** Dates as the API expects them: "YYYY-MM-DD" in local time. */
export function isoDate(d: Date): string {
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${month}-${day}`;
}

export const today = (): string => isoDate(new Date());

export function startOfMonth(monthsAgo = 0): string {
  const now = new Date();
  return isoDate(new Date(now.getFullYear(), now.getMonth() - monthsAgo, 1));
}

/** "2026-03-01" -> "Mar 2026" */
export function monthLabel(iso: string): string {
  const [year, month] = iso.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleString("en-US", { month: "short", year: "numeric" });
}
