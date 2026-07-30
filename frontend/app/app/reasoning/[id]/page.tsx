import { ResourceDetail } from "@/components/resource-detail";
export default function Page({params}:{params:{id:string}}){return <ResourceDetail kind="reasoning" id={params.id}/>}
