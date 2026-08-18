// Mirrors packages/schema/models.py. In CI these are regenerated from the
// backend's JSON Schema (`make types`) so the two cannot drift apart.

export type FieldSource =
  | "mrz" | "ocr_visual" | "llm_text" | "vlm" | "barcode" | "manual" | "derived";

export interface FieldValue {
  value: string | null;
  confidence: number;
  source: FieldSource;
  bbox: [number, number, number, number] | null;
  validated: boolean;
  alternatives: string[];
  note: string | null;
}

export interface PersonName {
  surname_latin: FieldValue;
  given_name_latin: FieldValue;
  patronymic_latin: FieldValue;
  surname_cyrillic: FieldValue;
  given_name_cyrillic: FieldValue;
  patronymic_cyrillic: FieldValue;
}

export interface Person {
  name: PersonName;
  birth_date: FieldValue;
  birth_place: FieldValue;
  sex: FieldValue;
  nationality: FieldValue;
  citizenship: FieldValue;
  pinfl: FieldValue;
  address: FieldValue;
  phone: FieldValue;
}

export interface IdentityDocument {
  doc_type: string;
  doc_number: FieldValue;
  doc_series: FieldValue;
  issuing_authority: FieldValue;
  issue_date: FieldValue;
  expiry_date: FieldValue;
}

export interface ExtractionResult {
  job_id: string;
  status: string;
  doc_type: string;
  person: Person;
  documents: IdentityDocument[];
  overall_confidence: number;
  needs_review: string[];
  warnings: string[];
  error_code: string | null;
  error_message: string | null;
  stages_used: string[];
  quality: { is_acceptable: boolean; reasons: string[] } | null;
}

export interface JobResponse {
  job_id: string;
  status: string;
  stage_label: string;
  doc_type: string | null;
  error_code: string | null;
  error_message: string | null;
  stages_used: string[];
  result?: ExtractionResult;
  needs_review?: string[];
  warnings?: string[];
}

export type ConfidenceBand = "confident" | "uncertain" | "doubtful";

export function band(f: FieldValue): ConfidenceBand {
  if (f.validated || f.confidence >= 0.9) return "confident";
  if (f.confidence >= 0.7) return "uncertain";
  return "doubtful";
}

/** Human-readable provenance. Users trust a check-digit-verified value more
 *  than a model guess, and they are right to, so we say which it is. */
export function sourceLabel(f: FieldValue): string {
  if (f.validated) return "Nazorat raqami bilan tasdiqlangan";
  switch (f.source) {
    case "mrz": return "Mashina o'qiydigan zonadan";
    case "ocr_visual": return "Hujjat yuzidan o'qildi";
    case "llm_text": return "Matn tahlilidan (tekshirilmagan)";
    case "vlm": return "Tasvir tahlilidan (tekshirilmagan)";
    case "manual": return "Siz kiritdingiz";
    case "derived": return "Hisoblab chiqarildi";
    default: return "";
  }
}
