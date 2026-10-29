import Link from "next/link";
import { ScientificScopeContent } from "@/components/scientific-scope-content";

export default function Scope() { return <main className="public-page"><header className="public-nav"><Link className="brand" href="/">ASRE-Lab</Link><div className="nav-actions"><Link className="button" href="/auth/log-in">Start ASRE-Lab</Link></div></header><ScientificScopeContent /></main>; }
