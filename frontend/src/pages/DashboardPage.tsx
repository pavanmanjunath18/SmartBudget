import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { get } from "../api/client";
import { useAccounts } from "../api/hooks";
import type { AgingReport, BankTransaction, MonthTotals, ProfitAndLoss } from "../api/types";
import { useOrgId } from "../auth/AuthContext";
import { Money } from "../components/Money";
import { daysAgo, monthLabel, startOfMonth, today } from "../lib/dates";
import { formatCents } from "../lib/money";

function useCashPosition() {
  const orgId = useOrgId();
  const { data: accounts } = useAccounts();
  const banks = accounts?.filter((a) => a.subtype === "bank") ?? [];
  return useQuery({
    queryKey: ["cash", orgId, banks.map((b) => b.id)],
    enabled: banks.length > 0,
    queryFn: async () => {
      const balances = await Promise.all(
        banks.map((b) =>
          get<{ balance_cents: number }>(`/orgs/${orgId}/accounts/${b.id}/balance`),
        ),
      );
      return balances.reduce((sum, b) => sum + b.balance_cents, 0);
    },
  });
}

export function DashboardPage() {
  const orgId = useOrgId();
  const cash = useCashPosition();
  const months = useQuery({
    queryKey: ["monthly", orgId],
    queryFn: () =>
      get<MonthTotals[]>(
        `/orgs/${orgId}/reports/monthly?date_from=${startOfMonth(5)}&date_to=${today()}`,
      ),
  });
  // A rolling window rather than "this month", so the number moves whenever recent
  // transactions are posted (including on the 1st of the month).
  const last30 = useQuery({
    queryKey: ["profit-loss-30d", orgId],
    queryFn: () =>
      get<ProfitAndLoss>(
        `/orgs/${orgId}/reports/profit-loss?date_from=${daysAgo(29)}&date_to=${today()}`,
      ),
  });
  const aging = useQuery({
    queryKey: ["aging", orgId],
    queryFn: () => get<AgingReport>(`/orgs/${orgId}/reports/ar-aging`),
  });
  const toReview = useQuery({
    queryKey: ["bank-transactions", orgId, "for_review"],
    queryFn: () => get<BankTransaction[]>(`/orgs/${orgId}/bank-transactions?status=for_review`),
  });

  const chartData = (months.data ?? []).map((m) => ({
    month: monthLabel(m.month),
    Income: m.income_cents,
    Expenses: m.expense_cents,
  }));

  return (
    <>
      <h1>Dashboard</h1>
      <div className="grid">
        <div className="panel stat">
          <div className="label">Cash in bank</div>
          <div className="value">{cash.data !== undefined ? <Money cents={cash.data} /> : "-"}</div>
        </div>
        <div className="panel stat">
          <div className="label">Net income, last 30 days</div>
          <div className="value">
            <Money cents={last30.data?.net_income_cents ?? 0} />
          </div>
        </div>
        <div className="panel stat">
          <div className="label">Customers owe you</div>
          <div className="value">
            <Money cents={aging.data?.totals.total_cents ?? 0} />
          </div>
        </div>
        <div className="panel stat">
          <div className="label">Transactions to review</div>
          <div className="value">{toReview.data?.length ?? "-"}</div>
        </div>
      </div>
      <div className="panel">
        <h2>Income vs expenses, last 6 months</h2>
        {chartData.length === 0 ? (
          <p className="muted">No income or expenses posted yet.</p>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="month" />
              <YAxis tickFormatter={(c: number) => formatCents(c).replace(/\.\d\d$/, "")} width={90} />
              <Tooltip formatter={(value) => formatCents(Number(value))} />
              <Legend />
              <Bar dataKey="Income" fill="#1f6f5c" />
              <Bar dataKey="Expenses" fill="#d97706" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </>
  );
}
