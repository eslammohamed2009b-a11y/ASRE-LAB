import hashlib,json,sqlite3,uuid
from datetime import datetime,timezone
from app.core.persistence import persistence_service
from app.core.repository import default_local_db_path
from app.v2.evidence_models import (
    EVIDENCE_MODELS,
    BenchmarkEvidence,
    EvidenceType,
    RefinementConvergenceEvidence,
)
from app.module2_simulation.source_resolution import SimulationSourceError, resolve_simulation_source

def canonical(value): return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True)

class EvidenceRepository:
    _COMPUTED_EVIDENCE_TYPES = {
        EvidenceType.NUMERICAL_RESULT,
        EvidenceType.FIELD_RESULT,
        EvidenceType.ANALYSIS,
    }
    _DEFINED_DEPENDENCIES = {
        EvidenceType.BENCHMARK: {EvidenceType.NUMERICAL_RESULT},
        EvidenceType.FIELD_RESULT: {EvidenceType.NUMERICAL_RESULT},
        EvidenceType.REFINEMENT_CONVERGENCE: {
            EvidenceType.NUMERICAL_RESULT,
            EvidenceType.RUN_CONVERGENCE,
        },
    }

    def __init__(self,path=None,repository=None):
        self.source_repository = repository
        repository_path = getattr(repository, "db_path", None)
        repository_client = getattr(repository, "_client", None)
        self.client = repository_client or (
            persistence_service.client
            if persistence_service.enabled and path is None and repository_path is None
            else None
        )
        self.path=path or repository_path or default_local_db_path()
        if self.client is None:
            with sqlite3.connect(self.path) as db: db.execute("""create table if not exists engineering_evidence_records(
            id text primary key,user_id text not null,experiment_id text,simulation_id text,record_type text not null,
            status text not null,schema_version text not null,payload text not null,payload_checksum text not null,
            parent_record_id text,created_at text not null,unique(user_id,record_type,payload_checksum))""")
    def create(self,user_id,value):
        body=canonical(value["payload"]); digest=hashlib.sha256(body.encode()).hexdigest()
        if self.client:
            existing=(self.client.table("engineering_evidence_records").select("*")
                .eq("user_id",user_id).eq("record_type",value["record_type"])
                .eq("payload_checksum",digest).execute().data)
            if existing:return existing[0]
        row={"id":str(uuid.uuid4()),"user_id":user_id,**value,"schema_version":"2.0","payload_checksum":digest,
             "created_at":datetime.now(timezone.utc).isoformat()}
        if self.client:
            data=self.client.table("engineering_evidence_records").upsert(
                row,on_conflict="user_id,record_type,payload_checksum",ignore_duplicates=True
            ).execute().data
            if data:return data[0]
            existing=(self.client.table("engineering_evidence_records").select("*")
                .eq("user_id",user_id).eq("record_type",value["record_type"])
                .eq("payload_checksum",digest).execute().data)
            if existing:return existing[0]
            raise RuntimeError("Scientific evidence upsert did not return a persisted record")
        with sqlite3.connect(self.path) as db:
            try: db.execute("insert into engineering_evidence_records values(?,?,?,?,?,?,?,?,?,?,?)",
                (row["id"],user_id,row.get("experiment_id"),row.get("simulation_id"),row["record_type"],row["status"],
                 "2.0",body,digest,row.get("parent_record_id"),row["created_at"]))
            except sqlite3.IntegrityError:
                old=db.execute("select id from engineering_evidence_records where user_id=? and record_type=? and payload_checksum=?",
                    (user_id,row["record_type"],digest)).fetchone(); return self.get(old[0],user_id)
        return row

    def create_scientific_evidence(self, user_id, payload: dict):
        """Validate typed evidence and owner-scoped provenance before reuse of
        the existing versioned evidence table."""
        try:
            evidence_type=EvidenceType(payload.get("evidence_type"))
            model=EVIDENCE_MODELS[evidence_type].model_validate(payload)
        except Exception as exc:
            raise ValueError("Invalid authoritative scientific evidence payload") from exc
        if model.schema_version != "2.0":
            raise ValueError("Unsupported scientific evidence schema version")

        resolved_sources = []
        if model.simulation_id:
            completed_claim = (
                evidence_type in self._COMPUTED_EVIDENCE_TYPES
                and model.status.value == "completed"
            )
            source = self._resolve_and_validate_simulation(
                model,
                model.simulation_id,
                user_id,
                require_result=evidence_type in self._COMPUTED_EVIDENCE_TYPES
                or evidence_type == EvidenceType.RUN_CONVERGENCE,
                require_completed=completed_claim,
            )
            resolved_sources.append(source)

        if isinstance(model, BenchmarkEvidence):
            source = self._resolve_and_validate_simulation(
                model,
                model.source_simulation_id,
                user_id,
                require_completed=True,
                required_metric=model.metric_name,
            )
            resolved_sources.append(source)

        if isinstance(model, RefinementConvergenceEvidence):
            for level in model.levels:
                source = self._resolve_and_validate_simulation(
                    model,
                    level.simulation_id,
                    user_id,
                    require_completed=True,
                    required_metric=model.selected_metric,
                )
                resolved_sources.append(source)

        experiments = {source.experiment_id for source in resolved_sources if source.experiment_id}
        designs = {source.design_id for source in resolved_sources if source.design_id}
        if len(experiments) > 1:
            raise ValueError("Evidence simulation references span contradictory experiments")
        if len(designs) > 1:
            raise ValueError("Evidence simulation references span contradictory designs")

        for source_id in model.source_ids:
            self._validate_evidence_dependency(model, evidence_type, source_id, user_id)
        return self.create(user_id,{
            "record_type":f"scientific_{evidence_type.value}","status":model.status.value,
            "experiment_id":model.experiment_id,"simulation_id":model.simulation_id,
            "parent_record_id":None,"payload":model.model_dump(mode="json"),
        })

    def _resolve_and_validate_simulation(
        self,
        model,
        simulation_id: str,
        user_id: str,
        *,
        require_completed: bool,
        require_result: bool | None = None,
        required_metric: str | None = None,
    ):
        try:
            source = resolve_simulation_source(
                simulation_id,
                user_id,
                require_result=(require_completed or required_metric is not None)
                if require_result is None else require_result,
                require_completed_result=require_completed,
                required_summary_metric=required_metric,
                repository=self.source_repository,
            )
        except SimulationSourceError as exc:
            raise ValueError("Evidence simulation source is unavailable") from exc
        if model.solver_id and model.solver_id != source.solver_id:
            raise ValueError("Evidence solver_id contradicts simulation source")
        if model.solver_version and source.solver_version and model.solver_version != source.solver_version:
            raise ValueError("Evidence solver_version contradicts simulation source")
        if model.experiment_id and model.experiment_id != source.experiment_id:
            raise ValueError("Evidence experiment_id contradicts simulation source")
        if model.design_id and model.design_id != source.design_id:
            raise ValueError("Evidence design_id contradicts simulation source")
        return source

    def _validate_evidence_dependency(self, model, evidence_type, source_id, user_id):
        record = self.get(source_id, user_id)
        if record is None:
            raise ValueError("Evidence source_ids must resolve to same-owner evidence")
        record_type = record.get("record_type", "")
        if not record_type.startswith("scientific_"):
            raise ValueError("Evidence source_ids must reference authoritative scientific evidence")
        try:
            source_type = EvidenceType(record_type.removeprefix("scientific_"))
            source_model = EVIDENCE_MODELS[source_type].model_validate(record.get("payload"))
        except Exception as exc:
            raise ValueError("Evidence source_ids must reference authoritative scientific evidence") from exc
        allowed = self._DEFINED_DEPENDENCIES.get(evidence_type)
        if allowed is not None and source_type not in allowed:
            raise ValueError(
                f"{evidence_type.value} evidence cannot depend on {source_type.value} evidence"
            )
        if model.experiment_id and source_model.experiment_id and model.experiment_id != source_model.experiment_id:
            raise ValueError("Evidence dependency contradicts experiment_id")
        if allowed is not None:
            if model.solver_id and source_model.solver_id and model.solver_id != source_model.solver_id:
                raise ValueError("Evidence dependency contradicts solver_id")
            if (
                model.solver_version
                and source_model.solver_version
                and model.solver_version != source_model.solver_version
            ):
                raise ValueError("Evidence dependency contradicts solver_version")

    def list_scientific_for_simulation(self, user_id, simulation_id):
        return [row for row in self.list(user_id) if row["simulation_id"] == simulation_id and row["record_type"].startswith("scientific_")]
    def get(self,record_id,user_id):
        if self.client:
            data=self.client.table("engineering_evidence_records").select("*").eq("id",record_id).eq("user_id",user_id).execute().data
            return data[0] if data else None
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row; row=db.execute("select * from engineering_evidence_records where id=? and user_id=?",(record_id,user_id)).fetchone()
        if not row:return None
        result=dict(row);result["payload"]=json.loads(result["payload"]);return result
    def list(self,user_id,record_type=None,parent_record_id=None,experiment_id=None):
        if self.client:
            query=self.client.table("engineering_evidence_records").select("*").eq("user_id",user_id)
            if record_type:query=query.eq("record_type",record_type)
            if parent_record_id:query=query.eq("parent_record_id",parent_record_id)
            if experiment_id:query=query.eq("experiment_id",experiment_id)
            rows=query.order("created_at").execute().data
        else:
            clauses=["user_id=?"];params=[user_id]
            if record_type:clauses.append("record_type=?");params.append(record_type)
            if parent_record_id:clauses.append("parent_record_id=?");params.append(parent_record_id)
            if experiment_id:clauses.append("experiment_id=?");params.append(experiment_id)
            with sqlite3.connect(self.path) as db:
                db.row_factory=sqlite3.Row
                rows=[dict(x) for x in db.execute(
                    f"select * from engineering_evidence_records where {' and '.join(clauses)} order by created_at,id",params)]
        result=[]
        for row in rows:
            item=dict(row)
            if isinstance(item["payload"],str):item["payload"]=json.loads(item["payload"])
            result.append(item)
        return result

    def list_page(self,user_id,record_type,limit=20,offset=0):
        if self.client:
            rows=(self.client.table("engineering_evidence_records").select("*")
                .eq("user_id",user_id).eq("record_type",record_type)
                .order("created_at",desc=True).order("id",desc=True)
                .range(offset,offset+limit-1).execute().data)
        else:
            with sqlite3.connect(self.path) as db:
                db.row_factory=sqlite3.Row
                rows=[dict(x) for x in db.execute(
                    """select * from engineering_evidence_records
                    where user_id=? and record_type=?
                    order by created_at desc,id desc limit ? offset ?""",
                    (user_id,record_type,limit,offset))]
        result=[]
        for row in rows:
            item=dict(row)
            if isinstance(item["payload"],str):item["payload"]=json.loads(item["payload"])
            result.append(item)
        return result
