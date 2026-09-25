import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { get, post } from "../api/client";
import { useAccounts } from "../api/hooks";
import type { AgingReport, Customer, Invoice } from "../api/types";
import { useOrgId } from "../auth/AuthContext";
import { Money } from "../components/Money";
import { isoDate, today } from "../lib/dates";
import { centsToInput, parseDollars } from "../lib/money";

interface DraftLine {
  description: string;
  quantity: string;
  rate: string; // dollars as typed
  accountId: number | "";
}

const emptyLine = (accountId: number | ""): DraftLine => ({
  description: "",
  quantity: "1",
  rate: "",
  accountId,
});

function NewInvoiceForm({ onDone }: { onDone: () => void }) {
  const orgId = useOrgId();
  const queryClient = useQueryClient();
  const { data: accounts = [] } = useAccounts();
  const incomeAccounts = accounts.filter((a) => a.type === "income" && a.is_active);
  const customers = useQuery({
    queryKey: ["customers", orgId],
    queryFn: () => get<Customer[]>(`/orgs/${orgId}/customers`),
  });
  const in30 = new Date();
  in30.setDate(in30.getDate() + 30);

  const [customerId, setCustomerId] = useState<number | "">("");
  const [newCustomer, setNewCustomer] = useState("");
  const [issueDate, setIssueDate] = useState(today());
  const [dueDate, setDueDate] = useState(isoDate(in30));
  const [lines, setLines] = useState<DraftLine[]>([emptyLine(incomeAccounts[0]?.id ?? "")]);
  const [error, setError] = useState<string | null>(null);

  const update = (i: number, patch: Partial<DraftLine>) =>
    setLines(lines.map((line, j) => (j === i ? { ...line, ...patch } : line)));

  const create = useMutation({
    mutationFn: async () => {
      let customer = customerId;
      if (customer === "") {
        if (!newCustomer.trim()) throw new Error("Pick or add a customer.");
        customer = (await post<Customer>(`/orgs/${orgId}/customers`, { name: newCustomer })).id;
      }
      const payloadLines = lines.map((line, i) => {
        const cents = parseDollars(line.rate);
        if (cents === null || cents < 0) throw new Error(`Line ${i + 1}: enter a valid rate.`);
        if (line.accountId === "") throw new Error(`Line ${i + 1}: pick an income account.`);
        return {
          description: line.description,
          quantity: line.quantity,
          unit_price_cents: cents,
          income_account_id: line.accountId,
        };
      });
      return post<Invoice>(`/orgs/${orgId}/invoices`, {
        customer_id: customer,
        issue_date: issueDate,
        due_date: dueDate,
        lines: payloadLines,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invoices", orgId] });
      queryClient.invalidateQueries({ queryKey: ["customers", orgId] });
      onDone();
    },
    onError: (err) => setError(err.message),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    create.mutate();
  };

  return (
    <form className="panel" onSubmit={submit}>
      <h2>New invoice (saved as draft)</h2>
      <div className="form-grid">
        <label>
          Customer
          <select
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value ? Number(e.target.value) : "")}
          >
            <option value="">New customer...</option>
            {(customers.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {customerId === "" && (
          <label>
            New customer name
            <input value={newCustomer} onChange={(e) => setNewCustomer(e.target.value)} />
          </label>
        )}
        <label>
          Issue date
          <input type="date" value={issueDate} onChange={(e) => setIssueDate(e.target.value)} />
        </label>
        <label>
          Due date
          <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
        </label>
      </div>
      <table>
        <thead>
          <tr>
            <th>Description</th>
            <th>Qty</th>
            <th>Rate ($)</th>
            <th>Income account</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {lines.map((line, i) => (
            <tr key={i}>
              <td>
                <input
                  value={line.description}
                  onChange={(e) => update(i, { description: e.target.value })}
                  required
                />
              </td>
              <td>
                <input
                  value={line.quantity}
                  onChange={(e) => update(i, { quantity: e.target.value })}
                  size={5}
                />
              </td>
              <td>
                <input
                  value={line.rate}
                  onChange={(e) => update(i, { rate: e.target.value })}
                  placeholder="0.00"
                  size={10}
                />
              </td>
              <td>
                <select
                  value={line.accountId}
                  onChange={(e) => update(i, { accountId: Number(e.target.value) })}
                >
                  {incomeAccounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.code} {a.name}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                {lines.length > 1 && (
                  <button
                    type="button"
                    className="link"
                    onClick={() => setLines(lines.filter((_, j) => j !== i))}
                  >
                    Remove
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row" style={{ marginTop: 12 }}>
        <button
          type="button"
          onClick={() => setLines([...lines, emptyLine(incomeAccounts[0]?.id ?? "")])}
        >
          Add line
        </button>
        <span className="spacer" />
        {error && <span className="error">{error}</span>}
        <button type="button" onClick={onDone}>
          Cancel
        </button>
        <button className="primary" disabled={create.isPending}>
          Save draft
        </button>
      </div>
    </form>
  );
}

function InvoiceActions({ invoice }: { invoice: Invoice }) {
  const orgId = useOrgId();
  const queryClient = useQueryClient();
  const [paying, setPaying] = useState(false);
  const [amount, setAmount] = useState(centsToInput(invoice.balance_due_cents));
  const [date, setDate] = useState(today());
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["invoices", orgId] });
    queryClient.invalidateQueries({ queryKey: ["aging", orgId] });
  };
  const send = useMutation({
    mutationFn: () => post(`/orgs/${orgId}/invoices/${invoice.id}/send`),
    onSuccess: refresh,
    onError: (err) => setError(err.message),
  });
  const pay = useMutation({
    mutationFn: () => {
      const cents = parseDollars(amount);
      if (cents === null || cents <= 0) throw new Error("Enter a valid amount.");
      return post(`/orgs/${orgId}/invoices/${invoice.id}/payments`, {
        amount_cents: cents,
        payment_date: date,
      });
    },
    onSuccess: () => {
      setPaying(false);
      refresh();
    },
    onError: (err) => setError(err.message),
  });

  if (invoice.status === "draft") {
    return (
      <>
        <button disabled={send.isPending} onClick={() => send.mutate()}>
          Send
        </button>
        {error && <div className="error">{error}</div>}
      </>
    );
  }
  if (invoice.status === "paid") return null;
  if (!paying) return <button onClick={() => setPaying(true)}>Record payment</button>;
  return (
    <div className="row">
      <input value={amount} onChange={(e) => setAmount(e.target.value)} size={10} />
      <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      <button className="primary" disabled={pay.isPending} onClick={() => pay.mutate()}>
        Save
      </button>
      <button className="link" onClick={() => setPaying(false)}>
        Cancel
      </button>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

export function InvoicesPage() {
  const orgId = useOrgId();
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState("");
  const invoices = useQuery({
    queryKey: ["invoices", orgId, filter],
    queryFn: () => get<Invoice[]>(`/orgs/${orgId}/invoices${filter ? `?status=${filter}` : ""}`),
  });
  const aging = useQuery({
    queryKey: ["aging", orgId],
    queryFn: () => get<AgingReport>(`/orgs/${orgId}/reports/ar-aging`),
  });

  return (
    <>
      <div className="row">
        <h1>Invoices</h1>
        <span className="spacer" />
        <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Status">
          <option value="">All</option>
          <option value="draft">Draft</option>
          <option value="sent">Sent</option>
          <option value="overdue">Overdue</option>
          <option value="paid">Paid</option>
        </select>
        {!creating && (
          <button className="primary" onClick={() => setCreating(true)}>
            New invoice
          </button>
        )}
      </div>
      {creating && <NewInvoiceForm onDone={() => setCreating(false)} />}
      <div className="panel">
        {invoices.data?.length === 0 ? (
          <p className="muted">No invoices yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Customer</th>
                <th>Issued</th>
                <th>Due</th>
                <th>Status</th>
                <th className="num">Total</th>
                <th className="num">Balance due</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(invoices.data ?? []).map((inv) => (
                <tr key={inv.id}>
                  <td>{inv.number}</td>
                  <td>{inv.customer_name}</td>
                  <td>{inv.issue_date}</td>
                  <td>{inv.due_date}</td>
                  <td>
                    <span className={`badge ${inv.status}`}>{inv.status}</span>
                  </td>
                  <td className="num">
                    <Money cents={inv.total_cents} />
                  </td>
                  <td className="num">
                    <Money cents={inv.balance_due_cents} />
                  </td>
                  <td>
                    <InvoiceActions invoice={inv} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="panel">
        <h2>Accounts receivable aging (as of today)</h2>
        {aging.data && aging.data.rows.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Customer</th>
                <th className="num">Current</th>
                <th className="num">1-30</th>
                <th className="num">31-60</th>
                <th className="num">61-90</th>
                <th className="num">Over 90</th>
                <th className="num">Total</th>
              </tr>
            </thead>
            <tbody>
              {[...aging.data.rows, aging.data.totals].map((row, i, all) => (
                <tr key={row.customer_name} className={i === all.length - 1 ? "total" : ""}>
                  <td>{row.customer_name}</td>
                  {[row.current, row.days_1_30, row.days_31_60, row.days_61_90, row.over_90, row.total_cents].map(
                    (cents, j) => (
                      <td key={j} className="num">
                        <Money cents={cents} />
                      </td>
                    ),
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No money owed right now.</p>
        )}
      </div>
    </>
  );
}
