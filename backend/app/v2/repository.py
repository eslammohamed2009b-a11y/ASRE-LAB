import hashlib,json,sqlite3,uuid
from datetime import datetime,timezone
from app.core.persistence import persistence_service
from app.core.repository import default_local_db_path

def canonical(value): return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True)

class EvidenceRepository:
    def __init__(self,path=None):
        self.client=persistence_service.client if persistence_service.enabled and path is None else None
        self.path=path or default_local_db_path()
        if self.client is None:
            with sqlite3.connect(self.path) as db: db.execute("""create table if not exists engineering_evidence_records(
            id text primary key,user_id text not null,experiment_id text,simulation_id text,record_type text not null,
            status text not null,schema_version text not null,payload text not null,payload_checksum text not null,
            parent_record_id text,created_at text not null,unique(user_id,record_type,payload_checksum))""")
    def create(self,user_id,value):
        body=canonical(value["payload"]); digest=hashlib.sha256(body.encode()).hexdigest()
        row={"id":str(uuid.uuid4()),"user_id":user_id,**value,"schema_version":"2.0","payload_checksum":digest,
             "created_at":datetime.now(timezone.utc).isoformat()}
        if self.client:
            data=self.client.table("engineering_evidence_records").upsert(row,on_conflict="user_id,record_type,payload_checksum").execute().data
            return data[0]
        with sqlite3.connect(self.path) as db:
            try: db.execute("insert into engineering_evidence_records values(?,?,?,?,?,?,?,?,?,?,?)",
                (row["id"],user_id,row.get("experiment_id"),row.get("simulation_id"),row["record_type"],row["status"],
                 "2.0",body,digest,row.get("parent_record_id"),row["created_at"]))
            except sqlite3.IntegrityError:
                old=db.execute("select id from engineering_evidence_records where user_id=? and record_type=? and payload_checksum=?",
                    (user_id,row["record_type"],digest)).fetchone(); return self.get(old[0],user_id)
        return row
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
