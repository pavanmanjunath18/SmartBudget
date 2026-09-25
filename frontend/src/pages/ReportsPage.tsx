import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { get } from "../api/client";
import type { BalanceSheet, CashFlow, ProfitAndLoss, Section } from "../api/types";
import { useOrgId } from "../auth/AuthContext";
import { Money } from "../components/Money";
import { startOfMonth, today } from "../lib/dates";

type Tab = "pl" | "bs" | "cf";

function SectionRows({ title, section, totalLabel }: { title: string; section: Section; totalLabel: string }) {
  return (
    <>
      <tr>
        <td colSpan={2}>
          <strong>{title}</strong>
        </td>
      </tr>
      {section.lines.map((line) => (
        <tr key={line.account_id}>
          <td>
            {line.code} {line.name}
          </td>
          <td className="num">
            <Money cents={line.amount_cents} />
          </td>
        </tr>
      ))}
      {section.lines.length === 0 && (
        <tr>
          <td className="muted">None</td>
          <td />
        </tr>
      )}
      <tr className="total">
        <td>{totalLabel}</td>
        <td className="num">
          <Money cents={section.total_cents} />
        </td>
      </tr>
    </>
  );
}

function TotalRow({ label, cents }: { label: string; cents: number }) {
  return (
    <tr className="total">
      <td>{label}</td>
      <td className="num">
        <Money cents={cents} />
      </td>
    </tr>
  );
}

export function ReportsPage() {
  const orgId = useOrgId();
  const [tab, setTab] = useState<Tab>("pl");
  const [from, setFrom] = useState(startOfMonth(2));
  const [to, setTo] = useState(today());
  const base = `/orgs/${orgId}/reports`;

  const pl = useQuery({
    queryKey: ["report", orgId, "pl", from, to],
    enabled: tab === "pl",
    queryFn: () => get<ProfitAndLoss>(`${base}/profit-loss?date_from=${from}&date_to=${to}`),
  });
  const bs = useQuery({
    queryKey: ["report", orgId, "bs", to],
    enabled: tab === "bs",
    queryFn: () => get<BalanceSheet>(`${base}/balance-sheet?as_of=${to}`),
  });
  const cf = useQuery({
    queryKey: ["report", orgId, "cf", from, to],
    enabled: tab === "cf",
    queryFn: () => get<CashFlow>(`${base}/cash-flow?date_from=${from}&date_to=${to}`),
  });
  const error = (tab === "pl" ? pl : tab === "bs" ? bs : cf).error;

  return (
    <>
      <h1>Reports</h1>
      <div className="tabs">
        {(
          [
            ["pl", "Profit & loss"],
            ["bs", "Balance sheet"],
            ["cf", "Cash flow"],
          ] as const
        ).map(([key, label]) => (
          <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>
      <div className="panel">
        <div className="row" style={{ marginBottom: 12 }}>
          {tab !== "bs" && (
            <label>
              From
              <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
            </label>
          )}
          <label>
            {tab === "bs" ? "As of" : "To"}
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
        </div>
        {error && <p className="error">{error.message}</p>}
        <table>
          <tbody>
            {tab === "pl" && pl.data && (
              <>
                <SectionRows title="Income" section={pl.data.income} totalLabel="Total income" />
                <SectionRows title="Expenses" section={pl.data.expenses} totalLabel="Total expenses" />
                <TotalRow label="Net income" cents={pl.data.net_income_cents} />
              </>
            )}
            {tab === "bs" && bs.data && (
              <>
                <SectionRows title="Assets" section={bs.data.assets} totalLabel="Total assets" />
                <SectionRows title="Liabilities" section={bs.data.liabilities} totalLabel="Total liabilities" />
                <SectionRows title="Equity" section={bs.data.equity} totalLabel="Owner's equity" />
                <tr>
                  <td>Current earnings</td>
                  <td className="num">
                    <Money cents={bs.data.current_earnings_cents} />
                  </td>
                </tr>
                <TotalRow label="Total liabilities and equity" cents={bs.data.total_liabilities_and_equity_cents} />
                <tr>
                  <td className={bs.data.is_balanced ? "muted" : "error"}>
                    {bs.data.is_balanced ? "Balanced: assets = liabilities + equity" : "Not balanced"}
                  </td>
                  <td />
                </tr>
              </>
            )}
            {tab === "cf" && cf.data && (
              <>
                <TotalRow label="Opening cash" cents={cf.data.opening_cash_cents} />
                <SectionRows title="Operating" section={cf.data.operating} totalLabel="Net operating" />
                <SectionRows title="Investing" section={cf.data.investing} totalLabel="Net investing" />
                <SectionRows title="Financing" section={cf.data.financing} totalLabel="Net financing" />
                <TotalRow label="Net change in cash" cents={cf.data.net_change_cents} />
                <TotalRow label="Closing cash" cents={cf.data.closing_cash_cents} />
              </>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
