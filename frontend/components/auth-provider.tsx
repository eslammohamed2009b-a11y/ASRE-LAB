"use client";
import {Session} from "@supabase/supabase-js";import {createContext,useContext,useEffect,useState} from "react";import {getSupabase} from "@/lib/supabase";
const AuthContext=createContext<{session:Session|null;loading:boolean}>({session:null,loading:true});
export function AuthProvider({children}:{children:React.ReactNode}){const[session,setSession]=useState<Session|null>(null),[loading,setLoading]=useState(true);useEffect(()=>{const s=getSupabase();s.auth.getSession().then(({data})=>{setSession(data.session);setLoading(false)});const{data}=s.auth.onAuthStateChange((_e,n)=>{setSession(n);setLoading(false)});return()=>data.subscription.unsubscribe()},[]);return <AuthContext.Provider value={{session,loading}}>{children}</AuthContext.Provider>}
export const useAuth=()=>useContext(AuthContext);
