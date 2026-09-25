import Link from "next/link";
import { ScientificScopeContent } from "@/components/scientific-scope-content";

export default function Scope() { return <main className="public-page"><header className="public-nav"><Link className="brand" href="/">ASRE-Lab</Link><div className="nav-actions"><Link href="/auth/log-in">Log In</Link><Link className="button" href="/auth/sign-up">Get Started</Link></div></header><ScientificScopeContent /></main>; }
