// Mirrors the backend's Pydantic response schemas. Amounts are integer cents.

export type AccountType = "asset" | "liability" | "equity" | "income" | "expense";

export interface Organization {
  id: number;
  name: string;
  role: string;
}

export interface Me {
  user: { id: number; email: string };
  organizations: Organization[];
}

export interface Account {
  id: number;
  code: string;
  name: string;
  type: AccountType;
  subtype: string | null;
  is_active: boolean;
}

export interface BankTransaction {
  id: number;
  account_id: number;
  posted_date: string;
  amount_cents: number;
  description: string;
  normalized_vendor: string;
  status: "for_review" | "posted" | "matched" | "excluded";
  journal_entry_id: number | null;
}

export interface ImportPreview {
  headers: string[];
  sample_rows: Record<string, string>[];
  suggested_mapping: Partial<Record<"date" | "description" | "amount" | "debit" | "credit", string>>;
}

export interface ImportResult {
  id: number;
  filename: string;
  total_rows: number;
  imported_count: number;
  duplicate_count: number;
  invalid_count: number;
  errors: { row: number; errors: string[] }[];
}

export interface Suggestion {
  id: number;
  bank_transaction_id: number;
  account_id: number;
  source: "rule" | "cache" | "llm";
  status: string;
}

export interface SourceStats {
  source: string;
  accepted: number;
  rejected: number;
  pending: number;
  acceptance_rate: number | null;
}

export interface Customer {
  id: number;
  name: string;
  email: string | null;
}

export interface Invoice {
  id: number;
  number: number;
  customer_id: number;
  customer_name: string;
  issue_date: string;
  due_date: string;
  status: "draft" | "sent" | "paid" | "overdue";
  total_cents: number;
  paid_cents: number;
  balance_due_cents: number;
}

export interface AgingRow {
  customer_name: string;
  current: number;
  days_1_30: number;
  days_31_60: number;
  days_61_90: number;
  over_90: number;
  total_cents: number;
}

export interface AgingReport {
  as_of: string;
  rows: AgingRow[];
  totals: AgingRow;
}

export interface ReportLine {
  account_id: number;
  code: string;
  name: string;
  amount_cents: number;
}

export interface Section {
  lines: ReportLine[];
  total_cents: number;
}

export interface ProfitAndLoss {
  income: Section;
  expenses: Section;
  net_income_cents: number;
}

export interface BalanceSheet {
  assets: Section;
  liabilities: Section;
  equity: Section;
  current_earnings_cents: number;
  total_equity_cents: number;
  total_liabilities_and_equity_cents: number;
  is_balanced: boolean;
}

export interface CashFlow {
  opening_cash_cents: number;
  operating: Section;
  investing: Section;
  financing: Section;
  net_change_cents: number;
  closing_cash_cents: number;
}

export interface MonthTotals {
  month: string;
  income_cents: number;
  expense_cents: number;
  net_cents: number;
}

export interface MatchCandidate {
  journal_entry_id: number;
  entry_date: string;
  memo: string;
  source: string;
  amount_cents: number;
}
