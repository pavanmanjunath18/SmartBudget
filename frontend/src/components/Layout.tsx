import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { DemoBanner } from "./DemoBanner";

const LINKS = [
  { to: "/", label: "Dashboard" },
  { to: "/transactions", label: "Transactions" },
  { to: "/invoices", label: "Invoices" },
  { to: "/reports", label: "Reports" },
];

export function Layout() {
  const { me, org, logout, selectOrg } = useAuth();
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">SmartBudget</span>
        <nav>
          {LINKS.map((link) => (
            <NavLink key={link.to} to={link.to} end={link.to === "/"}>
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="topbar-right">
          {me && me.organizations.length > 1 ? (
            <select
              value={org?.id}
              onChange={(e) => selectOrg(Number(e.target.value))}
              aria-label="Organization"
            >
              {me.organizations.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          ) : (
            <span className="muted">{org?.name}</span>
          )}
          <button className="link" onClick={logout}>
            Log out
          </button>
        </div>
      </header>
      {me?.demo && <DemoBanner />}
      <main>
        <Outlet />
      </main>
    </div>
  );
}
