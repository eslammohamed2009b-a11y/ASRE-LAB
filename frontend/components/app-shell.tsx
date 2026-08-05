"use client";
import Link from "next/link";
import {usePathname,useRouter} from "next/navigation";
import {useEffect,useState} from "react";
import {useAuth} from "./auth-provider";
import {api} from "@/lib/api";
import {getSupabase} from "@/lib/supabase";

type Account={email:string|null;founding_user:boolean;founding_user_number:number|null;usage_access:string;usage_access_period:string};

export function AppShell({children}:{children:React.ReactNode}){
  const{session,loading}=useAuth(),router=useRouter(),path=usePathname();
  const[account,setAccount]=useState<Account|null>(null),[signingOut,setSigningOut]=useState(false);
  useEffect(()=>{if(!loading&&!session&&!signingOut)router.replace(`/auth/log-in?returnTo=${encodeURIComponent(path)}`)},[loading,session,signingOut,path,router]);
  useEffect(()=>{if(session)api<Account>("/api/v2/account/me").then(setAccount).catch(()=>{})},[session]);
  if(loading||!session)return <main className="app-loading" aria-live="polite">Restoring secure sessionâ€¦</main>;
  return <div className="app-shell"><aside className="sidebar"><Link className="brand" href="/app/dashboard">ASRE-Lab</Link><nav aria-label="Application navigation"><Link href="/app/dashboard">Research Studies</Link><Link href="/app/studies/new">New Research Study</Link><Link href="/app/open">Open Evidence Item</Link><Link href="/scientific-scope">Scientific Scope</Link><Link href="/docs">Documentation</Link></nav><div className="account-summary"><span className="mono">{account?.email||session.user.email}</span>{account?.founding_user&&<span className="founder">Founding User Â· First 1,000 #{account.founding_user_number}</span>}{account?.usage_access_period==="early_access"&&<span>Early Access Â· Unlimited Usage</span>}<button className="secondary" disabled={signingOut} onClick={async()=>{setSigningOut(true);await getSupabase().auth.signOut();router.replace("/")}}>Log Out</button></div></aside><main className="app-main"><header className="app-topbar"><span className="eyebrow">RESEARCH WORKSPACE</span><span className="mono">SERVER STATE ACTIVE</span></header>{children}</main></div>;
}
