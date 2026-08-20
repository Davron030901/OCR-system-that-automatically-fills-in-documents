# Hujjat OCR va avtomatik to'ldirish tizimi

Pasport, ID karta va diplom rasmidan ma'lumot chiqarib, Word shablonlarini
avtomatik to'ldiradigan tizim.

```
Rasm  →  Preprocessing  →  L0 MRZ  →  L1 Qoidalar  →  L2 LLM(matn)  →  L3 VLM(rasm)
                                ↓
                    Kanonik sxema (Person / Education)
                                ↓
                    Word shabloni (Jinja2)  →  DOCX / PDF
```

## Loyihaning asosiy qarorlari

**MRZ birinchi, model keyin.** Pasport va ID kartadagi mashina o'qiydigan zona
ICAO 9303 standarti bo'yicha check digit bilan himoyalangan. Bu OCR xatosini
nafaqat *aniqlash*, ko'pincha *tuzatish* imkonini beradi. Shu sabab hujjat
raqami, sanalar, jins, fuqarolik va JSHSHIR arifmetik tasdiq bilan olinadi —
hech qanday model bunga yeta olmaydi.

**LLM rasmni emas, matnni ko'radi.** L2 bosqichi lokal OCR chiqargan
*matnni* yuboradi. Uch foyda: hujjat rasmi serverdan chiqmaydi, token soni
~5 barobar kamayadi, va tekshiruv arzimas darajada oson bo'ladi — model
qaytargan har bir qiymat o'sha matnda mavjud bo'lishi shart.

**Hech narsa taxmin qilinmaydi.** O'qilmagan maydon `None` bo'ladi va ko'rib
chiqishga yuboriladi. Bo'sh maydon noto'g'ri maydondan doim yaxshiroq.

## Isbotlangan xavfsizlik xususiyatlari

Bular hujjatdagi va'da emas, testlar bilan ta'minlangan invariantlar:

| Xususiyat | Qayerda tekshiriladi |
|---|---|
| Tasdiqlangan deb belgilangan qiymat hech qachon xato emas | `test_mrz.py::TestNeverSilentlyWrong` |
| Bepul LLM tier'ga real shaxsiy ma'lumot ketmaydi | `test_llm_layer.py::TestPIIGate` |
| Model o'ylab topgan qiymat rad etiladi | `test_llm_layer.py::TestGrounding` |
| Shablon orqali kod bajarilmaydi (SSTI) | `test_docgen.py::TestSandboxSSTI` |
| Log'larda shaxsiy ma'lumot qolmaydi | `test_security.py::TestRedaction` |
| Shifrlanmagan PII bazaga yozilmaydi | `test_security.py::TestCrypto` |
| Keshdagi model javobi shifrlangan, tenant bo'yicha ajratilgan | `test_cache.py` |
| Tanilmagan rasm rad etiladi, taxmin qilinmaydi | `test_classify.py` |
| Har bir servis import bo'ladi (route xatosi build'gacha yetmaydi) | `tests/test_service_startup.py` |
| Eval to'plamidagi har bir kutilgan qiymat kirish matnida bor | `test_prompts.py` |

Bularning har biri CI'da alohida darvoza sifatida ishlaydi.

### Nima uchun "tasdiqlash" alohida ehtiyot talab qiladi

Mod-10 check digit bitta o'nlik raqamni tashiydi, ya'ni tasodifiy
almashtirishlarning ~10% i tasodifan to'g'ri check digit beradi. Boshlang'ich
tuzatish algoritmi shu sabab **360 holatdan 179 tasida xato qiymatni
"tasdiqlangan"** deb belgilagan edi.

Hozirgi qoida: tahrir isbot emas. Faqat quyidagilar tasdiqlanadi:

- o'qilgan qiymatning o'zi check digit'ga mos (`edits == 0`), yoki
- tahrirni mustaqil dalil tasdiqlagan: OCR o'sha pozitsiyani ishonchsiz deb
  belgilagan, yoki bir necha maydonni qamrab oluvchi composite check digit
  aynan shu kombinatsiyani tasdiqlagan

