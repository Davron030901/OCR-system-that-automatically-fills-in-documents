# Huquqiy masalalar

> Men yurist emasman. Bu hujjat yuristga beriladigan savollar ro'yxati,
> huquqiy xulosa emas.

## Asosiy muammo: ma'lumot lokalizatsiyasi

O'zbekiston Respublikasining "Shaxsga doir ma'lumotlar to'g'risida"gi
qonuni (2019, 2021-yilgi o'zgartirishlar bilan) O'zbekiston fuqarolarining
shaxsga doir ma'lumotlari mamlakat hududidagi serverlarda saqlanishini
talab qiladi.

Bu tizim uchun bu **ikki joyda** muammo:

1. **Hosting.** Render, Vercel, Supabase, Neon, Cloudflare R2 — hammasi
   chet elda. `infra/render.yaml` shuning uchun demo va test uchun
   belgilangan.
2. **LLM provayderlari.** L2 bosqichi OCR matnini OpenAI yoki Google'ga
   yuboradi. Rasm yuborilmaydi, lekin matnda ism, sana va JSHSHIR bo'ladi.

## Yuristga savollar

1. Foydalanuvchi roziligi lokalizatsiya talabini bekor qiladimi, yoki
   lokalizatsiya rozilikdan qat'i nazar majburiymi?
2. OCR matnini chet el xizmatiga tahlil uchun yuborish "uzatish" (transfer)
   hisoblanadimi, agar u saqlanmasa?
3. Bu xizmat uchun operator sifatida ro'yxatga olinish talab qilinadimi?
4. Rasm 24 soatdan keyin o'chirilishi yetarlimi, yoki umuman
   saqlanmasligi kerakmi?
5. Uchinchi tomon provayderi bilan qanday shartnoma (DPA) kerak?
6. Notarial yoki rasmiy hujjatlarni avtomatik to'ldirish uchun alohida
   litsenziya kerakmi?

## Tayyor migratsiya yo'li

Arxitektura bu muammoni oldindan hisobga olgan:

| Komponent | Chet el varianti | Mahalliy varianti |
|---|---|---|
| Hosting | Render + Vercel | `infra/onprem/` (Docker Compose) |
| LLM | OpenAI / Gemini | `LOCAL_VLM_URL` — Qwen2.5-VL o'z serveringizda |
| Storage | Cloudflare R2 | MinIO (compose'da allaqachon bor) |
| DB | Supabase / Neon | PostgreSQL konteyneri |

`packages/llm/providers/http_providers.py` dagi `LocalProvider` aynan shu
uchun yozilgan: `allows_real_pii=True`, ma'lumot infratuzilmangizdan
chiqmaydi. O'tish — konfiguratsiya o'zgarishi, kodni qayta yozish emas.

Lokal VLM uchun minimal talab: 16 GB VRAM (Qwen2.5-VL-7B, 4-bit).

## Rozilik matni

`components/ConsentGate.tsx` da ikki alohida qaror bor:

1. **Majburiy** — hujjatni o'qish va ma'lumot ajratib olish
2. **Ixtiyoriy** — matnni uchinchi tomon xizmatiga yuborish, davlati
   ko'rsatilgan holda

Ikkinchisini rad etgan foydalanuvchi uchun tizim ishlashda davom etadi,
faqat L2 bosqichisiz. Bu texnik jihatdan ta'minlangan: pipeline har bir
bosqichsiz ishlay oladi.

Umumiy "biz uchinchi tomonlardan foydalanamiz" degan qator yetarli emas —
qaysi xizmat, qaysi davlatda, qanday ma'lumot aytilishi kerak.
