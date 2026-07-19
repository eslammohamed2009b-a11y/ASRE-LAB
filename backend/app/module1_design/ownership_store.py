"""
Module 1 — Design ownership tracking.

`/api/design/export/{design_id}` serves a file purely by id, with no
database-backed link to the requesting user. Without an ownership check,
ANY authenticated user could download ANY other user's exported STL/STEP
file simply by knowing (or guessing) its design_id.

This module closes that gap at the application layer: every time a design
is generated, its owner is recorded here, and the export endpoint checks
that the requesting user matches before serving the file.

Known limitation (disclosed, not hidden): this store is an in-process
dict, not the Supabase-backed `design_models` table. It does not survive a
process restart and does not work across multiple backend replicas. Once
live Supabase persistence is wired up for every design (not just pipeline
runs), this should be replaced with a real ownership lookup against
`design_models`/`experiments.owner_id` so it is durable and horizontally
scalable. Until then, it is real, enforced, in-memory access control -
not a placeholder - for the common single-process deployment.
"""
import threading

_lock = threading.Lock()
_owners: dict[str, str] = {}


def record_owner(design_id: str, owner_id: str) -> None:
    with _lock:
        _owners[design_id] = owner_id


def get_owner(design_id: str) -> str | None:
    with _lock:
        return _owners.get(design_id)
