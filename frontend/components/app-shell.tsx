"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { getSupabase } from "@/lib/supabase";
import { useAuth } from "./auth-provider";
import { CommandPalette, type WorkspaceCommand } from "./ui/command-palette";

type Account = { email: string | null; founding_user: boolean; founding_user_number: number | null; usage_access: string; usage_access_period: string };

const navigation: WorkspaceCommand[] = [
  { label: "Research Studies", description: "Review persisted studies and execution state", href: "/app/dashboard", shortcut: "1" },
  { label: "New Research Study", description: "Define a new evidence-backed investigation", href: "/app/studies/new", shortcut: "2" },
  { label: "Open Evidence Item", description: "Retrieve a scientific record by identifier", href: "/app/open", shortcut: "3" },
  { label: "Scientific Scope", description: "Review supported physics and validity limits", href: "/scientific-scope", shortcut: "4" },
  { label: "Documentation", description: "Open ASRE-Lab research documentation", href: "/docs", shortcut: "5" },
];

function isActive(pathname: string, href: string) { return href === "/app/dashboard" ? pathname === href : pathname === href || pathname.startsWith(`${href}/`); }
function pageContext(pathname: string) {
  if (pathname.startsWith("/app/studies/new")) return "New research study";
  if (pathname.startsWith("/app/studies")) return "Research study";
  if (pathname.startsWith("/app/open")) return "Evidence retrieval";
  if (pathname.startsWith("/scientific-scope")) return "Scientific scope";
  if (pathname.startsWith("/docs")) return "Documentation";
  return "Research studies";
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { session, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [account, setAccount] = useState<Account | null>(null);
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => { if (!loading && !session && !signingOut) router.replace(`/auth/log-in?returnTo=${encodeURIComponent(pathname)}`); }, [loading, session, signingOut, pathname, router]);
  useEffect(() => { if (session) api<Account>("/api/v2/account/me").then(setAccount).catch(() => {}); }, [session]);
  const activePage = useMemo(() => pageContext(pathname), [pathname]);

  if (loading || !session) return <main className="app-loading" aria-live="polite">Restoring secure session…</main>;

  return <div className="app-shell">
    <aside className="workspace-sidebar" aria-label="Research workspace navigation">
      <Link className="workspace-brand" href="/app/dashboard" aria-label="ASRE-Lab research workspace"><span className="workspace-brand-mark" aria-hidden="true">A</span><span>ASRE-Lab</span></Link>
      <p className="workspace-nav-label">Workspace</p>
      <nav className="workspace-nav" aria-label="Application navigation">{navigation.map((item, index) => <Link key={item.href} href={item.href} data-active={isActive(pathname, item.href)}><span className="workspace-nav-index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span><span>{item.label}</span></Link>)}</nav>
      <div className="workspace-account">
        <div className="workspace-account-identity"><span className="workspace-account-avatar" aria-hidden="true">{(account?.email || session.user.email || "A").slice(0, 1).toUpperCase()}</span><span><strong>{account?.email || session.user.email}</strong><small>Authenticated researcher</small></span></div>
        {account?.founding_user && <p className="workspace-account-note">Founding User · First 1,000 #{account.founding_user_number}</p>}
        {account?.usage_access_period === "early_access" && <p className="workspace-account-note">Early access · Unlimited usage</p>}
        <button type="button" className="workspace-logout" disabled={signingOut} onClick={async () => { setSigningOut(true); await getSupabase().auth.signOut(); router.replace("/"); }}>{signingOut ? "Signing out…" : "Log out"}</button>
      </div>
    </aside>
    <main className="app-main">
      <header className="workspace-topbar"><div className="workspace-context"><span>ASRE /</span><strong>{activePage}</strong></div><CommandPalette commands={navigation} onNavigate={router.push} /><span className="workspace-evidence-state"><i aria-hidden="true" /> Evidence-first research</span></header>
      <div className="workspace-content">{children}</div>
    </main>
  </div>;
}
