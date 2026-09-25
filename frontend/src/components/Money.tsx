import { formatCents } from "../lib/money";

export function Money({ cents, className = "" }: { cents: number; className?: string }) {
  return <span className={`money ${cents < 0 ? "negative" : ""} ${className}`}>{formatCents(cents)}</span>;
}
