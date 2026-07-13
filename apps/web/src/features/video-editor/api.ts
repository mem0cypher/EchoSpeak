import type { EditOperation, VideoAdapter, VideoDocument } from "./types.ts";

const jsonHeaders = { "Content-Type": "application/json" };

async function responseJson(response: Response) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(body?.detail || `Video API failed (${response.status})`));
  return body;
}

export async function listVideoDocuments(apiBase: string, sessionId: string, projectId: string): Promise<VideoDocument[]> {
  const response = await fetch(`${apiBase}/video/projects/${encodeURIComponent(projectId)}/documents?session_id=${encodeURIComponent(sessionId)}`);
  const body = await responseJson(response);
  return Array.isArray(body.items) ? body.items : [];
}

export async function createVideoDocument(apiBase: string, sessionId: string, projectId: string, name: string): Promise<VideoDocument> {
  return responseJson(await fetch(`${apiBase}/video/documents`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ session_id: sessionId, project_id: projectId, name }),
  }));
}

export async function loadVideoDocument(apiBase: string, sessionId: string, projectId: string, documentId: string): Promise<VideoDocument> {
  return responseJson(await fetch(`${apiBase}/video/documents/${encodeURIComponent(documentId)}?session_id=${encodeURIComponent(sessionId)}&project_id=${encodeURIComponent(projectId)}`));
}

export async function importVideoAsset(apiBase: string, sessionId: string, projectId: string, documentId: string, path: string) {
  return responseJson(await fetch(`${apiBase}/video/documents/${encodeURIComponent(documentId)}/assets/import`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ session_id: sessionId, project_id: projectId, project_relative_path: path }),
  }));
}

/** Multipart upload into Project media/ then probe+register assets. */
export async function uploadVideoAssets(
  apiBase: string,
  sessionId: string,
  projectId: string,
  documentId: string,
  files: File[],
) {
  const form = new FormData();
  form.append("session_id", sessionId);
  form.append("project_id", projectId);
  for (const file of files) {
    form.append("files", file, file.name);
  }
  return responseJson(
    await fetch(`${apiBase}/video/documents/${encodeURIComponent(documentId)}/assets/upload`, {
      method: "POST",
      body: form,
    }),
  );
}

export async function applyVideoOperations(
  apiBase: string,
  sessionId: string,
  projectId: string,
  documentId: string,
  operations: EditOperation[],
) {
  return responseJson(await fetch(`${apiBase}/video/documents/${encodeURIComponent(documentId)}/transactions`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ session_id: sessionId, project_id: projectId, operations }),
  }));
}

export async function proposeVideoOperations(
  apiBase: string,
  sessionId: string,
  projectId: string,
  documentId: string,
  objective: string,
  operations: EditOperation[],
) {
  return responseJson(await fetch(`${apiBase}/video/documents/${encodeURIComponent(documentId)}/proposals`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ session_id: sessionId, project_id: projectId, objective, operations }),
  }));
}

export async function decideVideoApproval(apiBase: string, sessionId: string, approvalId: string, decision: "confirm" | "cancel") {
  return responseJson(await fetch(
    `${apiBase}/approvals/${encodeURIComponent(approvalId)}/${decision}?expected_session_id=${encodeURIComponent(sessionId)}`,
    { method: "POST", headers: jsonHeaders },
  ));
}

export async function undoRedoVideo(apiBase: string, sessionId: string, projectId: string, documentId: string, action: "undo" | "redo") {
  return responseJson(await fetch(
    `${apiBase}/video/documents/${encodeURIComponent(documentId)}/${action}?session_id=${encodeURIComponent(sessionId)}&project_id=${encodeURIComponent(projectId)}`,
    { method: "POST" },
  ));
}

export async function listVideoAdapters(apiBase: string): Promise<VideoAdapter[]> {
  const body = await responseJson(await fetch(`${apiBase}/video/adapters`));
  return Array.isArray(body.items) ? body.items : [];
}

export async function createVideoJob(apiBase: string, sessionId: string, projectId: string, documentId: string, adapterId: string) {
  return responseJson(await fetch(`${apiBase}/video/documents/${encodeURIComponent(documentId)}/jobs`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({
      session_id: sessionId,
      project_id: projectId,
      kind: "generation",
      adapter_id: adapterId,
      idempotency_key: crypto.randomUUID(),
      parameters: {},
    }),
  }));
}
