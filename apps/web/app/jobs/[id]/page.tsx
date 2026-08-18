"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { getJob, streamJob } from "@/lib/api";
import type { JobResponse } from "@/lib/types";

export default function JobProgressPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [label, setLabel] = useState("Navbatda");
  const [job, setJob] = useState<JobResponse | null>(null);

  useEffect(() => {
    const stop = streamJob(id, async (update) => {
      setLabel(update.label);
      if (["ok", "review_needed", "failed", "bad_quality",
           "unknown_doc_type"].includes(update.status)) {
        const full = await getJob(id);
        setJob(full);
        if (["ok", "review_needed"].includes(update.status)) {
          router.push(`/jobs/${id}/review`);
        }
      }
    });
    return stop;
  }, [id, router]);

  // A failure the user can fix is worth more than a correct error code. Every
  // message here names the problem AND the action.
  if (job && job.error_message) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold">Hujjat o‘qilmadi</h1>
        <p className="rounded-md bg-amber-50 p-4 text-amber-900">
          {job.error_message}
        </p>
        <button onClick={() => router.push("/upload")}
          className="rounded-lg bg-stone-900 px-5 py-2.5 text-white">
          Qayta suratga olish
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Qayta ishlanmoqda</h1>
      <div className="rounded-lg border border-stone-200 bg-white p-6">
        <div className="mb-3 h-1.5 w-full overflow-hidden rounded bg-stone-100">
          <div className="h-full w-1/2 animate-pulse rounded bg-stone-800" />
        </div>
        <p className="text-stone-700">{label}…</p>
      </div>
    </div>
  );
}
