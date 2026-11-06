"""FastAPI application for memory service."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agents.workflow import build_workflow
from app.memory.client import supabase
from app.memory.context import get_relevant_context
from app.memory.embeddings import embed_or_none
from app.memory import errors
from app.memory.propagation import get_ancestry, get_descendants, share_memory
from app.memory.staleness import find_stale_copies, refresh_stale_copies
from app.memory.store import get_agent_memories, get_memory, save_memory
from app.memory.summaries import get_active_summary, refresh_summary, summary_state
from app.memory.versioning import get_version_history, update_memory
from app.observability.tracing import trace_workflow_run

from .auth import authorize_admin, authorize_agent, require_auth
from .schemas import (
    ContextRequest,
    ContextResponse,
    MemoriesResponse,
    MemoryResponse,
    ProvenanceResponse,
    SaveMemoryRequest,
    ShareMemoryRequest,
    SummariesResponse,
    UpdateMemoryRequest,
    VersionHistoryResponse,
    WorkflowRunRequest,
    WorkflowRunResponse,
)

app = FastAPI(title="Provenance-Aware Memory Service", version="0.1.0")

from .procurement_routes import router as procurement_router  # noqa: E402

app.include_router(procurement_router)

from .observability_routes import router as observability_router  # noqa: E402

app.include_router(observability_router)

from .mail_routes import router as mail_router  # noqa: E402

app.include_router(mail_router)


@app.on_event("shutdown")
def _flush_langfuse() -> None:
    # Langfuse sends in background batches; flush so the last requests' traces are not lost.
    from app.observability import langfuse_tracing

    langfuse_tracing.flush()

_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIST = _ROOT / "web" / "dist"  # the React app (`cd web && npm run build`)
_FRONTEND_DIR = _ROOT / "frontend"  # the previous static pages, used when the React build is absent


class _SPAStaticFiles(StaticFiles):
    """Static files for a single-page app: an unknown path serves index.html so client-side routes
    (/app/chat, /app/observability) survive a reload. Real files (assets) are served as-is."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:  # Starlette's own type: FastAPI's HTTPException is a subclass
            if exc.status_code == 404 and ("." not in Path(path).name or path.endswith(".html")):
                return await super().get_response("index.html", scope)
            raise


# Mounted defensively: if neither directory exists (a stripped-down deployment), the API still starts.
if (_WEB_DIST / "index.html").is_file():
    app.mount("/app", _SPAStaticFiles(directory=str(_WEB_DIST), html=True), name="frontend")
elif _FRONTEND_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


def _memory_error_status(exc: errors.MemoryError) -> int:
    """Map a typed memory error onto the HTTP status that fits it."""
    if isinstance(exc, errors.MemorySchemaMissing):
        return 503
    if isinstance(exc, errors.MemoryNotFound):
        return 404
    if isinstance(exc, (errors.ShareNotAllowed, errors.HopLimitExceeded, errors.SelfShareNotAllowed)):
        return 403
    return 400  # AgentNotRegistered, DuplicateMemory, ConstraintViolation, ...


def _require_owner(agent_id: str, memory_id: str) -> None:
    """An agent may only act on provenance of memories it actually owns."""
    row = get_memory(memory_id, client=supabase)
    if row is None:
        raise HTTPException(status_code=404, detail=f"memory {memory_id} not found")
    if row.get("agent_id") != agent_id:
        raise HTTPException(status_code=403, detail=f"memory {memory_id} is not owned by {agent_id}")


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


@app.get("/agents/{agent_id}/memories", response_model=MemoriesResponse)
def list_agent_memories(
    agent_id: str,
    limit: int | None = Query(default=None, ge=1),
    auth_token: str = Depends(require_auth),
):
    """List an agent's active memories -- the Memory Explorer's data source."""
    authorize_agent(agent_id, auth_token)
    try:
        memories = get_agent_memories(agent_id, limit=limit, client=supabase)
        return MemoriesResponse(agent_id=agent_id, memories=memories)
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


