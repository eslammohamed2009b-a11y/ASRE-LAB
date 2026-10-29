import Link from "next/link";
import { AuthForm } from "@/components/auth-form";

export default function LogIn() { return <main className="auth-page"><aside className="auth-aside"><Link className="brand" href="/">ASRE-Lab</Link><div className="auth-aside-copy"><p className="eyebrow">ENGINEERING WORKSPACE</p><h1>Build evidence that can be reviewed.</h1><p>Start an owner-scoped workspace for runs, scientific validation, decisions, reasoning records, and reports.</p></div><p className="auth-aside-note">Bounded models · Private artifacts · Human review</p></aside><section className="auth-card"><p className="eyebrow">START</p><h1>Begin an engineering study</h1><p className="muted">Enter the workspace instantly. No account or password required.</p><AuthForm /></section></main>; }
