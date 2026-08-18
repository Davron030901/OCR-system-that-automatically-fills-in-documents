"use client";

import clsx from "clsx";

import { band, sourceLabel, type FieldValue } from "@/lib/types";

const LABELS: Record<string, string> = {
  "person.name.surname_latin": "Familiya",
  "person.name.given_name_latin": "Ism",
  "person.name.patronymic_latin": "Otasining ismi",
  "person.birth_date": "Tug‘ilgan sana",
  "person.birth_place": "Tug‘ilgan joy",
  "person.sex": "Jinsi",
  "person.pinfl": "JSHSHIR",
  "person.address": "Manzil",
  "person.nationality": "Millati",
  "person.citizenship": "Fuqaroligi",
  "documents.0.doc_number": "Hujjat raqami",
  "documents.0.issue_date": "Berilgan sana",
  "documents.0.expiry_date": "Amal qilish muddati",
  "documents.0.issuing_authority": "Kim tomonidan berilgan",
};

const BAND_STYLE = {
  confident: "border-l-4 border-l-confident",
  uncertain: "border-l-4 border-l-uncertain bg-amber-50/40",
  doubtful: "border-l-4 border-l-doubtful bg-red-50/40",
} as const;

export function FieldRow({
  path, field, onChange, onFocus, focused,
}: {
  path: string;
  field: FieldValue;
  onChange: (v: string) => void;
  onFocus: () => void;
  focused: boolean;
}) {
  const b = band(field);

  return (
    <div className={clsx("rounded-md bg-white p-3", BAND_STYLE[b],
      focused && "ring-2 ring-stone-900")}>
      <div className="flex items-baseline justify-between gap-2">
        <label className="text-sm font-medium">{LABELS[path] ?? path}</label>
        <span className="text-[11px] text-stone-500">{sourceLabel(field)}</span>
      </div>

      <input
        value={field.value ?? ""}
        onFocus={onFocus}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1.5 w-full rounded border border-stone-300 px-2 py-1.5 text-sm"
      />

      {/* When the recogniser could not decide between candidates, offering the
          alternatives is far more useful than making the user retype. */}
      {field.alternatives.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <span className="text-xs text-stone-500">Variantlar:</span>
          {field.alternatives.map((alt) => (
            <button key={alt} onClick={() => onChange(alt)}
              className="rounded border border-stone-300 px-2 py-0.5 text-xs hover:bg-stone-100">
              {alt}
            </button>
          ))}
        </div>
      )}

      {field.note && <p className="mt-1 text-xs text-stone-500">{field.note}</p>}
    </div>
  );
}