@app.post("/agents/{agent_id}/summaries/refresh")
def refresh_agent_summary(agent_id: str, auth_token: str = Depends(require_auth)):
    """Create (or bring up to date) the summary of this agent's memories.
    Uses the LLM when one is configured, otherwise a labelled extractive
    summary. Afterwards ``POST /agents/{id}/context`` returns the summary
    followed by any memories newer than it."""
    authorize_agent(agent_id, auth_token)
    try:
        try:
            from app.agents.workflow import get_llm

            llm = get_llm("chat_router")
        except Exception:
            llm = None
        summary = refresh_summary(agent_id, llm=llm, client=supabase)
        if summary is None:
            return {"agent_id": agent_id, "summary": None, "detail": "no current memories to summarize"}
        return {"agent_id": agent_id, "summary": summary}
    except errors.MemoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agents/{agent_id}/memory-health")
def memory_health(agent_id: str, auth_token: str = Depends(require_auth)):
    """How trustworthy this agent's memory is right now: counts, the state of
    its summary (fresh / partial / stale), and shared copies whose source has
    since changed."""
    authorize_agent(agent_id, auth_token)
    try:
        rows = supabase.table("agent_memories").select("status,hop_depth").eq("agent_id", agent_id).execute().data or []
        summary = get_active_summary(agent_id, client=supabase)
        state = summary_state(summary, client=supabase) if summary else None
        stale = find_stale_copies(agent_id, client=supabase)
        return {
            "agent_id": agent_id,
            "memories": {
                "active": sum(1 for r in rows if r["status"] == "active"),
                "superseded": sum(1 for r in rows if r["status"] == "superseded"),
                "shared_copies": sum(1 for r in rows if r["status"] == "active" and (r.get("hop_depth") or 0) > 0),
            },
            "summary": None
            if summary is None
            else {
                "id": summary["id"],
                "state": state.state,
                "reason": state.reason,
                "covers": len(summary.get("memory_ids") or []),
                "covered_observed_at": summary.get("covered_observed_at"),
                "newer_memories": len(state.new_memories),
            },
            "stale_copies": [
                {
                    "copy_key": i["copy"]["memory_key"],
                    "copy_version": i["copy"]["version"],
                    "outdated_source": f"{i['source']['agent_id']}/{i['source']['memory_key']} v{i['source']['version']}",
                    "found": i["via"],
                    "can_refresh": i["via"] == "direct" and bool(i["latest_source_id"]),
                }
                for i in stale
            ],
        }
    except errors.MemoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agents/{agent_id}/stale-copies/refresh")
def refresh_agent_stale_copies(agent_id: str, auth_token: str = Depends(require_auth)):
    """Re-share the current version of every stale shared copy in this agent's
    space (see ``GET /agents/{id}/memory-health``)."""
    authorize_agent(agent_id, auth_token)
    try:
        refreshed = refresh_stale_copies(agent_id, client=supabase)
        return {"agent_id": agent_id, "refreshed": [{"memory_key": r["memory_key"], "version": r["version"]} for r in refreshed]}
    except errors.MemoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agents/{agent_id}/memories", response_model=MemoryResponse, status_code=201)
