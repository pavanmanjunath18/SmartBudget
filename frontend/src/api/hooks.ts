import { useQuery } from "@tanstack/react-query";

import { useOrgId } from "../auth/AuthContext";
import { get } from "./client";
import type { Account } from "./types";

/** The organization's chart of accounts, shared by every page that picks an account. */
export function useAccounts() {
  const orgId = useOrgId();
  return useQuery({
    queryKey: ["accounts", orgId],
    queryFn: () => get<Account[]>(`/orgs/${orgId}/accounts`),
  });
}

export function accountLabel(accounts: Account[] | undefined, id: number): string {
  const account = accounts?.find((a) => a.id === id);
  return account ? `${account.code} ${account.name}` : `#${id}`;
}
