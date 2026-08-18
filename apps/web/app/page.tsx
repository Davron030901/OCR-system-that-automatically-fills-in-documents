import Link from "next/link";

export default function Home() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Hujjatdan ma’lumot olish</h1>
      <p className="max-w-2xl text-stone-600">
        Pasport, ID karta yoki diplom suratini yuklang. Tizim ma’lumotlarni
        o‘qib, siz tanlagan hujjat shablonini to‘ldiradi.
      </p>
      <Link href="/upload"
        className="inline-block rounded-lg bg-stone-900 px-5 py-3 text-white">
        Boshlash
      </Link>
      <section className="rounded-lg border border-stone-200 bg-white p-4 text-sm text-stone-600">
        <h2 className="mb-2 font-medium text-stone-900">Ma’lumotlaringiz haqida</h2>
        <ul className="list-inside list-disc space-y-1">
          <li>Yuklangan rasm 24 soatdan keyin avtomatik o‘chiriladi</li>
          <li>Chiqarilgan ma’lumotlar shifrlangan holda saqlanadi</li>
          <li>Har bir maydonni yuklab olishdan oldin tekshirasiz</li>
        </ul>
      </section>
    </div>
  );
}
