import {ResearchStudy} from "@/components/research-study";
export default async function Page({params}:{params:Promise<{id:string}>}){const{id}=await params;return <ResearchStudy studyId={id}/>}
