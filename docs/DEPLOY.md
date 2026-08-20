# Deploy: Render (backend) + Vercel (frontend)

> ⚠️ **Avval `docs/LEGAL.md` ni o'qing.** Render'ning Frankfurt/Singapur
> regionlari O'zbekiston lokalizatsiya talabini **qondirmaydi**. Bu qo'llanma
> demo, test va investorga ko'rsatish uchun. Real fuqarolar ma'lumoti bilan
> ishlaydigan prod uchun O'zbekistondagi hosting yoki on-prem kerak.

Nima qayerda ishlaydi:

```
Vercel                 Render (Frankfurt)                Tashqi
──────                 ──────────────────                ──────
apps/web    ──HTTPS──▶ ocr-api        (web, ochiq)  ──▶  Neon / Supabase (Postgres)
                          │                          ──▶  Cloudflare R2 (fayllar)
                          ├─private──▶ ocr-ml       (pserv)  ──▶ OpenAI / Gemini
                          ├─private──▶ ocr-converter (pserv, ixtiyoriy)
                          └─private──▶ ocr-keyvalue (Redis-mos)
```

**Faqat `ocr-api` internetdan ochiq.** Qolgan uchtasi Render'ning ichki
tarmog'ida (`pserv`), tashqaridan umuman ko'rinmaydi, va ustiga har so'rovda
`INTERNAL_TOKEN` tekshiriladi.

**LLM kalitlari `ocr-ml` da, `ocr-api` da emas.** `packages/llm` aynan shu
servis ichida ishlaydi — API hech qachon model provayderiga murojaat
qilmaydi. Kalitni API'ga qo'yish uni keraksiz joyda ochib qo'yish demak.

---

## Demo rejim: MVP uchun sozlama

MVP bepul Gemini kalitlari bilan ishlaydi. Buning uchun `DEMO_MODE=true` —
`ocr-api` va `ocr-ml` da **ikkalasida ham**.

### Demo rejim nima qiladi

| | Demo (`true`) | Prod (`false`) |
|---|---|---|
| LLM provayder tartibi | `gemini-free` birinchi | `openai` birinchi, bepul umuman yo'q |
| So'rov belgisi | `contains_real_pii=False` | `contains_real_pii=True` |
| UI | Sariq ogohlantirish banneri | Banner yo'q |
| Real pasport yuklansa | **Rad etiladi** (pastda) | Normal ishlanadi |

### ⭐ Nima uchun bu xavfsiz

Demo rejim PII darvozasini **o'chirmaydi**. Mexanizm teskari ishlaydi:

1. Demo rejim so'rovni "sintetik" deb belgilaydi.
2. `PIIGate` esa **haqiqiy yukni** tekshiradi. Matnda 14 xonali JSHSHIR,
   `AA1234567` ko'rinishidagi hujjat raqami yoki MRZ qatori topilsa —
   so'rov **rad etiladi**.

Ya'ni foydalanuvchi ogohlantirishga qaramay real pasport yuklasa, ma'lumot
bepul tier'ga **ketmaydi**. So'rov xato beradi, kaskad lokal bosqichlar bilan
davom etadi, natija ko'rib chiqishga belgilanadi.

**Demo rejimning buzilish usuli — rad etish, oshkor qilish emas.** Bu xossa
darvozadan keladi (uni o'chirish imkoni yo'q), demo modulidan emas.

Muhimi: L0 (MRZ) va L1 (qoidalar) **lokal** ishlaydi va hech qanday
provayderga tegmaydi. Demo pasportning mashina o'qiydigan zonasini pullik
kalitsiz ham mukammal o'qiydi. Faqat vizual zonani semantik xaritalash (L2)
real hujjat uchun demo rejimida mavjud emas.

Rasm yuborish (L3) demo rejimida umuman ishlamaydi: darvoza har qanday
hujjat rasmini shartsiz real shaxsiy ma'lumot deb hisoblaydi.

### Prod'ga o'tish

```
DEMO_MODE=false
OPENAI_API_KEY=<pullik kalit>
GEMINI_FREE_KEYS=            # bo'shatiladi
```

Uchta o'zgaruvchi, kod o'zgarmaydi.

Testlar: `packages/llm/tests/test_demo_mode.py` — 21 ta test, jumladan
"demo rejimida real pasport rad etiladi".

---

## 0. Deploy'dan oldin: kalitlarni almashtiring