Tasdiqlanmagan tuzatishda foydalanuvchiga **hujjatda yozilgan qiymat**
ko'rsatiladi, taklif esa `alternatives` sifatida beriladi. Jimgina
almashtirilgan hujjat raqami — ishonarli ko'rinadi va shuning uchun
tekshirishdan o'tib ketadi.

## Ishga tushirish

```bash
make env      # .env yaratadi va maxfiy so'zlarni generatsiya qiladi
make dev      # api, ml-service, db, storage, web
make test     # virtualenv yaratib, 276 testni ishga tushiradi
```

`make test` birinchi navbatda `tests/` ni ishga tushiradi — u har uchala
FastAPI ilovasini import qiladi. Faqat import paytida buziladigan route xatosi
(masalan `status_code=204` bilan `-> None` annotatsiyasi) shu yerda ikki
soniyada ko'rinadi, to'rt daqiqalik `docker compose up` dan keyin emas.

`make dev` LibreOffice'ni qurmaydi — u ~400 MB va build'ni bir necha daqiqaga
cho'zadi. U faqat `.doc` shablonlarni o'girish va PDF chiqishi uchun kerak:

```bash
make dev-full     # converter bilan birga
```

`make env` uchta maxfiy so'zni generatsiya qiladi (`ENCRYPTION_KEY`,
`JWT_SECRET`, `INTERNAL_TOKEN`) va `.env` ichiga **o'rniga yozadi** — oxiriga
qo'shmaydi. Ikki marta ishga tushirsangiz mavjudlariga tegmaydi.

Birinchi `make dev` 5-10 daqiqa oladi (LibreOffice ~400 MB, npm install).
Keyingi safar layer cache tufayli tez bo'ladi.

Muammo chiqsa: **`TROUBLESHOOTING.md`**

Frontend `localhost:3000`, API `localhost:8000/docs`.

## Struktura

```
packages/schema     kanonik modellar, MRZ validatorlari, transliteratsiya
packages/ml         preprocessing, MRZ, qoidalar, kaskad orkestratori
packages/ml/classify  ONNX hujjat turi klassifikatori (OOD rad etish bilan)
packages/llm        provayder abstraksiyasi, PII darvozasi, grounding
packages/llm/prompts  versiyalangan promptlar + eval to'plamlari
packages/docgen     run coalescing, sanitizatsiya, Word render
packages/docgen/templates  6 ta tayyor shablon (skript generatsiya qiladi)
apps/api            FastAPI backend
apps/ml-service     ONNX inference (PyTorch yo'q)
apps/converter      LibreOffice mikroservisi
apps/web            Next.js
scripts/            generatorlar (shablonlar, notebook, .env)
training/           Colab T4 notebook'lari (sintetik dataset, klassifikator)
eval/               golden set va baholash
```

## Model o'rgatish (Colab T4)

Loyihada **bitta** majburiy training bor — hujjat turi klassifikatori.
Qolgan hammasi (MRZ, qoidalar, LLM xaritalash) o'rgatilgan modelsiz ishlaydi.

| Notebook | Nima | Vaqt | T4 shartmi |
|---|---|---|---|
| `training/00_synthetic_data.ipynb` | Sintetik dataset (20k rasm) | ~40 daq | 🟡 tezroq |
| `training/01_classifier.ipynb` | Klassifikator + ONNX int8 eksport | ~30 daq | 🟡 CPU'da ~2 soat |

Klassifikatorning asosiy vazifasi — **rad etish**. Chek rasmini yuklagan
foydalanuvchi "pasportingiz o'qildi" degan javob olmasligi kerak. Buning
uchun ikki mustaqil tekshiruv ishlaydi: softmax pastki chegarasi va erkin
energiya (`-logsumexp(logits)`). Ikkinchisi kerak, chunki softmax har doim
1 ga yig'iladi — mushuk rasmini ko'rgan model ham 94% ishonch ko'rsatadi.
Energiya esa logit **masshtabini** o'qiydi, softmax uni yo'q qiladi.

Notebook oxirida ikki chegara hisoblanadi va chop etiladi:

