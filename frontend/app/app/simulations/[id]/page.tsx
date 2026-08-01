import { ResourceDetail } from "@/components/resource-detail";
export default async function Page({params}:{params:Promise<{id:string}>}){const {id}=await params;return <ResourceDetail kind="simulation" id={id}/>}
