from pathlib import Path
from app.v2.repository import EvidenceRepository

def test_evidence_idempotency_and_ownership(tmp_path):
    repo=EvidenceRepository(str(tmp_path/"v2.db"))
    value={"record_type":"run_manifest","status":"created","experiment_id":None,"simulation_id":None,
           "parent_record_id":None,"payload":{"solver":"thermal","normalized_inputs":{"length_m":1.0}}}
    first=repo.create("a",value);again=repo.create("a",value)
    assert first["id"]==again["id"]
    assert len(first["payload_checksum"])==64
    assert repo.get(first["id"],"a")["payload"]==value["payload"]
    assert repo.get(first["id"],"b") is None

def test_migrations_are_identical_and_owner_scoped():
    root=Path(__file__).parents[3]
    a=(root/"database/migrations/011_backend_v2_foundation.sql").read_bytes()
    b=(root/"backend/supabase/migrations/20260728010000_backend_v2_foundation.sql").read_bytes()
    assert a==b
    text=a.decode()
    assert "enable row level security" in text and "user_id=auth.uid()" in text
