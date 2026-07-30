import {createClient,SupabaseClient} from "@supabase/supabase-js";
let client:SupabaseClient|undefined;
export function getSupabase(){if(client)return client;const url=process.env.NEXT_PUBLIC_SUPABASE_URL,key=process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;if(!url||!key)throw new Error("Authentication is not configured.");client=createClient(url,key,{auth:{persistSession:true,autoRefreshToken:true,detectSessionInUrl:true}});return client}
