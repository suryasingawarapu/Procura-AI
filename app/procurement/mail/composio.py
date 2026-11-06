"""A minimal Composio MCP client, only for the Gmail tools this app needs.

Composio's hosted MCP (``https://connect.composio.dev/mcp``) takes the consumer
API key in an ``x-consumer-api-key`` header. Gmail calls go through its
``COMPOSIO_MULTI_EXECUTE_TOOL`` with an explicit connected account, so a call
can never silently create or use some other connection.

Configuration (environment):
    COMPOSIO_API_KEY        consumer key (never logged or returned)
    COMPOSIO_GMAIL_ACCOUNT  connected account id/alias to send from
    COMPOSIO_MCP_URL        override of the default MCP endpoint
"""

from __future__ import annotations

import base64
import json
import os
import threading
from typing import Any, Dict, Optional

import httpx

DEFAULT_URL = "https://connect.composio.dev/mcp"
_SESSION_ID = "procurement-mail"


class ComposioError(RuntimeError):
    """Composio is unconfigured, unreachable, or the tool call failed."""


def configured() -> bool:
    return bool(os.getenv("COMPOSIO_API_KEY", "").strip() and os.getenv("COMPOSIO_GMAIL_ACCOUNT", "").strip())


def _sse_json(resp: httpx.Response) -> Dict[str, Any]:
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return resp.json()


class ComposioGmail:
    def __init__(self, *, api_key: Optional[str] = None, account: Optional[str] = None, url: Optional[str] = None):
        self._key = (api_key or os.getenv("COMPOSIO_API_KEY", "")).strip()
        self.account = (account or os.getenv("COMPOSIO_GMAIL_ACCOUNT", "")).strip()
        self._url = url or os.getenv("COMPOSIO_MCP_URL") or DEFAULT_URL
        if not self._key or not self.account:
            raise ComposioError("COMPOSIO_API_KEY and COMPOSIO_GMAIL_ACCOUNT must both be set")
        self._lock = threading.Lock()
        self._headers: Optional[Dict[str, str]] = None
        self._next_id = 10

    def _base_headers(self) -> Dict[str, str]:
        return {"x-consumer-api-key": self._key, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

    def _open(self, client: httpx.Client) -> Dict[str, str]:
        r = client.post(self._url, headers=self._base_headers(), json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "procurement-mail", "version": "1"}},
        })
        if r.status_code == 401:
            raise ComposioError("Composio rejected the API key")
        r.raise_for_status()
        headers = self._base_headers()
        if r.headers.get("mcp-session-id"):
            headers["Mcp-Session-Id"] = r.headers["mcp-session-id"]
        client.post(self._url, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        return headers

    def execute(self, tool_slug: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Run one Gmail tool; returns its ``data`` payload or raises ComposioError."""
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            try:
                with httpx.Client(timeout=60) as client:
                    if self._headers is None:
                        self._headers = self._open(client)
                    r = client.post(self._url, headers=self._headers, json={
                        "jsonrpc": "2.0", "id": rid, "method": "tools/call",
                        "params": {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "arguments": {
                            "session_id": _SESSION_ID,
                            "thought": f"procurement mail: {tool_slug}",
                            "tools": [{"tool_slug": tool_slug, "arguments": arguments, "account": self.account}],
                        }},
                    })
                    if r.status_code in (401, 404):
                        self._headers = None  # a stale MCP session: the next call reopens it
                    r.raise_for_status()
                    body = _sse_json(r)
            except httpx.HTTPError as exc:
                raise ComposioError(f"Composio request failed: {type(exc).__name__}") from exc
        result = body.get("result") or {}
        if result.get("isError") or body.get("error"):
            raise ComposioError(f"Composio error calling {tool_slug}")
        try:
            outer = json.loads(result["content"][0]["text"])
        except (KeyError, IndexError, ValueError) as exc:
            raise ComposioError(f"unreadable Composio response for {tool_slug}") from exc
        if not outer.get("successful", False):
            raise ComposioError(f"Composio could not run {tool_slug}: {outer.get('error') or 'unknown error'}")
        results = (outer.get("data") or {}).get("results") or []
        if not results:
            raise ComposioError(f"no result returned for {tool_slug}")
        item = results[0]
        resp = item.get("response") or item
        if item.get("error") or resp.get("successful") is False:
            raise ComposioError(f"{tool_slug} failed: {item.get('error') or resp.get('error') or 'unknown error'}")
        return resp.get("data") or resp

    def _meta_call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call one of Composio's own tools (not a toolkit action); returns its ``data`` payload."""
        with self._lock:
            self._next_id += 1
            try:
                with httpx.Client(timeout=120) as client:
                    if self._headers is None:
                        self._headers = self._open(client)
                    r = client.post(self._url, headers=self._headers, json={
                        "jsonrpc": "2.0", "id": self._next_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
                    if r.status_code in (401, 404):
                        self._headers = None
                    r.raise_for_status()
                    body = _sse_json(r)
            except httpx.HTTPError as exc:
                raise ComposioError(f"Composio request failed: {type(exc).__name__}") from exc
        result = body.get("result") or {}
        try:
            outer = json.loads(result["content"][0]["text"])
        except (KeyError, IndexError, ValueError) as exc:
            raise ComposioError(f"unreadable Composio response for {name}") from exc
        if result.get("isError") or not outer.get("successful", False):
            raise ComposioError(f"Composio could not run {name}")
        return outer.get("data") or {}

    # --- the Gmail operations the outbox needs -------------------------------------------------
    def upload_file(self, name: str, data: bytes, mimetype: str = "application/pdf") -> Dict[str, str]:
        """Upload bytes to Composio storage (through its sandbox helper) and return a Gmail-ready file reference."""
        path = f"/tmp/{os.path.basename(name)}"
        code = "\n".join([
            "import base64, json",
            f"open({path!r}, 'wb').write(base64.b64decode({base64.b64encode(data).decode()!r}))",
            f"res, err = upload_local_file({path!r})",
            "print('UPLOAD_RESULT=' + json.dumps({'s3key': (res or {}).get('s3key'), 'error': str(err or '')}))",
        ])
        out = self._meta_call("COMPOSIO_REMOTE_WORKBENCH", {
            "code_to_execute": code, "thought": "upload the purchase order PDF", "session_id": _SESSION_ID})
        marker = next((ln for ln in str(out.get("stdout", "")).splitlines() if ln.startswith("UPLOAD_RESULT=")), None)
        info = json.loads(marker.split("=", 1)[1]) if marker else {}
        if not info.get("s3key"):
            raise ComposioError(f"could not upload {name}: {info.get('error') or 'no file reference returned'}")
        return {"name": os.path.basename(name), "mimetype": mimetype, "s3key": info["s3key"]}

    def send(self, *, to: str, subject: str, body: str, attachment: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        args: Dict[str, Any] = {"recipient_email": to, "subject": subject, "body": body, "is_html": False}
        if attachment:
            args["attachment"] = attachment
        return self.execute("GMAIL_SEND_EMAIL", args)

    def reply(self, *, thread_id: str, to: str, body: str) -> Dict[str, Any]:
        return self.execute("GMAIL_REPLY_TO_THREAD", {"thread_id": thread_id, "recipient_email": to, "message_body": body, "is_html": False})

    def thread(self, thread_id: str) -> Dict[str, Any]:
        return self.execute("GMAIL_FETCH_MESSAGE_BY_THREAD_ID", {"thread_id": thread_id})


_client: Optional[ComposioGmail] = None


def get_client() -> ComposioGmail:
    global _client
    if _client is None:
        _client = ComposioGmail()
    return _client
