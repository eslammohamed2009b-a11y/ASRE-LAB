from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

RecordType = Literal["scientific_trust","run_manifest","engineering_decision","job_attempt","reasoning_event","research_report"]

class EvidenceCreate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    record_type: RecordType
    status: str=Field(min_length=1,max_length=50)
    experiment_id: str|None=None
    simulation_id: str|None=None
    parent_record_id: str|None=None
    payload: dict[str,Any]

class EvidenceResponse(EvidenceCreate):
    id: str
    schema_version: str
    payload_checksum: str
    created_at: datetime
