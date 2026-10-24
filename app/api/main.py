"""FastAPI application for memory service."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query

from app.memory.client import supabase
from app.memory.context import get_relevant_context
from app.memory import errors

from .auth import authorize_agent, require_auth
from .schemas import ContextRequest, ContextResponse, SummariesResponse

app = FastAPI(title="Provenance-Aware Memory Service", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/agents/{agent_id}/summaries", response_model=SummariesResponse)
def get_summaries(
    agent_id: str,
    limit: int | None = Query(default=None, ge=1),
    auth_token: str = Depends(require_auth),
):
    authorize_agent(agent_id, auth_token)
    try:
        query = (
            supabase.table("agent_memory_summaries")
            .select("*")
            .eq("agent_id", agent_id)
            .order("created_at", desc=True)
        )
        if limit is not None:
            query = query.limit(limit)
        summaries = query.execute().data or []
        return SummariesResponse(agent_id=agent_id, summaries=summaries)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agents/{agent_id}/context", response_model=ContextResponse)
def get_context(
    agent_id: str,
    request: ContextRequest,
    auth_token: str = Depends(require_auth),
):
    authorize_agent(agent_id, auth_token)
    if request.agent_id != agent_id:
        raise HTTPException(status_code=400, detail="agent_id mismatch")
    
    try:
        items = get_relevant_context(
            agent_id,
            limit=request.limit,
            memory_limit=request.memory_limit,
            exclude_stale=request.exclude_stale,
            query_embedding=request.query_embedding,
            match_threshold=request.match_threshold,
            match_count=request.match_count,
            client=supabase,
        )
        return ContextResponse(agent_id=agent_id, items=items)
    except HTTPException:
        raise
    except errors.MemoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
