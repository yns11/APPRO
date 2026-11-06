/** Thin typed fetch wrapper around the FastAPI backend. */
export class ApiError extends Error {
  status: number;
  /** the ``detail`` of the answer when it is an object (a 409 of the Planning transport carries the slot check) */
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

export type Params = Record<string, string | number | boolean | null | undefined | (string | number)[]>;

export function qs(params?: Params): string {
  if (!params) return "";
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => usp.append(k, String(x)));
    else usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

async function parseError(res: Response): Promise<{ message: string; detail?: unknown }> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return { message: body.detail };
    if (Array.isArray(body?.detail)) return { message: body.detail.map((d: { msg: string; loc?: unknown[] }) => `${(d.loc ?? []).join(".")}: ${d.msg}`).join(" ; ") };
    if (body?.detail && typeof body.detail === "object") return { message: body.detail.message ?? `HTTP ${res.status}`, detail: body.detail };
    return { message: JSON.stringify(body) };
  } catch {
    return { message: res.statusText || `HTTP ${res.status}` };
  }
}

/** Options of a write : ``group`` ties the requests of one gesture (a bulk edit) into one undoable action. */
export interface WriteOpts { group?: string }

/** A write the server journalised as an undoable action (headers X-Action-Id / X-Action-Label). */
export interface RecordedAction { id: string; label: string }
type ActionListener = (a: RecordedAction) => void;
const actionListeners = new Set<ActionListener>();
/** Called after every write that produced an action (the toast « Annuler » of the topbar). */
export function onRecordedAction(fn: ActionListener): () => void {
  actionListeners.add(fn);
  return () => { actionListeners.delete(fn); };
}

async function request<T>(method: string, path: string, body?: unknown, params?: Params, raw?: FormData, opts?: WriteOpts): Promise<T> {
  const headers: Record<string, string> = {};
  const init: RequestInit = { method, headers };
  if (raw) init.body = raw;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  if (opts?.group) headers["X-Action-Group"] = opts.group;
  const res = await fetch(`${path}${qs(params)}`, init);
  if (!res.ok) { const err = await parseError(res); throw new ApiError(res.status, err.message, err.detail); }
  const actionId = res.headers.get("X-Action-Id");
  if (actionId) {
    let label = res.headers.get("X-Action-Label") ?? "";
    try { label = decodeURIComponent(label); } catch { /* keep as is */ }
    actionListeners.forEach((fn) => fn({ id: actionId, label }));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, params?: Params) => request<T>("GET", path, undefined, params),
  post: <T>(path: string, body?: unknown, params?: Params, opts?: WriteOpts) => request<T>("POST", path, body, params, undefined, opts),
  put: <T>(path: string, body?: unknown, opts?: WriteOpts) => request<T>("PUT", path, body, undefined, undefined, opts),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T = void>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, undefined, undefined, form),
  downloadUrl: (path: string, params?: Params) => `${path}${qs(params)}`,
  /** A fresh group id for the writes of one gesture (``X-Action-Group``). */
  newGroup: () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`),
};
