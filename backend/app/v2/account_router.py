from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.core.persistence import persistence_service
from app.core.repository import default_local_db_path
from app.v2.repository import EvidenceRepository

router = APIRouter(prefix="/api/v2", tags=["Backend V2 - Account and Collections"])


class AccountResponse(BaseModel):
    user_id: str
    email: str | None = None
    founding_user: bool
    founding_user_number: int | None = Field(default=None, ge=1, le=1000)
    usage_access: Literal["unlimited", "standard"]
    usage_access_period: Literal["early_access", "standard"]
    created_at: str
    updated_at: str


class CollectionResponse(BaseModel):
    items: list[dict[str, Any]]
    limit: int
    offset: int
    has_more: bool


_COLLECTION_TYPES = {
    "manifests": "run_manifest",
    "attempts": "job_attempt",
    "decisions": "engineering_decision",
    "reasoning": "reasoning_event",
    "reports": "research_report",
}


def _provision_account(user_id: str, email: str | None) -> dict[str, Any]:
    """Provision once. PostgreSQL uses one locked DB function; SQLite uses BEGIN IMMEDIATE."""
    now = datetime.now(timezone.utc).isoformat()
    if persistence_service.enabled:
        rows = persistence_service.client.rpc(
            "provision_asre_account", {"requested_user_id": user_id, "requested_email": email}
        ).execute().data
        return rows[0] if isinstance(rows, list) else rows

    path = default_local_db_path()
    with sqlite3.connect(path, timeout=30, isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """create table if not exists asre_account_allocation_state(
            singleton integer primary key check(singleton=1),
            last_allocated_ordinal integer not null check(last_allocated_ordinal>=0))"""
        )
        db.execute(
            """insert or ignore into asre_account_allocation_state(
            singleton,last_allocated_ordinal) values(1,0)"""
        )
        db.execute(
            """create table if not exists asre_accounts(
            user_id text primary key,email text,founding_user_number integer unique,
            usage_access text not null,usage_access_period text not null,
            created_at text not null,updated_at text not null)"""
        )
        existing = db.execute("select * from asre_accounts where user_id=?", (user_id,)).fetchone()
        if existing:
            if email and existing["email"] != email:
                db.execute(
                    "update asre_accounts set email=?,updated_at=? where user_id=?",
                    (email, now, user_id),
                )
            row = db.execute("select * from asre_accounts where user_id=?", (user_id,)).fetchone()
        else:
            last_number = db.execute(
                "select last_allocated_ordinal from asre_account_allocation_state where singleton=1"
            ).fetchone()[0]
            next_number = last_number + 1
            db.execute(
                "update asre_account_allocation_state set last_allocated_ordinal=? where singleton=1",
                (next_number,),
            )
            ordinal = next_number if next_number <= 1000 else None
            db.execute(
                "insert into asre_accounts values(?,?,?,?,?,?,?)",
                (
                    user_id,
                    email,
                    ordinal,
                    "unlimited" if ordinal else "standard",
                    "early_access" if ordinal else "standard",
                    now,
                    now,
                ),
            )
            row = db.execute("select * from asre_accounts where user_id=?", (user_id,)).fetchone()
        db.commit()
    return dict(row)


def _account_response(user: dict[str, Any]) -> AccountResponse:
    row = _provision_account(user["id"], user.get("email"))
    number = row.get("founding_user_number")
    return AccountResponse(
        user_id=str(row.get("user_id") or row.get("id")),
        email=row.get("email"),
        founding_user=number is not None,
        founding_user_number=number,
        usage_access=row["usage_access"],
        usage_access_period=row["usage_access_period"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _collection(record_type: str, user_id: str, limit: int, offset: int) -> CollectionResponse:
    rows = EvidenceRepository().list_page(user_id, record_type, limit + 1, offset)
    return CollectionResponse(
        items=rows[:limit], limit=limit, offset=offset, has_more=len(rows) > limit
    )


@router.get("/account/me", response_model=AccountResponse)
def account_me(user: dict[str, Any] = Depends(get_current_user)) -> AccountResponse:
    return _account_response(user)


@router.get("/execution/runs", response_model=CollectionResponse)
@router.get("/execution/manifests", response_model=CollectionResponse)
def manifests(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
    user: dict[str, Any] = Depends(get_current_user),
) -> CollectionResponse:
    return _collection("run_manifest", user["id"], limit, offset)


@router.get("/execution/attempts", response_model=CollectionResponse)
def attempts(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
    user: dict[str, Any] = Depends(get_current_user),
) -> CollectionResponse:
    return _collection("job_attempt", user["id"], limit, offset)


def evidence_collection(
    collection: Literal["decisions", "reasoning", "reports"],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
    user: dict[str, Any] = Depends(get_current_user),
) -> CollectionResponse:
    return _collection(_COLLECTION_TYPES[collection], user["id"], limit, offset)


@router.get("/dashboard")
def dashboard(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    repo = EvidenceRepository()
    return {
        "account": _account_response(user).model_dump(),
        "attempts": repo.list_page(user["id"], "job_attempt", 10, 0),
        "manifests": repo.list_page(user["id"], "run_manifest", 10, 0),
        "decisions": repo.list_page(user["id"], "engineering_decision", 10, 0),
        "reports": repo.list_page(user["id"], "research_report", 10, 0),
    }

@router.get("/decisions", response_model=CollectionResponse)
def decisions(limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10_000),user:dict[str,Any]=Depends(get_current_user)):
    return evidence_collection("decisions",limit,offset,user)

@router.get("/reasoning", response_model=CollectionResponse)
def reasoning_records(limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10_000),user:dict[str,Any]=Depends(get_current_user)):
    return evidence_collection("reasoning",limit,offset,user)

@router.get("/reports", response_model=CollectionResponse)
def reports(limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10_000),user:dict[str,Any]=Depends(get_current_user)):
    return evidence_collection("reports",limit,offset,user)
