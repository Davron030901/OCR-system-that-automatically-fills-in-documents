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

### `ocr-ml` uchun

| O'zgaruvchi | Qiymat |
|---|---|
| `INTERNAL_TOKEN` | API'dagi bilan **bir xil** |
| `OPENAI_API_KEY` | Pullik kalit |
| `GEMINI_PAID_KEYS` | Pullik Gemini kalitlari (bo'sh qoldirish mumkin) |
| `LLM_CACHE_KEY` | 32-baytli tasodifiy satr |

⚠️ **Bepul Gemini kalitlarini bu yerga yozmang.** Bepul tier ma'lumoti
model o'rgatish uchun ishlatiladi. PII darvozasi ularni rad etadi — ya'ni
ma'lumot chiqib ketmaydi, lekin so'rovlar xato beradi. To'g'ri yechim: prod'da
bepul kalit umuman bo'lmasin. Ular `training/` va eval uchun.

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

`training/01_classifier.ipynb` Colab T4 da `classifier.onnx` (~4 MB) chiqaradi.
Modelsiz ham tizim ishlaydi — u shunchaki tanilmagan rasmni oldindan rad eta
olmaydi.

Render'da doimiy disk `pserv` uchun mavjud, lekin eng oddiy yo'l — modelni
image ichiga qo'shish:

1. `classifier.onnx` va `labels.json` ni `models/` papkasiga qo'ying
2. `.gitignore` da `*.onnx` bor — bu bittasi uchun istisno qiling:

```gitignore
*.onnx
!models/classifier.onnx
```

3. `infra/Dockerfile.ml` ga qo'shing:

```dockerfile
COPY models/ ./models/
```

4. `ocr-ml` env: `CLASSIFIER_MODEL_PATH=/srv/models/classifier.onnx`
5. Notebook chop etgan ikki chegarani ham yozing:
   `CLASSIFIER_MIN_CONFIDENCE`, `CLASSIFIER_MAX_ENERGY`

⚠️ Chegaralar **modelga xos**. Modelni qayta o'rgatsangiz ularni ham qayta
hisoblang — eski chegaralar yangi model bilan ma'nosiz.

`/readyz` da `"classifier": true` chiqishi kerak.

---

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
| LLM (1000 hujjat) | — | ~$1-7 |

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
□ Bepul Gemini kalitlari prod'da YO'Q
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
