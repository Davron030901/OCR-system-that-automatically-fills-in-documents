# Ishga tushirishda uchraydigan muammolar

## `COPY /app/public not found` (web build)

Next.js `public/` papkasini talab qilmaydi, va bo'sh papka git'da saqlanmaydi.
Tuzatildi: `apps/web/public/.gitkeep` qo'shildi va `Dockerfile.web` builder
bosqichida `mkdir -p public` bajaradi.

## `AssertionError: Status code 204 must not have a response body` (api ishga tushmaydi)

Konteyner qurildi, lekin `api-1` import paytida yiqiladi:

```
File "/srv/apps/api/app/routers/jobs.py", line 220, in <module>
    @router.delete("/{job_id}", status_code=204)
AssertionError: Status code 204 must not have a response body
```

Sabab nozik. `jobs.py` da `from __future__ import annotations` bor, shuning
uchun `-> None` annotatsiyasi FastAPI ga **`"None"` satri** sifatida yetadi.
FastAPI uni `NoneType` ga aylantiradi — bu esa klass obyekti, ya'ni *truthy*.
Natijada FastAPI "route'da javob tanasi bor" deb hisoblaydi va 204 uchun
assert qiladi.

Tuzatish — `response_model=None` ni ochiq yozish:

```python
@router.delete("/{job_id}", status_code=204, response_model=None)
async def delete_job(...) -> None:
```

Bu xato faqat import paytida chiqadi, shuning uchun unit testlar yashil
bo'lib turaveradi. Endi `tests/test_service_startup.py` har uchala ilovani
import qiladi va `make test` shu turdagi xatoni ikki soniyada topadi —
to'rt daqiqalik build'dan keyin emas.

## `make: pytest: No such file or directory`

Makefile hostda o'rnatilgan Python muhitini kutar edi. Endi `make test`
avtomatik virtualenv yaratadi:

```bash
make install     # .venv yaratadi va requirements/dev.txt o'rnatadi
make test        # install ni o'zi chaqiradi
```

Docker ichida test ishlatish uchun:
```bash
docker compose -f infra/docker-compose.yml run --rm api pytest packages -q
```

## `.env` va maxfiy so'zlar

```bash
make env
```

Bu `.env` ni yaratadi va uchta maxfiy so'zni generatsiya qiladi. Faqat stdlib
ishlatadi, shuning uchun hech narsa o'rnatilmagan toza clone'da ham ishlaydi.

**`make keys >> .env` ishlatmang.** U ikki sababdan xavfli:
- ikki marta ishga tushirilsa ikkita `ENCRYPTION_KEY` qatori qoladi; keyingisi
  jimgina g'olib chiqadi va birinchi kalit bilan shifrlangan hamma narsa
  o'qilmay qoladi
- `make` o'z buyruqlarini stdout'ga chiqaradi, ular ham faylga tushadi

## `failed to read .env: line NN: key cannot contain a space`

`.env` ga buyruq chiqishi tushib qolgan (odatda `>>` redirect orqali).

```bash
make env
```

`.env` faylida `=` belgisisiz qator hech qanday talqinda sozlama bo'la
olmaydi, shuning uchun `make env` bunday qatorlarni **avtomatik olib
tashlaydi** va nimani o'chirganini aytadi. Mavjud kalitlarga tegmaydi.

## `ModuleNotFoundError: No module named 'cryptography'` (make keys)

Tuzatildi: `make keys` endi faqat stdlib ishlatadi. AES-256 kaliti — 32 ta
tasodifiy bayt, buning uchun kutubxona kerak emas. Eski versiyada bu maqsad
`install` ga emas, faqat virtualenv yaratishga bog'liq edi.

## `relation "jobs" does not exist`

Jadval yaratilmagan. Endi API ishga tushganda avtomatik yaratiladi
(`app/db/bootstrap.py`), lekin **faqat `ENVIRONMENT != production` bo'lganda**.

Ishlab chiqarishda `create_all` ishlatilmaydi — u faqat qo'sha oladi, o'zgartira
va qaytara olmaydi, shuning uchun sxema migratsiya tarixidan jimgina ajralib
ketardi. Prod uchun Alembic migratsiyalari yozilishi kerak (hali yozilmagan).

## `NoSuchBucket` yuklashda

MinIO bo'sh holda ishga tushadi. Bootstrap bucket'ni avtomatik yaratadi. Agar
xato qolsa, MinIO ko'tarilishini kuting yoki qo'lda yarating:
`localhost:9001` (minioadmin / minioadmin).

## `MissingGreenlet` xatosi

SQLAlchemy async kontekstda relationship'ni lazy yuklay olmaydi. Barcha
`job.uploads` va `job.extraction` murojaatlari `selectinload` bilan
yozilgan. Yangi relationship qo'shsangiz, xuddi shunday qiling.

## ml-service: `local OCR unavailable`

Bu **kutilgan xabar**, xato emas. PaddleOCR `requirements/ml.txt` da yo'q
(og'ir, ~500 MB). Usiz tizim MRZ-only rejimda ishlaydi — pasport va ID karta
uchun bu allaqachon foydali natija beradi.

Lokal OCR ni yoqish:
```bash
echo "paddleocr==2.9.1" >> requirements/ml.txt
docker compose -f infra/docker-compose.yml build ml-service
```

## `failed to solve: image "infra-...:latest": already exists`

Docker'ning containerd image store'i bekor qilingan build'dan keyin yarim
yozilgan image qoldiradi. Keyingi build o'sha nom bilan to'qnashadi.

```bash
make reset-docker    # image'larni va build cache'ni tozalaydi
make dev
```

Yoki faqat bittasini:
```bash
docker rmi -f infra-converter:latest
```

## Build juda sekin (5-10 daqiqa)

**Eng katta sababchi — LibreOffice (~400 MB, ~280 sekund).** U endi sukut
bo'yicha qurilmaydi:

```bash
make dev        # LibreOffice yo'q — tez
make dev-full   # converter bilan (.doc shablonlar va PDF chiqishi uchun)
```

Converter faqat ikki narsa uchun kerak:
- `.doc` (eski Word) shablonini ro'yxatga olishda bir marta o'girish
- DOCX → PDF konvertatsiyasi

`.docx` shablonlar va `ENABLE_PDF_OUTPUT=false` bilan tizim to'liq ishlaydi.
Converter yo'q bo'lsa API 500 emas, tushunarli xabar qaytaradi.

Birinchi marta normal: LibreOffice ~400 MB, npm install ~150 s. Keyingi
build'lar layer cache tufayli tez bo'ladi. Faqat bitta servisni qayta qurish:

```bash
docker compose -f infra/docker-compose.yml build web
docker compose -f infra/docker-compose.yml up -d web
```

## Windows / WSL

Loyihani `/mnt/c/...` da emas, WSL fayl tizimida (`~/projects/...`) saqlang.
`/mnt/c` orqali Docker bind mount va npm install bir necha barobar sekin
ishlaydi.

## Render / Vercel deploy muammolari

Deploy bosqichidagi xatolar (CORS, `No Next.js version detected`,
`asyncio extension requires an async driver`, Docker konteksti) alohida
hujjatda: **`docs/DEPLOY.md`**, 8-bo'lim.
