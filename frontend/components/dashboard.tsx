"use client";
import Link from "next/link";
import {useEffect,useState} from "react";
import {api} from "@/lib/api";

type RecordItem={id:string;status:string;created_at:string;payload:Record<string,unknown>};
type Study={id:string;title:string;status:string;updated_at:string;design_count:number;simulation_count:number;completed_run_count:number;failed_run_count:number;analysis_count:number;report_count:number};
type Data={attempts:RecordItem[];decisions:RecordItem[];reports:RecordItem[];account:{founding_user:boolean;founding_user_number:number|null;usage_access_period:string}};

export function Dashboard(){
  const[data,setData]=useState<Data|null>(null),[studies,setStudies]=useState<Study[]>([]),[error,setError]=useState("");
  useEffect(()=>{Promise.all([api<Data>("/api/v2/dashboard"),api<{items:Study[]}>("/api/studies")]).then(([dashboard,collection])=>{setData(dashboard);setStudies(collection.items)}).catch(reason=>setError(reason.message))},[]);
  return <div className="page"><div className="page-heading"><div><p className="eyebrow">RESEARCH DASHBOARD</p><h1>Persisted studies</h1><p className="muted">Study state and counts come from the authenticated server, not this device.</p></div><Link className="button" href="/app/studies/new">New Research Study</Link></div>
  {error&&<p className="error" role="alert">{error}</p>}
  {!data&&!error?<div className="panel">Loading server-side studiesâ€¦</div>:data&&<div className="dashboard-grid">
    <section className="panel span-8"><h2>Your studies</h2>{studies.length?<table className="technical-table"><thead><tr><th>Study</th><th>Status</th><th>Designs</th><th>Runs</th><th>Analysis</th><th>Report</th><th>Updated</th></tr></thead><tbody>{studies.map(study=><tr key={study.id}><td><Link href={`/app/studies/${study.id}`}><strong>{study.title}</strong><br/><span className="mono">{study.id}</span></Link></td><td>{study.status}</td><td>{study.design_count}</td><td>{study.completed_run_count}/{study.simulation_count}{study.failed_run_count?` (${study.failed_run_count} failed)`:""}</td><td>{study.analysis_count}</td><td>{study.report_count}</td><td>{new Date(study.updated_at).toLocaleString()}</td></tr>)}</tbody></table>:<div className="empty">No persisted studies yet.</div>}</section>
    <section className="panel span-4"><h2>Recognition and access</h2>{data.account.founding_user?<><p><strong>Founding User â€” First 1,000</strong></p><p className="muted">Permanent recognition Â· ordinal #{data.account.founding_user_number}</p>{data.account.usage_access_period==="early_access"&&<p><strong>Early Access â€” Unlimited Usage</strong></p>}</>:<p className="muted">Standard account access.</p>}</section>
    <section className="panel span-4"><h2>Pending human decisions</h2><p>{data.decisions.filter(item=>item.status==="proposed").length} require review.</p></section>
    <section className="panel span-4"><h2>Reports</h2><p>{data.reports.length} recent owner-scoped reports.</p></section>
    <section className="panel span-4"><h2>Worker attempts</h2><p>{data.attempts.length} recent durable execution attempts.</p></section>
  </div>}</div>;
}
