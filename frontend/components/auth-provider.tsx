"use client";
import {Session} from "@supabase/supabase-js";import {createContext,useContext,useEffect,useState} from "react";import {getSupabase,isSupabaseConfigured} from "@/lib/supabase";
const AuthContext=createContext<{session:Session|null;loading:boolean}>({session:null,loading:true});
export function AuthProvider({children}:{children:React.ReactNode}){const[session,setSession]=useState<Session|null>(null),[loading,setLoading]=useState(true);useEffect(()=>{if(!isSupabaseConfigured()){setLoading(false);return}const s=getSupabase();let active=true;s.auth.getSession().then(({data})=>{if(active){setSession(data.session);setLoading(false)}}).catch(()=>{if(active)setLoading(false)});const{data}=s.auth.onAuthStateChange((_e,n)=>{if(active){setSession(n);setLoading(false)}});return()=>{active=false;data.subscription.unsubscribe()}},[]);return <AuthContext.Provider value={{session,loading}}>{children}</AuthContext.Provider>}
export const useAuth=()=>useContext(AuthContext);