```
CLASSIFIER_MODEL_PATH=/models/classifier.onnx
CLASSIFIER_MIN_CONFIDENCE=0.612
CLASSIFIER_MAX_ENERGY=-3.104
```

Ularni `.env` ga ko'chiring. Model qayta o'rgatilganda chegaralar ham qayta
hisoblanishi **shart** — ular validatsiya taqsimotidan olinadi.

Model yo'q bo'lsa tizim ishlashda davom etadi: klassifikator o'chadi, pipeline
chaqiruvchining `doc_type` maslahatiga tayanadi. `/readyz` da `"classifier"`
maydoni holatni ko'rsatadi.

Generatsiya qilinadigan artefaktlar (shablonlar, notebook) qo'lda
tahrirlanmaydi:

```bash
make templates    # packages/docgen/templates/*.docx
make notebooks    # training/01_classifier.ipynb
```

## LLM kalitlari

`.env` da uch xil kalit turi bor va ular teng emas:

| Kalit | Real PII | Nima uchun |
|---|---|---|
| `OPENAI_API_KEY` (pullik) | ✅ | Yuborilgan ma'lumot training uchun ishlatilmaydi |
| `GEMINI_FREE_KEYS` | ❌ | Bepul tier ma'lumoti mahsulotni yaxshilash uchun ishlatilishi mumkin |
| `GEMINI_PAID_KEYS` | ⚙️ | `GEMINI_PAID_ENABLED=true` bo'lgandan keyin |
| `LOCAL_VLM_URL` | ✅ | Ma'lumot infratuzilmangizdan chiqmaydi |

Bepul kalitlarning to'g'ri ishlatilishi — `training/00_synthetic_data.ipynb`
chiqargan **sintetik** dataset'da prompt sozlash. Bu real ish: kvotani
cheksiz sarflaysiz va real hujjat bilan tajriba qilish xavfi yo'q.

Bir loyiha ichidagi bir necha kalit bitta kvotani bo'lishadi, shuning uchun
ular orasida aylanish hech narsa bermaydi. Kalit pool'ining maqsadi —
**provayderlar orasida** ishonchlilik (biri uzilsa ikkinchisi ishlaydi).

⚠️ `GEMINI_PAID_KEYS` va `GEMINI_FREE_KEYS` **har xil** kalitlar bo'lishi
shart. Bir xil kalit ikkalasida ham turgan bo'lsa, `GEMINI_PAID_ENABLED=true`
qilinganda bepul tier kaliti real pasport ma'lumoti uchun ochilgan deb
hisoblanadi — bu PII darvozasi to'sa olmaydigan yagona holat.

⚠️ Real kalitlar faqat `.env` da (u `.gitignore` da). `.env.example` git'ga
tushadi, shuning uchun unda faqat bo'sh joy tutuvchilar bo'lishi kerak.

## Narx

L2 asosiy, L3 ~10% bo'lganda 1000 hujjat oyiga taxminan $1–7 turadi
(model tanloviga qarab). Narxlar va model nomlari konfiguratsiyada saqlanadi,
kodda emas — provayderlar model qatorini tez-tez almashtiradi.

## Deploy

Render (backend) + Vercel (frontend) uchun bosqichma-bosqich qo'llanma:
**`docs/DEPLOY.md`**. Blueprint `infra/render.yaml` to'rtala servisni bir
vaqtda yaratadi; frontend'da yagona muhim sozlama — Vercel'dagi Root
Directory `apps/web` bo'lishi.

⚠️ LLM kalitlari `ocr-ml` servisida bo'ladi, `ocr-api` da emas: `packages/llm`
aynan shu servis ichida ishlaydi.

## ⚠️ Ishga tushirishdan oldin

`docs/LEGAL.md` ni o'qing. O'zbekiston fuqarolarining shaxsga doir
ma'lumotlari mamlakat hududidagi serverlarda saqlanishi talab qilinadi.
Render, Vercel va chet el LLM provayderlari bu talabni qondirmaydi.
`infra/render.yaml` demo va test uchun; ishlab chiqarish uchun
`infra/onprem/` yoki mahalliy hosting kerak. Men yurist emasman — bu savolni
yurist bilan hal qiling.
