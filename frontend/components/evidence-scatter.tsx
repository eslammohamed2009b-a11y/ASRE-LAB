"use client";
import {useState} from "react";

export type EvidencePoint={designId:string;simulationId:string;x:number;y:number};

export function EvidenceScatter({points,xLabel,yLabel}:{points:EvidencePoint[];xLabel:string;yLabel:string}){
  const[selected,setSelected]=useState<EvidencePoint|null>(null);
  if(points.length<2)return <div className="empty">At least two completed numerical results are required for a comparison plot.</div>;
  const width=720,height=320,pad=52;
  const xs=points.map(point=>point.x),ys=points.map(point=>point.y);
  const xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);
  const sx=(x:number)=>pad+(x-xmin)/(xmax-xmin||1)*(width-2*pad);
  const sy=(y:number)=>height-pad-(y-ymin)/(ymax-ymin||1)*(height-2*pad);
  return <div><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${xLabel} versus ${yLabel} for persisted designs`} style={{width:"100%",maxHeight:360}}><line x1={pad} x2={pad} y1={pad} y2={height-pad} stroke="currentColor"/><line x1={pad} x2={width-pad} y1={height-pad} y2={height-pad} stroke="currentColor"/><text x={width/2} y={height-8} textAnchor="middle">{xLabel}</text><text x={16} y={height/2} textAnchor="middle" transform={`rotate(-90 16 ${height/2})`}>{yLabel}</text>{points.map(point=><circle key={point.simulationId} cx={sx(point.x)} cy={sy(point.y)} r={selected?.simulationId===point.simulationId?8:6} fill="#00685f" tabIndex={0} role="button" aria-label={`Design ${point.designId}: ${xLabel} ${point.x}, ${yLabel} ${point.y}`} onClick={()=>setSelected(point)} onKeyDown={event=>{if(event.key==="Enter"||event.key===" ")setSelected(point)}}><title>{`Design ${point.designId}\nRun ${point.simulationId}\n${xLabel}: ${point.x}\n${yLabel}: ${point.y}`}</title></circle>)}</svg>{selected&&<p className="info"><strong>Selected design:</strong> <span className="mono">{selected.designId}</span><br/><strong>Evidence run:</strong> <span className="mono">{selected.simulationId}</span><br/>{xLabel}: {selected.x} Â· {yLabel}: {selected.y}</p>}</div>;
}
