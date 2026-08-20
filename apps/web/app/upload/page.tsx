"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ConsentGate } from "@/components/ConsentGate";
import { compressImage, createJob, getConfig } from "@/lib/api";

export default function UploadPage() {
  const router = useRouter();
  const [consented, setConsented] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [demoWarning, setDemoWarning] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Fetched, not hardcoded: the banner has to reflect what the backend is
  // actually configured to do. If the request fails we show nothing rather
  // than guessing — a wrong reassurance is worse than no banner.
  useEffect(() => {
    getConfig()
      .then((c) => setDemoWarning(c.demo_mode ? c.demo_warning : null))
      .catch(() => setDemoWarning(null));
  }, []);

  if (!consented) return <ConsentGate onAccept={() => setConsented(true)} />;

  async function submit() {
    if (!files.length) return;
    setBusy(true);
    setError(null);
    try {
      const compressed = await Promise.all(files.map((f) => compressImage(f)));
      const { job_id } = await createJob(compressed);
      router.push(`/jobs/${job_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Yuklashda xatolik");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold">Hujjat suratini yuklang</h1>

      {/* Amber, above the drop zone, and unmissable. Someone about to
          photograph their passport needs this before they tap, not after. */}
      {demoWarning && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4">
          <p className="font-medium text-amber-900">⚠️ Demo rejim</p>
          <p className="mt-1 text-sm text-amber-900">{demoWarning}</p>
        </div>
      )}

      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          setFiles(Array.from(e.dataTransfer.files).slice(0, 4));
        }}
        onClick={() => inputRef.current?.click()}
        className="cursor-pointer rounded-xl border-2 border-dashed border-stone-300 bg-white p-10 text-center"
      >
        <p className="text-stone-700">Faylni shu yerga tashlang yoki bosing</p>
        <p className="mt-1 text-sm text-stone-500">JPEG, PNG yoki PDF · 10 MB gacha</p>
        <input ref={inputRef} type="file" accept="image/*,application/pdf"
          multiple capture="environment" className="hidden"
          onChange={(e) => setFiles(Array.from(e.target.files ?? []).slice(0, 4))} />
      </div>

      {/* ID cards carry the MRZ on the reverse, and the MRZ is where the
          check-digit-verified fields come from. Saying so up front avoids a
          second round trip for most users. */}
      <p className="rounded-md bg-sky-50 p-3 text-sm text-sky-900">
        ID karta bo‘lsa, <strong>old va orqa tomonini</strong> birga yuklang —
        orqa tomondagi zonadan ma’lumotlar ancha aniq o‘qiladi.
      </p>

      {files.length > 0 && (
        <ul className="space-y-1 text-sm text-stone-700">
          {files.map((f) => (
            <li key={f.name}>{f.name} · {(f.size / 1024 / 1024).toFixed(1)} MB</li>
          ))}
        </ul>
      )}

      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      <button onClick={submit} disabled={!files.length || busy}
        className="rounded-lg bg-stone-900 px-5 py-2.5 text-white disabled:opacity-40">
        {busy ? "Yuborilmoqda…" : "Yuborish"}
      </button>
    </div>
  );
}
