import Link from "next/link";
import { DocumentationContent } from "@/components/documentation-content";

export default function DocumentationPage() {
  return <main className="public-page"><header className="public-nav"><Link className="brand" href="/">ASRE-Lab</Link><div className="nav-actions"><Link href="/app/dashboard">Workspace</Link></div></header><DocumentationContent /></main>;
}
