"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { FieldRow } from "@/components/FieldRow";
import { correctFields, getJob } from "@/lib/api";
import { band, type FieldValue, type JobResponse } from "@/lib/types";

/** Flatten the nested result into dotted paths, mirroring the backend. */
function flatten(node: unknown, prefix = ""): Record<string, FieldValue> {
  const out: Record<string, FieldValue> = {};
  if (node && typeof node === "object") {
    if ("confidence" in (node as object) && "source" in (node as object)) {
      out[prefix] = node as FieldValue;
      return out;
    }
    for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
      Object.assign(out, flatten(v, prefix ? `${prefix}.${k}` : k));
    }
  }
  return out;
}

export default function ReviewPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<JobResponse | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [focused, setFocused] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => { getJob(id).then(setJob); }, [id]);

  const fields = useMemo(() => {
    if (!job?.result) return {};
    return {
      ...flatten(job.result.person, "person"),
      ...flatten(job.result.documents, "documents"),
    };
  }, [job]);

  // Anything the system is unsure about goes to the top. The user's attention
  // is the scarcest resource on this screen; spending it on a green field that
  // a check digit already verified is waste.
  const ordered = useMemo(() => {
    const rank = { doubtful: 0, uncertain: 1, confident: 2 } as const;
    return Object.entries(fields)
      .filter(([, f]) => f.value !== null || true)
      .sort(([, a], [, b]) => rank[band(a)] - rank[band(b)]);
  }, [fields]);

  if (!job) return <p className="text-stone-600">Yuklanmoqda…</p>;

  const review = job.needs_review ?? [];

  async function save() {
    setSaving(true);
    await correctFields(id, edits);
    router.push(`/templates?job=${id}`);
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Ma’lumotlarni tekshiring</h1>
        <p className="mt-1 text-sm text-stone-600">
          Yashil chiziqli maydonlar nazorat raqami bilan tasdiqlangan. Sariq va
          qizil maydonlarni albatta tekshiring.
        </p>
      </div>

      {review.length > 0 && (
        <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">
          {review.length} ta maydon tekshirishni talab qiladi.
        </p>
      )}

      {(job.warnings ?? []).map((w) => (
        <p key={w} className="rounded-md bg-red-50 p-3 text-sm text-red-900">{w}</p>
      ))}

      <div className="space-y-2">
        {ordered.map(([path, field]) => (
          <FieldRow
            key={path}
            path={path}
            field={{ ...field, value: edits[path] ?? field.value }}
            focused={focused === path}
            onFocus={() => setFocused(path)}
            onChange={(v) => setEdits((e) => ({ ...e, [path]: v }))}
          />
        ))}
      </div>

      <button onClick={save} disabled={saving}
        className="rounded-lg bg-stone-900 px-5 py-2.5 text-white disabled:opacity-40">
        {saving ? "Saqlanmoqda…" : "Tasdiqlash va shablon tanlash"}
      </button>
    </div>
  );
}
