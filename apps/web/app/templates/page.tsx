"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { generateDocument, listTemplates } from "@/lib/api";

type Template = {
  id: string;
  name: string;
  required_fields: string[];
  // Values no document can supply — a salary, a job title, a contract number.
  // The analyser collects these from `extra.*` placeholders so we can ask for
  // them here, rather than shipping a contract with a blank line where the
  // salary belongs.
  extra_fields: string[];
  output_formats: string[];
};

// Field names come from whoever authored the template, so an unrecognised one
// falls back to its raw name rather than rendering an empty label.
const LABELS: Record<string, string> = {
  salary: "Oylik ish haqi (so'm)",
  position: "Lavozim",
  employer: "Ish beruvchi",
  contract_number: "Shartnoma raqami",
  start_date: "Ish boshlash sanasi",
  reference_number: "Ma'lumotnoma raqami",
  purpose: "Qayerga taqdim etiladi",
  director: "Rahbar F.I.Sh.",
  university: "Oliy ta'lim muassasasi",
  speciality: "Ta'lim yo'nalishi",
  study_form: "Ta'lim shakli",
  city: "Shahar",
  attorney_name: "Ishonchli shaxs F.I.Sh.",
  powers: "Berilayotgan huquqlar",
  valid_until: "Amal qilish muddati",
};

function label(name: string): string {
  return LABELS[name] ?? name.replace(/_/g, " ");
}

function TemplatePicker() {
  const jobId = useSearchParams().get("job") ?? "";
  const [templates, setTemplates] = useState<Template[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [extra, setExtra] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ url: string; missing: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { listTemplates().then(setTemplates).catch(() => setTemplates([])); }, []);

  async function generate(t: Template, format: string) {
    setBusy(t.id); setError(null);
    try {
      const doc = await generateDocument(jobId, t.id, format, extra);
      setResult({ url: doc.download_url, missing: doc.missing_fields });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Hujjat yaratilmadi");
    } finally { setBusy(null); }
  }

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold">Shablonni tanlang</h1>

      {templates.length === 0 && (
        <p className="rounded-md bg-stone-100 p-4 text-sm text-stone-600">
          Hali shablon yuklanmagan. Word (.docx) faylini yuklang — ichida
          <code className="mx-1 rounded bg-white px-1">{"{{ person.pinfl }}"}</code>
          kabi belgilar bo‘lsin.
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {templates.map((t) => {
          const manual = t.extra_fields ?? [];
          const open = openId === t.id;
          return (
            <div key={t.id} className="rounded-lg border border-stone-200 bg-white p-4">
              <h2 className="font-medium">{t.name}</h2>
              <p className="mt-1 text-sm text-stone-500">
                {t.required_fields.length} ta maydon hujjatdan olinadi
                {manual.length > 0 && `, ${manual.length} tasini siz kiritasiz`}
              </p>

              {manual.length > 0 && (
                <button
                  onClick={() => setOpenId(open ? null : t.id)}
                  className="mt-2 text-sm text-stone-700 underline underline-offset-2">
                  {open ? "Yashirish" : "Qo‘lda kiritiladigan maydonlar"}
                </button>
              )}

              {manual.length > 0 && open && (
                <div className="mt-3 space-y-2 border-t border-stone-100 pt-3">
                  {manual.map((name) => (
                    <label key={name} className="block text-sm">
                      <span className="text-stone-600">{label(name)}</span>
                      <input
                        type="text"
                        value={extra[name] ?? ""}
                        onChange={(e) =>
                          setExtra({ ...extra, [name]: e.target.value })}
                        className="mt-1 w-full rounded border border-stone-300 px-2 py-1.5"
                      />
                    </label>
                  ))}
                  <p className="text-xs text-stone-500">
                    Bo‘sh qoldirilgan maydon hujjatda chiziq bilan chiqadi.
                  </p>
                </div>
              )}

              <div className="mt-3 flex gap-2">
                {t.output_formats.map((f) => (
                  <button key={f} disabled={busy === t.id}
                    onClick={() => generate(t, f)}
                    className="rounded border border-stone-300 px-3 py-1.5 text-sm hover:bg-stone-50 disabled:opacity-40">
                    {busy === t.id ? "…" : f.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      {result && (
        <div className="space-y-3 rounded-lg border border-stone-200 bg-white p-4">
          {result.missing.length > 0 && (
            <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">
              Quyidagi maydonlar bo‘sh qoldi va hujjatda chiziq bilan
              ko‘rsatilgan: {result.missing.join(", ")}
            </p>
          )}
          <a href={result.url}
            className="inline-block rounded-lg bg-stone-900 px-5 py-2.5 text-white">
            Hujjatni yuklab olish
          </a>
        </div>
      )}
    </div>
  );
}

export default function TemplatesPage() {
  return <Suspense><TemplatePicker /></Suspense>;
}
