import type { JobResponse } from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.message ?? body.detail ?? "So'rov bajarilmadi");
  }
  return res.json() as Promise<T>;
}

export async function createJob(files: File[]): Promise<{ job_id: string }> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  return json(await fetch(`${BASE}/api/v1/jobs`, { method: "POST", body: form }));
}

export async function getJob(id: string): Promise<JobResponse> {
  return json(await fetch(`${BASE}/api/v1/jobs/${id}`, { cache: "no-store" }));
}

export async function correctFields(id: string, corrections: Record<string, string>) {
  return json(
    await fetch(`${BASE}/api/v1/jobs/${id}/fields`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corrections),
    }),
  );
}

export async function listTemplates() {
  return json<Array<{ id: string; name: string; required_fields: string[];
    output_formats: string[] }>>(
    await fetch(`${BASE}/api/v1/templates`, { cache: "no-store" }));
}

export async function generateDocument(
  jobId: string, templateId: string, format: string,
  extra: Record<string, string> = {},
) {
  return json<{ id: string; download_url: string; missing_fields: string[];
    warnings: string[] }>(
    await fetch(`${BASE}/api/v1/documents`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId, template_id: templateId,
        output_format: format, extra_fields: extra }),
    }));
}

/** Live progress. A spinner tells the user nothing; naming the current stage
 *  makes a six-second wait feel like progress rather than a hang. */
export function streamJob(id: string, onUpdate: (s: { status: string; label: string }) => void) {
  const es = new EventSource(`${BASE}/api/v1/jobs/${id}/stream`);
  es.onmessage = (e) => {
    try { onUpdate(JSON.parse(e.data)); } catch { /* ignore keep-alives */ }
  };
  es.onerror = () => es.close();
  return () => es.close();
}

/** Downscale before upload: a 12 MP phone photo is ~5 MB of mostly noise, and
 *  on a slow mobile connection that is the difference between 2s and 30s. */
export async function compressImage(file: File, maxWidth = 2000): Promise<File> {
  if (!file.type.startsWith("image/")) return file;
  const bitmap = await createImageBitmap(file);
  if (bitmap.width <= maxWidth) return file;

  const scale = maxWidth / bitmap.width;
  const canvas = document.createElement("canvas");
  canvas.width = maxWidth;
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")!.drawImage(canvas.width ? bitmap : bitmap, 0, 0,
    canvas.width, canvas.height);

  const blob = await new Promise<Blob | null>((r) =>
    canvas.toBlob(r, "image/jpeg", 0.85));
  return blob ? new File([blob], file.name, { type: "image/jpeg" }) : file;
}
