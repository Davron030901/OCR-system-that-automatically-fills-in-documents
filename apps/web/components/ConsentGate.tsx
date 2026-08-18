"use client";

import { useState } from "react";

/**
 * Consent must be specific to be meaningful. A single "I agree" checkbox over
 * a wall of text is not informed consent, so the two decisions that actually
 * matter — third-party processing and cross-border transfer — are separated
 * and the second is optional.
 */
export function ConsentGate({ onAccept }: { onAccept: (thirdParty: boolean) => void }) {
  const [core, setCore] = useState(false);
  const [thirdParty, setThirdParty] = useState(false);

  return (
    <div className="space-y-4 rounded-lg border border-stone-200 bg-white p-5">
      <h2 className="font-medium">Ma’lumotlarni qayta ishlashga rozilik</h2>

      <div className="space-y-2 text-sm text-stone-600">
        <p>Siz yuklaydigan hujjatda shaxsiy ma’lumotlar bo‘ladi: F.I.SH.,
          tug‘ilgan sana, JSHSHIR va hujjat raqami.</p>
        <ul className="list-inside list-disc space-y-1">
          <li>Rasm 24 soatdan keyin o‘chiriladi</li>
          <li>Chiqarilgan ma’lumotlar 30 kun shifrlangan holda saqlanadi</li>
          <li>Istalgan vaqtda “O‘chirish” tugmasi bilan hammasini o‘chirasiz</li>
        </ul>
      </div>

      <label className="flex gap-3 text-sm">
        <input type="checkbox" checked={core} className="mt-1"
          onChange={(e) => setCore(e.target.checked)} />
        <span>Hujjatimni o‘qib, ma’lumotlarni ajratib olishga roziman.</span>
      </label>

      <label className="flex gap-3 rounded-md bg-amber-50 p-3 text-sm">
        <input type="checkbox" checked={thirdParty} className="mt-1"
          onChange={(e) => setThirdParty(e.target.checked)} />
        <span>
          <strong>Ixtiyoriy.</strong> Aniqlikni oshirish uchun o‘qilgan
          <em> matn</em> tahlil qilish maqsadida uchinchi tomon xizmatiga
          (AQShda joylashgan) yuborilishiga roziman. Rasmning o‘zi
          yuborilmaydi. Rad etsangiz ham tizim ishlaydi, faqat ba’zi
          maydonlarni qo‘lda kiritishingiz kerak bo‘lishi mumkin.
        </span>
      </label>

      <button disabled={!core} onClick={() => onAccept(thirdParty)}
        className="rounded-lg bg-stone-900 px-5 py-2.5 text-white disabled:opacity-40">
        Davom etish
      </button>
    </div>
  );
}
