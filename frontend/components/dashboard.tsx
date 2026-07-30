"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
type RecordItem={id:string;status:string;created_at:string;payload:Record<string,unknown>};
type RecentItem={type:string;id:string;at:string};
type Data={attempts:RecordItem[];manifests:RecordItem[];decisions:RecordItem[];reports:RecordItem[];account:{founding_user:boolean;founding_user_number:number|null;usage_access_period:string}};
const List=({items,label}:{items:RecordItem[];label:string})=><div className="record-list">{items.length?items.map(item=><div className="record" key={item.id}><div><strong>{item.status.replaceAll("_"," ")}</strong><div className="mono">{item.id}</div></div><small>{new Date(item.created_at).toLocaleString()}</small></div>):<div className="empty">No {label.toLowerCase()} yet.</div>}</div>;
export function Dashboard(){
  const[data,setData]=useState<Data|null>(null),[error,setError]=useState(""),[recent,setRecent]=useState<RecentItem[]>([]);
  useEffect(()=>{api<Data>("/api/v2/dashboard").then(setData).catch(reason=>setError(reason.message));try{setRecent(JSON.parse(localStorage.getItem("asre-recent-ids")||"[]"))}catch{setRecent([])}},[]);
  return <div className="page"><div className="page-heading"><div><p className="eyebrow">DASHBOARD</p><h1>Engineering attention queue</h1><p className="muted">Only resources available to this authenticated account are shown.</p></div><Link className="button" href="/app/studies/new">New Engineering Study</Link></div>
  {error&&<p className="error" role="alert">{error}</p>}
  {!data&&!error?<div className="panel">Loading account evidence…</div>:data&&<div className="dashboard-grid">
    <section className="panel span-8"><h2>Active and recent attempts</h2><List items={data.attempts} label="attempts"/></section>
    <section className="panel span-4"><h2>Recognition and access</h2>{data.account.founding_user?<><p><strong>Founding User — First 1,000</strong></p><p className="muted">Permanent recognition · ordinal #{data.account.founding_user_number}</p>{data.account.usage_access_period==="early_access"&&<p><strong>Early Access — Unlimited Usage</strong></p>}<p className="muted">Your badge is permanent. Unlimited usage is part of the current early-access period.</p></>:<p className="muted">Standard account access. No Founding User badge is assigned.</p>}</section>
    <section className="panel span-4"><h2>Pending human decisions</h2><List items={data.decisions.filter(item=>item.status==="proposed")} label="pending decisions"/></section>
    <section className="panel span-4"><h2>Recent reports</h2><List items={data.reports} label="reports"/></section>
    <section className="panel span-4"><h2>Recently viewed on this device</h2>{recent.length?<div className="record-list">{recent.map(item=><div className="record" key={`${item.type}-${item.id}`}><span>{item.type}<span className="mono">{item.id}</span></span><small>{new Date(item.at).toLocaleString()}</small></div>)}</div>:<div className="empty">No resources opened on this device.</div>}</section>
    <section className="panel span-12"><h2>Recent manifests</h2><List items={data.manifests} label="manifests"/></section>
  </div>}</div>;
}
