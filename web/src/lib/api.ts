// Fetch client over the real FastAPI backend. The browser never holds an API token: the backend
// authorizes this bundled UI itself (UI_LOCAL_AUTH, loopback only; see app/api/auth.py).

export const AUTH_HELP =
  "The backend did not authorize this page. Set UI_LOCAL_AUTH=1 in the server's .env, open the app at http://127.0.0.1:8000, and restart the server.";

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(message: string, status: number, data?: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export async function apiFetch<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  let data: any;
  try {
    data = await response.json();
  } catch {
    data = { detail: "(no JSON body)" };
  }
  if (!response.ok) {
    const detail =
      response.status === 401
        ? AUTH_HELP
        : typeof data?.detail === "string"
          ? data.detail
          : `HTTP ${response.status}`;
    throw new ApiError(detail, response.status, data);
  }
  return data as T;
}

/** POST to a Server-Sent Events endpoint and dispatch each event to handlers[event](data) as it arrives. */
export async function streamPost(
  path: string,
  body: unknown,
  handlers: Record<string, (data: any) => void | Promise<void>>,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    let data: any = {};
    try {
      data = await response.json();
    } catch {
      /* no JSON body */
    }
    throw new ApiError(response.status === 401 ? AUTH_HELP : data.detail || `HTTP ${response.status}`, response.status);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      const handler = handlers[event];
      if (handler) await handler(JSON.parse(data || "null"));
    }
  }
}