Agar `.env.example` haqiqiy kalitlar bilan git'ga tushgan bo'lsa (bu loyihada
shunday bo'lgan), prod'ga chiqishdan **oldin** hammasini bekor qilib, yangisini
oling. Tarixni tozalash yetarli emas — kalit allaqachon ochilgan.

```bash
git log --oneline -- .env.example      # tekshiring
```

Yangi maxfiy so'zlar:

```bash
make keys      # ENCRYPTION_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"   # JWT_SECRET
python -c "import secrets; print(secrets.token_urlsafe(32))"   # INTERNAL_TOKEN
python -c "import secrets; print(secrets.token_urlsafe(32))"   # LLM_CACHE_KEY
```

⚠️ `ENCRYPTION_KEY` — chiqarilgan pasport ma'lumoti shu kalit bilan
shifrlanadi. **Uni yo'qotsangiz bazadagi hamma narsa o'qilmaydi.** Parol
menejerida saqlang. Prod'da kalitsiz API ishga tushishdan bosh tortadi
(`main.py` dagi `lifespan`), bu ataylab shunday.

---

## 1. Tashqi xizmatlar (Render'dan oldin)

Render'ning o'z bepul Postgres'i 30 kundan keyin **o'chib ketadi** va
shifrlangan ekstraksiyalarni ham olib ketadi. Shuning uchun baza tashqarida.

### Postgres — Neon yoki Supabase

Ulanish satri **asyncpg** drayveri bilan bo'lishi shart:

```
postgresql+asyncpg://user:parol@host/dbname
```

Ko'pchilik provayder `postgresql://...` beradi — `postgresql+asyncpg://` ga
o'zgartiring, aks holda API `InvalidRequestError` bilan yiqiladi.

Supabase pooler ishlatayotgan bo'lsangiz `?pgbouncer=true` emas, **direct
connection** satrini oling: SQLAlchemy'ning async pool'i bilan transaction
pooler mos kelmaydi.

Migratsiya kerak emas — `app/db/bootstrap.py` ishga tushishda jadvallarni
yaratadi.

### Obyekt saqlash — Cloudflare R2 yoki Backblaze B2

R2 misolida:

1. R2 → Create bucket → nomi `ocr-uploads`
2. Manage R2 API Tokens → Create API token → **Object Read & Write**
3. Yozib oling:

```
STORAGE_ENDPOINT=https://<account_id>.r2.cloudflarestorage.com
STORAGE_BUCKET=ocr-uploads
STORAGE_ACCESS_KEY=<access key id>
STORAGE_SECRET_KEY=<secret access key>
```

Bucket **public bo'lmasin**. Fayllar imzolangan URL orqali beriladi
(15 daqiqa TTL). Public bucket — pasport skanini internetga qo'yish demak.

R2 lifecycle qoidasini qo'shing: `uploads/` prefiksi uchun 1 kun. Kod ham
retention'ni bajaradi (`app/services/retention.py`), lekin storage darajasidagi
qoida — ikkinchi mudofaa chizig'i.

---

## 2. Render: blueprint bilan deploy

`infra/render.yaml` — to'rtala servisni bir vaqtda yaratadigan blueprint.

1. Render Dashboard → **New** → **Blueprint**
2. GitHub repo'ni ulang
3. Blueprint fayli: `infra/render.yaml`
4. Render `sync: false` bo'lgan har bir o'zgaruvchini so'raydi — quyidagi
   jadvalga qarab to'ldiring

### `ocr-api` uchun kiritiladigan qiymatlar

| O'zgaruvchi | Qiymat |
|---|---|
| `ENCRYPTION_KEY` | `make keys` chiqargani |
| `JWT_SECRET` | 32-baytli tasodifiy satr |
| `INTERNAL_TOKEN` | 32-baytli tasodifiy satr — **`ocr-ml` va `ocr-converter` da aynan shu qiymat** |
| `DATABASE_URL` | `postgresql+asyncpg://...` |
| `STORAGE_ENDPOINT` / `STORAGE_BUCKET` / `STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY` | R2 dan |
| `CORS_ORIGINS` | Hozircha `https://example.vercel.app` — 4-bo'limda haqiqiysiga almashtiriladi |
| `DEMO_MODE` | `true` (blueprint'da tayyor). UI banneri shundan chiqadi |

### `ocr-ml` uchun

| O'zgaruvchi | Qiymat |
|---|---|
| `INTERNAL_TOKEN` | API'dagi bilan **bir xil** |
| `GEMINI_FREE_KEYS` | Bepul kalitlar, vergul bilan — **MVP da asosiysi** |
| `OPENAI_API_KEY` | Pullik kalit. Demo rejimida ixtiyoriy, bo'sh qoldiring |
| `GEMINI_PAID_KEYS` | Bo'sh. Prod'ga o'tganda to'ldiriladi |
| `LLM_CACHE_KEY` | 32-baytli tasodifiy satr |

⚠️ Bepul kalitlar bu yerda **faqat `DEMO_MODE=true` bo'lgani uchun**
ishlaydi. `DEMO_MODE=false` qilsangiz PII darvozasi ularni rad etadi va
so'rovlar xato beradi — bu himoya, nosozlik emas. Prod'ga o'tayotganda
`GEMINI_FREE_KEYS` ni bo'shatib, `OPENAI_API_KEY` ni to'ldiring.

⚠️ Bir loyihadagi 10 ta bepul kalit **bitta kvotani bo'lishadi** (kvota
loyiha darajasida hisoblanadi). Ular orasida aylanish hech narsa bermaydi.
Demo uchun bittasi ham yetarli.

### `ocr-converter` uchun

| O'zgaruvchi | Qiymat |
|---|---|
| `INTERNAL_TOKEN` | API'dagi bilan bir xil |

**Bu servis ixtiyoriy.** U faqat ikki narsa uchun kerak: eski `.doc`
shablonini `.docx` ga o'girish va PDF chiqarish. Agar shablonlaringiz
`.docx` bo'lsa va PDF kerak bo'lmasa:

- blueprint'dan `ocr-converter` blokini va `ocr-api` dagi `CONVERTER_URL`
  havolasini o'chiring
- `ENABLE_PDF_OUTPUT=false` qiling

Bu ~700 MB image va oyiga bitta instance narxini tejaydi. Tizim to'liq
ishlaydi — `ENABLE_PDF_OUTPUT=false` bilan barcha testlar o'tadi.

### Birinchi build haqida

- `ocr-ml` build'i eng uzun: onnxruntime va opencv ~4-6 daqiqa
- `ocr-converter` bundan ham uzun: LibreOffice ~5-8 daqiqa
- Docker konteksti repo ildizi (`rootDir` berilmagan), shuning uchun
  Dockerfile'lardagi `COPY packages ./packages` ishlaydi

### Sog'liqni tekshirish

```bash
curl https://ocr-api-XXXX.onrender.com/healthz
# {"status":"ok"}
```

`ocr-ml` tashqaridan ochiq emas, shuning uchun uni Render'ning **Shell**
tabidan tekshiring:

```bash
curl -s http://ocr-ml:8100/readyz
# {"status":"ready","llm_enabled":true,"l3_vision":false,
#  "ocr":true,"classifier":false}
```

`"classifier": false` — normal. Model hali yo'q; 5-bo'limga qarang.

---

## 3. Vercel: frontend

Monorepo bo'lgani uchun **Root Directory** ni to'g'ri belgilash eng muhim
qadam. Uni noto'g'ri qoldirsangiz Vercel repo ildizida `package.json`
topolmaydi.

1. Vercel → **Add New** → **Project** → repo'ni tanlang
2. **Root Directory** → `apps/web`  ← ⭐ shu yerda adashish oson
3. Framework Preset: **Next.js** (avtomatik aniqlanadi)
4. **Environment Variables** → qo'shing:

```
NEXT_PUBLIC_API_URL = https://ocr-api-XXXX.onrender.com
```

Uch muhitga ham (Production / Preview / Development) qo'ying.

5. **Deploy**

### `NEXT_PUBLIC_` haqida

Bu o'zgaruvchi **build paytida** JS bundle'iga yoziladi va brauzerda
ko'rinadi. Shuning uchun:

- u yerga hech qanday maxfiy narsa qo'ymang — faqat public API manzili
- qiymatini o'zgartirsangiz **qayta deploy qilish shart**, Vercel'da
  o'zgaruvchini yangilash o'zi yetarli emas

`vercel.json` da `env` bloki **yo'q** va bo'lmasligi kerak: Vercel'ning o'zi
bu usuldan voz kechishni tavsiya qiladi, va u `NEXT_PUBLIC_*` uchun baribir
ishlamaydi (u runtime funksiyalari uchun, build uchun emas). Loyihadagi
`vercel.json` faqat framework, build buyrug'i va xavfsizlik header'larini
belgilaydi.

---

## 4. CORS: ikki tomonni ulash

Vercel domenini olganingizdan keyin Render'ga qayting:

`ocr-api` → **Environment** → `CORS_ORIGINS`:

```
https://sizning-loyihangiz.vercel.app
```

Bir nechta domen bo'lsa vergul bilan:

```
https://sizning-loyihangiz.vercel.app,https://hujjat.uz
```

Saqlang → Render avtomatik qayta deploy qiladi.

⚠️ Preview deploy'lar har safar yangi domen oladi
(`loyiha-git-branch-team.vercel.app`). Ular CORS ro'yxatida bo'lmaydi va
brauzerda xato beradi. Bu normal — preview'ni test qilish uchun o'sha domenni
vaqtincha qo'shing yoki `vercel.app` subdomenlariga ruxsat bering.

### Tekshirish

Brauzerda frontend'ni oching, rasm yuklang. Xato bo'lsa **Network** tabiga
qarang:

| Belgi | Sabab |
|---|---|
| `CORS policy: No 'Access-Control-Allow-Origin'` | `CORS_ORIGINS` noto'g'ri yoki eski |
| `Failed to fetch`, so'rov umuman ketmagan | `NEXT_PUBLIC_API_URL` xato yoki eski build |
| 502 / 30 soniya kutish | API cold start (starter plan) yoki `ocr-ml` yiqilgan |
| 401 `unauthorized` | `INTERNAL_TOKEN` servislar orasida mos emas |

---

## 5. Klassifikator modelini qo'shish (ixtiyoriy)

Model **repo ichida** saqlanadi. Lokal kompyuterda training yo'q, Hugging Face
yo'q, model registry yo'q:

```
Colab T4  ──▶  yuklab olish  ──▶  git commit  ──▶  Render image
```

### Qadamlar

1. `training/01_classifier.ipynb` ni Colab'da (Runtime → T4 GPU) oching va
   oxirigacha ishlating. ~25-35 daqiqa.
2. Oxirgi katak uchta faylni brauzeringizga yuklab beradi:
   `classifier.onnx` (~4 MB), `labels.json`, `MODEL_CARD.md`.
3. Ularni repo'ning `models/` papkasiga qo'ying va commit qiling:

```bash
git add models/classifier.onnx models/labels.json models/MODEL_CARD.md
git commit -m "classifier: v1"
git push
```

4. Notebook chop etgan ikki chegarani Render'da `ocr-ml` ga yozing:

```
CLASSIFIER_MIN_CONFIDENCE=0.612
CLASSIFIER_MAX_ENERGY=-3.104
```

`CLASSIFIER_MODEL_PATH` allaqachon `/srv/models/classifier.onnx` ga
qo'yilgan — `infra/Dockerfile.ml` `models/` ni image ichiga ko'chiradi.

5. Deploy tugagach: `/readyz` → `"classifier": true`.

### Nima uchun repo ichida

4 MB — kod bilan birga versiyalash uchun yetarlicha kichik, va bu uch narsani
beradi: deploy `git push` ga tenglashadi; model bilan uni kalibrlagan
chegaralar bir joyda turadi (alohida bo'lsa jimgina bir-biriga mos kelmay
qoladi); rollback esa oddiy commit'ga qaytish bo'ladi.

`.gitignore` da `*.onnx` bloklangan, lekin `!models/classifier.onnx`
istisnosi bor — tasodifiy checkpoint'lar tushmaydi.

Model 20 MB dan oshsa bu qarorni qayta ko'rib chiqing.

### ⚠️ Chegaralar modelga xos

Ular validatsiya taqsimotidan olinadi. Modelni qayta o'rgatsangiz —
ikkalasini ham qayta yozing. Eski chegara yangi model bilan ma'nosiz.

### Modelsiz ham ishlaydi

`models/classifier.onnx` bo'lmasa fayl topilmaydi, klassifikator o'chadi va
tizim ishlashda davom etadi: MRZ va qoidalar lokal, LLM hujjat turini
kontekstdan tushunadi. MVP'ni modelsiz ishga tushirish **normal** — bu bosqich
ixtiyoriy deb belgilangani shuning uchun.

## 6. Narx

| Element | Plan | Oyiga |
|---|---|---|
| ocr-api | Starter | ~$7 |
| ocr-ml | Starter | ~$7 |
| ocr-converter | Starter | ~$7 (kerak bo'lmasa 0) |
| ocr-keyvalue | Starter | ~$10 |
| Neon / Supabase | Free | $0 |
| Cloudflare R2 | 10 GB gacha | $0 |
| Vercel | Hobby | $0 |
| LLM — demo (bepul Gemini) | — | $0 |
| LLM — prod (1000 hujjat) | — | ~$1-7 |

Jami taxminan **$25-35/oy**, converter'siz **$18-28/oy**.

Bepul tier'da ishlatib bo'lmaydimi? `ocr-api` ni `free` ga tushirish mumkin,
lekin 512 MB va 15 daqiqadan keyin uxlab qolish demakdir: birinchi so'rov
~60 soniya kutadi. Demo uchun ham yomon taassurot qoldiradi.

`ocr-ml` bepul tier'da **ishlamaydi** — onnxruntime va opencv 512 MB ga
sig'maydi.

---

## 7. Deploy'dan keyingi ro'yxat

```
□ /healthz javob beradi
□ /readyz da "ocr": true
□ Rasm yuklab, natija olindi (uchidan uchiga)
□ Shablon yuklandi, DOCX chiqdi
□ CORS ishlaydi (brauzer konsolida xato yo'q)
□ ENCRYPTION_KEY parol menejerida saqlandi
□ DEMO_MODE ikkala servisda ham bir xil (api va ml)
□ Demo bo'lsa: UI'da sariq ogohlantirish banneri ko'rinadi
□ Prod'ga o'tganda: DEMO_MODE=false va bepul Gemini kalitlari o'chirilgan
□ R2 bucket public EMAS
□ R2 lifecycle qoidasi yoqilgan (uploads/ → 1 kun)
□ Render log'larida shaxsiy ma'lumot ko'rinmaydi
□ docs/LEGAL.md dagi savollar yuristga berildi
```

Oxirgi ikki punktni jiddiy qabul qiling. Log'larni bir marta qo'lda ko'rib
chiqing: `packages` ichidagi redaction filtri PII ni to'sadi, lekin uchinchi
tomon kutubxonasi (masalan `httpx` yoki `asyncpg`) chiqargan xato matni shu
filtrsiz o'tishi mumkin.

---

## 8. Nima ishlamayapti?

### `ModuleNotFoundError: No module named 'packages'`

Docker konteksti noto'g'ri. `render.yaml` da `rootDir` **belgilanmasin** —
kontekst repo ildizi bo'lishi kerak, chunki Dockerfile `COPY packages ./packages`
qiladi.

### `ocr-api` ishga tushmaydi, log'da `ENCRYPTION_KEY must be set`

Bu himoya, xato emas. `ENVIRONMENT=production` da kalitsiz ishga tushmaydi.

### `sqlalchemy.exc.InvalidRequestError` / `The asyncio extension requires an async driver`

`DATABASE_URL` da `postgresql://` turibdi. `postgresql+asyncpg://` bo'lsin.

### ml-service log'ida `local OCR unavailable`

PaddleOCR o'rnatilmagan — bu kutilgan holat. Tizim MRZ + LLM bilan davom
etadi. To'liq OCR uchun `requirements/ml.txt` ga OCR dvigatelini qo'shing.

### Render build 15 daqiqadan oshdi va to'xtadi

Odatda `ocr-converter` (LibreOffice). Agar PDF kerak bo'lmasa, uni umuman
deploy qilmang — 2-bo'limga qarang.

### Vercel: `No Next.js version detected`

**Root Directory** `apps/web` ga qo'yilmagan.

Qolgan muammolar: `TROUBLESHOOTING.md`.

---

## 9. Prod uchun keyingi qadam

Bu qo'llanma **demo arxitekturasini** tasvirlaydi. Real foydalanuvchilar
ma'lumoti bilan ishlash uchun uchta narsa o'zgaradi:

1. **Hosting O'zbekistonda** — `infra/onprem/` yoki mahalliy VPS.
   `docker-compose.yml` shu holicha ishlaydi.
2. **LLM lokal** — `LOCAL_VLM_URL` ni o'z serveringizdagi Qwen2.5-VL ga
   yo'naltiring. `packages/llm` aynan shuning uchun provayderdan mustaqil:
   o'tish konfiguratsiya o'zgarishi, kod qayta yozilmaydi. Minimal 16 GB VRAM.
3. **Rozilik matni** — `docs/LEGAL.md` dagi ro'yxat bo'yicha, uchinchi tomonga
   uzatish aniq nomlangan holda.
