import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { post } from "../api/client";
import { useAuth } from "../auth/AuthContext";

/** Shown only in the shared demo account: how to try it, and a way to start over. */
export function DemoBanner() {
  const { reloadMe } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = async () => {
    const ok = window.confirm(
      "Reset the demo to its original sample data? Anything imported or changed will be removed, " +
        "for everyone using the shared demo.",
    );
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      await post("/demo/reset");
      await reloadMe(); // the demo business was rebuilt, so it has a new id
      await queryClient.invalidateQueries();
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset the demo.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="demo-banner">
      <span>
        <strong>Shared demo account.</strong> Open <Link to="/transactions">Transactions</Link> to
        import a sample file and watch the numbers change.
      </span>
      {error && <span className="error">{error}</span>}
      <button disabled={busy} onClick={() => void reset()}>
        {busy ? "Resetting..." : "Reset demo data"}
      </button>
    </div>
  );
}