def create_memory(
    agent_id: str,
    request: SaveMemoryRequest,
    auth_token: str = Depends(require_auth),
):
    """Save a memory into the caller's own memory space (idempotent write)."""
    authorize_agent(agent_id, auth_token)
    try:
        row = save_memory(
            agent_id,
            request.memory_key,
            request.content,
            source_agent_id=agent_id,
            source_reference=request.source_reference,
            embedding=embed_or_none(request.content),
            observed_at=request.observed_at,
            subject_key=request.subject_key,
            valid_until=request.valid_until,
            client=supabase,
        )
        return MemoryResponse(memory=row)
    except HTTPException:
        raise
    except errors.MemoryError as e:
        raise HTTPException(status_code=_memory_error_status(e), detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/agents/{agent_id}/memories/{memory_key}", response_model=MemoryResponse)
def update_memory_endpoint(
    agent_id: str,
    memory_key: str,
    request: UpdateMemoryRequest,
    auth_token: str = Depends(require_auth),
):
    """Create the next version of one of the caller's own memories."""
    authorize_agent(agent_id, auth_token)
    try:
        row = update_memory(
            agent_id,
            memory_key,
            request.new_content,
            source_reference=request.source_reference,
            embedding=embed_or_none(request.new_content),
            observed_at=request.observed_at,
            subject_key=request.subject_key,
            valid_until=request.valid_until,
            client=supabase,
        )
        return MemoryResponse(memory=row)
    except HTTPException:
        raise
    except errors.MemoryError as e:
        raise HTTPException(status_code=_memory_error_status(e), detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agents/{agent_id}/memories/{memory_key}/versions", response_model=VersionHistoryResponse)
def memory_versions(
    agent_id: str,
    memory_key: str,
    auth_token: str = Depends(require_auth),
):
    """Every version of one of the caller's own memories, oldest first."""
    authorize_agent(agent_id, auth_token)
    try:
        versions = get_version_history(agent_id, memory_key, client=supabase)
        return VersionHistoryResponse(agent_id=agent_id, memory_key=memory_key, versions=versions)
    except HTTPException:
        raise
    except errors.MemoryError as e:
        raise HTTPException(status_code=_memory_error_status(e), detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agents/{agent_id}/memories/{memory_id}/share", response_model=MemoryResponse, status_code=201)
def share_memory_endpoint(
    agent_id: str,
    memory_id: str,
    request: ShareMemoryRequest,
    auth_token: str = Depends(require_auth),
):
    """Share one of the caller's own memories into another agent's space.

    The sharing policy (who may share with whom, and how many hops) is still
    enforced in the database; this only adds that the caller must own the
    memory it is trying to hand over.
    """
    authorize_agent(agent_id, auth_token)
    try:
        _require_owner(agent_id, memory_id)
        row = share_memory(
            memory_id,
            request.target_agent_id,
            memory_key=request.memory_key,
            max_hops=request.max_hops,
            client=supabase,
        )
        return MemoryResponse(memory=row)
    except HTTPException:
        raise
    except errors.MemoryError as e:
        raise HTTPException(status_code=_memory_error_status(e), detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agents/{agent_id}/memories/{memory_id}/ancestry", response_model=ProvenanceResponse)
def memory_ancestry(
    agent_id: str,
    memory_id: str,
    max_depth: int = Query(default=10, ge=1),
    auth_token: str = Depends(require_auth),
):
    """Provenance ancestry of one of the caller's own memories."""
    authorize_agent(agent_id, auth_token)
    try:
        _require_owner(agent_id, memory_id)
        items = get_ancestry(memory_id, max_depth=max_depth, client=supabase)
        return ProvenanceResponse(memory_id=memory_id, items=items)
    except HTTPException:
        raise
    except errors.MemoryError as e:
        raise HTTPException(status_code=_memory_error_status(e), detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agents/{agent_id}/memories/{memory_id}/descendants", response_model=ProvenanceResponse)
def memory_descendants(
    agent_id: str,
    memory_id: str,
    max_depth: int = Query(default=10, ge=1),
    auth_token: str = Depends(require_auth),
):
    """Memories derived from one of the caller's own memories."""
    authorize_agent(agent_id, auth_token)
    try:
        _require_owner(agent_id, memory_id)
        items = get_descendants(memory_id, max_depth=max_depth, client=supabase)
        return ProvenanceResponse(memory_id=memory_id, items=items)
    except HTTPException:
        raise
    except errors.MemoryError as e:
        raise HTTPException(status_code=_memory_error_status(e), detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/workflow/run", response_model=WorkflowRunResponse)
def run_workflow(
    request: WorkflowRunRequest,
    auth_token: str = Depends(require_auth),
):
    """Run the supervisor-led agent workflow end to end for one task.

    Requires the admin token: this drives every agent, not one agent's own
    resources, so a per-agent scoped token must not be able to call it.

    A failure inside the workflow (an agent's LLM call erroring out, an
    unparseable supervisor/validator reply, the step cap) is not an API
    error: it comes back as a normal 200 response with ``failed=True`` and
    ``error`` set. Only a failure to even start the run (e.g. the workflow
    graph cannot be built) is a 500.
    """
    authorize_admin(auth_token)
    try:
        workflow = build_workflow()
        result = workflow.invoke({"task": request.task})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        trace_workflow_run(request.task, result)
    except Exception:
        # Tracing is best-effort and must never affect the workflow's result.
        pass

    return WorkflowRunResponse(
        task=request.task,
        result=result.get("result"),
        validation_passed=result.get("validation_passed"),
        failed=result.get("failed", False),
        error=result.get("error"),
        step=result.get("step"),
        history=result.get("history", []),
    )
