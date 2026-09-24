export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Verdict = "conflict" | "review" | "concord";

export interface DocumentRow {
  id: string;
  filename: string;
  sha256: string;
  version: number;
  uploaded_at: string;
  claim_count: number;
  section_count: number;
}

export interface Chunk {
  id: string;
  ordinal: number;
  section_path: string;
  page: number | null;
  raw_text: string;
  claim_text: string;
}

export interface DocumentDetail extends DocumentRow {
  chunks: Chunk[];
}

export interface Finding {
  id: string;
  severity: "conflict" | "review";
  title: string;
  new_claim: string;
  new_section_path: string;
  new_page: number | null;
  existing: {
    chunk_id: string;
    document_id: string;
    filename: string;
    section_path: string;
    page: number | null;
    raw_text: string;
  };
  same_subject: number | null;
  contradicts: number | null;
  confidence: number | null;
  numeric: [string, string] | null;
  explanation: string | null;
  kind: string | null;
  llm_checked: boolean;
  note: string | null;
}

export interface Job {
  job_id: string;
  upload_id: string;
  filename: string;
  status: "running" | "done" | "error";
  stage: string;
  progress: number;
  message: string;
  documents_total: number;
  claims_total?: number;
  verdict: Verdict | null;
  conflicts: Finding[];
  documents: { document_id: string; filename: string; conflicts: number; reviews: number }[];
  duplicate_of: { id: string; filename: string } | null;
  claims_checked?: number;
  pairs_checked?: number;
  error?: string;
}

export interface Health {
  ok: boolean;
  ready: boolean;
  error: string | null;
  models: Record<string, boolean>;
  llm: { available: boolean; model: string };
  documents: number;
}

export class BackendDown extends Error {
  constructor() {
    super("The backend is not reachable");
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new BackendDown();
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

function form(file: File): FormData {
  const data = new FormData();
  data.append("file", file);
  return data;
}

export const api = {
  health: () => request<Health>("/health"),
  documents: () => request<{ documents: DocumentRow[] }>("/documents").then((r) => r.documents),
  document: (id: string) => request<DocumentDetail>(`/documents/${id}`),
  deleteDocument: (id: string) => request<{ deleted: string }>(`/documents/${id}`, { method: "DELETE" }),
  addDocument: (file: File) =>
    request<{ id: string; created: boolean }>("/documents", { method: "POST", body: form(file) }),
  startCheck: (file: File) =>
    request<{ job_id: string; upload_id: string }>("/uploads/check", { method: "POST", body: form(file) }),
  job: (id: string) => request<Job>(`/uploads/check/${id}`),
  commit: (body: { upload_id: string; action: "commit" | "override" | "replace"; reason?: string; replace_document_ids?: string[] }) =>
    request<{ document_id: string; status: string }>("/uploads/commit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  cancel: (uploadId: string) => request<{ status: string }>(`/uploads/${uploadId}/cancel`, { method: "POST" }),
};

/** "3 Data handling > 3.2 Retention period" -> "§3.2 Retention period" */
export function formatSection(path: string | null | undefined): string {
  if (!path) return "";
  const last = path.split(" > ").pop() ?? "";
  const m = last.match(/^(\d+(?:\.\d+)*)\.?\s+(.*)$/);
  return m ? `§${m[1]} ${m[2]}` : last;
}

export function citation(filename: string, section: string, page: number | null): string {
  return [filename, formatSection(section), page ? `p.${page}` : ""].filter(Boolean).join(", ");
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}
