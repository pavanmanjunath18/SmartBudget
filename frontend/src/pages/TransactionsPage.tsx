import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { get, post } from "../api/client";
import { accountLabel, useAccounts } from "../api/hooks";
import type {
  Account,
  BankTransaction,
  ImportPreview,
  ImportResult,
  MatchCandidate,
  SourceStats,
  Suggestion,
} from "../api/types";
import { useOrgId } from "../auth/AuthContext";
import { Money } from "../components/Money";

const MAPPING_FIELDS = ["date", "description", "amount", "debit", "credit"] as const;
type MappingField = (typeof MAPPING_FIELDS)[number];

function ImportPanel({ bankAccounts }: { bankAccounts: Account[] }) {
  const orgId = useOrgId();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Partial<Record<MappingField, string>>>({});
  const [accountId, setAccountId] = useState<number | null>(bankAccounts[0]?.id ?? null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const choose = async (chosen: File | null) => {
    setFile(chosen);
    setPreview(null);
    setResult(null);
    setError(null);
    if (!chosen) return;
    const form = new FormData();
    form.append("file", chosen);
    try {
      const data = await post<ImportPreview>(`/orgs/${orgId}/imports/preview`, form);
      setPreview(data);
      setMapping(data.suggested_mapping);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file!);
      form.append("account_id", String(accountId));
      const cleaned = Object.fromEntries(Object.entries(mapping).filter(([, v]) => v));
      form.append("mapping", JSON.stringify(cleaned));
      return post<ImportResult>(`/orgs/${orgId}/imports`, form);
    },
    onSuccess: (data) => {
      setResult(data);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["bank-transactions", orgId] });
    },
    onError: (err) => setError(err.message),
  });

  return (
    <div className="panel">
      <h2>Import a bank statement (CSV)</h2>
      <div className="row">
        <input type="file" accept=".csv,text/csv" onChange={(e) => choose(e.target.files?.[0] ?? null)} />
        <select value={accountId ?? ""} onChange={(e) => setAccountId(Number(e.target.value))}>
          {bankAccounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.code} {a.name}
            </option>
          ))}
        </select>
      </div>
      {preview && (
        <>
          <p className="muted">Match your file's columns. Use Amount, or both Debit and Credit.</p>
          <div className="form-grid">
            {MAPPING_FIELDS.map((field) => (
              <label key={field}>
                {field}
                <select
                  value={mapping[field] ?? ""}
                  onChange={(e) => setMapping({ ...mapping, [field]: e.target.value })}
                >
                  <option value="">(none)</option>
                  {preview.headers.map((h) => (
                    <option key={h}>{h}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <p className="muted">{preview.sample_rows.length} sample rows loaded.</p>
          <button className="primary" disabled={upload.isPending} onClick={() => upload.mutate()}>
            Import
          </button>
        </>
      )}
      {error && <p className="error">{error}</p>}
      {result && (
        <div>
          <p>
            <strong>{result.imported_count}</strong> imported, {result.duplicate_count} already
            imported (skipped), {result.invalid_count} invalid, out of {result.total_rows} rows.
          </p>
          {result.errors.length > 0 && (
            <ul className="error">
              {result.errors.map((e) => (
                <li key={e.row}>
                  Row {e.row}: {e.errors.join("; ")}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function ReviewRow({
  tx,
  suggestion,
  categories,
  accounts,
}: {
  tx: BankTransaction;
  suggestion: Suggestion | undefined;
  categories: Account[];
  accounts: Account[];
}) {
  const orgId = useOrgId();
  const queryClient = useQueryClient();
  const [accountId, setAccountId] = useState<number | "">(suggestion?.account_id ?? "");
  const [createRule, setCreateRule] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<MatchCandidate[] | null>(null);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["bank-transactions", orgId] });
    queryClient.invalidateQueries({ queryKey: ["suggestions", orgId] });
  };
  const action = useMutation({
    mutationFn: (kind: "post" | "reject" | "exclude") => {
      if (kind === "reject") return post(`/orgs/${orgId}/suggestions/${suggestion!.id}/reject`);
      if (kind === "exclude") return post(`/orgs/${orgId}/bank-transactions/${tx.id}/exclude`);
      // Accepting the suggestion as-is records it as accepted; any other choice is a manual post.
      if (suggestion && accountId === suggestion.account_id) {
        return post(`/orgs/${orgId}/suggestions/${suggestion.id}/accept`, {
          create_rule: createRule,
        });
      }
      return post(`/orgs/${orgId}/bank-transactions/${tx.id}/categorize`, {
        account_id: accountId,
        create_rule: createRule,
      });
    },
    onSuccess: refresh,
    onError: (err) => setError(err.message),
  });
  // Money already in the ledger (e.g. an invoice payment) is matched, not posted again.
  const findMatches = async () => {
    setError(null);
    try {
      setCandidates(
        await get<MatchCandidate[]>(`/orgs/${orgId}/bank-transactions/${tx.id}/match-candidates`),
      );
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const match = useMutation({
    mutationFn: (entryId: number) =>
      post(`/orgs/${orgId}/bank-transactions/${tx.id}/match`, { journal_entry_id: entryId }),
    onSuccess: refresh,
    onError: (err) => setError(err.message),
  });

  return (
    <tr>
      <td className="nowrap">{tx.posted_date}</td>
      <td>
        {tx.description}
        <div className="muted">{tx.normalized_vendor}</div>
      </td>
      <td className="num">
        <Money cents={tx.amount_cents} />
      </td>
      <td>
        {suggestion ? (
          <>
            <span className={`badge ${suggestion.source}`}>{suggestion.source}</span>{" "}
            {accountLabel(accounts, suggestion.account_id)}
          </>
        ) : (
          <span className="muted">-</span>
        )}
      </td>
      <td>
        <div className="row">
          <select value={accountId} onChange={(e) => setAccountId(Number(e.target.value))}>
            <option value="">Choose account</option>
            {categories.map((a) => (
              <option key={a.id} value={a.id}>
                {a.code} {a.name}
              </option>
            ))}
          </select>
          <label className="row" style={{ flexDirection: "row" }}>
            <input
              type="checkbox"
              checked={createRule}
              onChange={(e) => setCreateRule(e.target.checked)}
            />
            rule
          </label>
          <button
            className="primary"
            disabled={accountId === "" || action.isPending}
            onClick={() => action.mutate("post")}
          >
            Post
          </button>
          {suggestion && (
            <button disabled={action.isPending} onClick={() => action.mutate("reject")}>
              Reject
            </button>
          )}
          <button className="link" onClick={findMatches}>
            Find match
          </button>
          <button className="link" disabled={action.isPending} onClick={() => action.mutate("exclude")}>
            Exclude
          </button>
        </div>
        {candidates !== null &&
          (candidates.length === 0 ? (
            <div className="muted">No ledger entry with this amount within 7 days.</div>
          ) : (
            candidates.map((c) => (
              <div key={c.journal_entry_id} className="row">
                <span className="muted">
                  {c.entry_date} · {c.memo || c.source}
                </span>
                <button disabled={match.isPending} onClick={() => match.mutate(c.journal_entry_id)}>
                  Match
                </button>
              </div>
            ))
          ))}
        {error && <div className="error">{error}</div>}
      </td>
    </tr>
  );
}

export function TransactionsPage() {
  const orgId = useOrgId();
  const queryClient = useQueryClient();
  const { data: accounts = [] } = useAccounts();
  const transactions = useQuery({
    queryKey: ["bank-transactions", orgId, "for_review"],
    queryFn: () => get<BankTransaction[]>(`/orgs/${orgId}/bank-transactions?status=for_review`),
  });
  const suggestions = useQuery({
    queryKey: ["suggestions", orgId],
    queryFn: () => get<Suggestion[]>(`/orgs/${orgId}/suggestions?status=pending`),
  });
  const stats = useQuery({
    queryKey: ["suggestions", orgId, "stats"],
    queryFn: () => get<SourceStats[]>(`/orgs/${orgId}/suggestions/stats`),
  });
  const suggest = useMutation({
    mutationFn: () => post<Suggestion[]>(`/orgs/${orgId}/bank-transactions/suggest`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["suggestions", orgId] }),
  });

  const bankAccounts = accounts.filter((a) => a.subtype === "bank" && a.is_active);
  const categories = accounts.filter((a) => a.subtype !== "bank" && a.is_active);
  const byTx = new Map((suggestions.data ?? []).map((s) => [s.bank_transaction_id, s]));

  return (
    <>
      <h1>Transactions</h1>
      {bankAccounts.length > 0 && <ImportPanel bankAccounts={bankAccounts} />}
      <div className="panel">
        <div className="row">
          <h2>For review ({transactions.data?.length ?? 0})</h2>
          <span className="spacer" />
          <span className="muted">
            {(stats.data ?? [])
              .filter((s) => s.acceptance_rate !== null)
              .map((s) => `${s.source}: ${Math.round((s.acceptance_rate ?? 0) * 100)}% accepted`)
              .join(" · ")}
          </span>
          <button disabled={suggest.isPending} onClick={() => suggest.mutate()}>
            {suggest.isPending ? "Suggesting..." : "Suggest categories"}
          </button>
        </div>
        {suggest.error && <p className="error">{suggest.error.message}</p>}
        {transactions.data?.length === 0 ? (
          <p className="muted">Nothing to review. Import a statement to get started.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Description</th>
                <th className="num">Amount</th>
                <th>Suggestion</th>
                <th>Categorize</th>
              </tr>
            </thead>
            <tbody>
              {(transactions.data ?? []).map((tx) => (
                <ReviewRow
                  // Re-mount when a suggestion arrives, so the dropdown picks it up.
                  key={`${tx.id}-${byTx.get(tx.id)?.id ?? "none"}`}
                  tx={tx}
                  suggestion={byTx.get(tx.id)}
                  categories={categories}
                  accounts={accounts}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
