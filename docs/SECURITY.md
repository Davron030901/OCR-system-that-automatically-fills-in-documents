# Xavfsizlik va shaxsiy ma'lumotlar

Bu tizim pasport ma'lumotlari va JSHSHIR bilan ishlaydi — shaxsiy
ma'lumotlarning eng sezgir toifasi. Quyidagilar dizayn cheklovi, keyingi
faza emas.

## Ma'lumot oqimi

```
Rasm  →  storage (shifrlangan, TTL 24 soat)
      →  ml-service (xotirada, saqlanmaydi)
      →  L2 bo'lsa: FAQAT matn tashqi provayderga
      →  natija  →  AES-GCM  →  postgres
```

Rasm hech qachon L2 bosqichida chiqmaydi. L3 (rasm yuborish) `.env` da
sukut bo'yicha o'chirilgan — bu ongli qaror bo'lishi kerak.

## Saqlash muddatlari

| Ma'lumot | Muddat | Mexanizm |
|---|---|---|
| Yuklangan rasm | 24 soat | `services/retention.py` + storage lifecycle |
| Chiqarilgan ma'lumot | 30 kun | `retention.sweep_expired()` |
| Generatsiya qilingan hujjat | 90 kun | Bir xil sweep |
| Audit log | 1 yil | Arxiv |

Sweep test bilan qoplangan. Faqat hujjatda yozilgan siyosat — siyosat emas.

## Shifrlash

`extractions.data_encrypted` — AES-GCM. Kalit environment yoki secret
manager'dan; API ishlab chiqarishda kalitsiz **ishga tushmaydi**.

AAD sifatida `job_id` ishlatiladi: bitta jobning shifrlangan bloki boshqa
job qatoriga ko'chirilsa, ochilmaydi.

Kalit rotatsiyasi: shifrmatn birinchi bayti versiya belgisi. Yangi versiya
qo'shib, eskisini o'qishda davom etish mumkin — migratsiya shart emas.

```bash
make keys   # yangi kalit generatsiya qiladi
```

## Log'lar

`security/redaction.py` logging filtri sifatida o'rnatiladi — `main.py` da
birinchi qatorlardan biri, hech qanday startup xabari undan oldin
chiqmasligi uchun.

Filtrlanadi: JSHSHIR, hujjat raqami, MRZ qatorlari, email, telefon, API
kalitlari. Nomi bo'yicha: `pinfl`, `surname`, `value`, `secret`, `token` va
boshqalar.

Kutilmagan xatolik matni ko'pincha qayta ishlanayotgan hujjat parchasini
o'z ichiga oladi. Shuning uchun u foydalanuvchiga **hech qachon**
qaytarilmaydi — faqat barqaror kod va o'zbekcha xabar.

## Yuklangan fayllar

Kengaytmaga ishonilmaydi — magic byte tekshiriladi. `.doc` deb nomlangan
RTF va HTML fayllar juda keng tarqalgan (Word ularni ochadi, foydalanuvchi
farqni bilmaydi).

Shablonlar alohida xavf: ular ishonchsiz **kod** o'z ichiga oladi.
- Jinja2 `SandboxedEnvironment` majburiy
- Statik SSTI tekshiruvi yuklash paytida (ikki qatlamli himoya)
- `.docm`, VBA, ZIP bomba, XML entity bomba, DDE maydoni — rad etiladi
- Tashqi havolalar olib tashlanadi

## LLM provayderlari

`PIIGate` har chaqiruvdan oldin ishlaydi va **konfiguratsiya bilan
o'chirilmaydi**. Xavfsizlik nazoratini o'chirish flag'i oxir-oqibat
ishlab chiqarishda yoqib qo'yiladi.

Ikki tekshiruv:
1. **Siyosat** — real PII, tier ruxsat etmagan bo'lsa → rad
2. **Evristika** — "sintetik" deb belgilangan, lekin 14 xonali raqam yoki
   MRZ ko'rinishidagi qator bor bo'lsa → rad. Chaqiruvchi xato qilishi
   mumkin, va bu xatoning oqibati qaytarib bo'lmaydigan

`llm_usage` jadvalida prompt va javob **mazmuni** saqlanmaydi. Narx
hisobi buni talab qilmaydi.

## Rad etilgan yondashuvlar

**Bir necha bepul akkaunt orqali kvotadan qutulish.** ToS muammosi,
hammasi birga bloklanishi mumkin, va mahsulot poydevori sifatida
yaroqsiz. Pullik kalit oyiga bir necha dollar turadi — rotatsiya tizimini
yozish va saqlashdan arzonroq.

**Statik PDF shablonlarni koordinata xaritasi bilan to'ldirish.**
Vizual editor talab qiladi (~3 hafta) va DOCX yetarli edi.

**Diplom uchun LayoutLMv3 fine-tune.** 300+ annotatsiyalangan diplom
yig'ib, 10 soat training qilib, VLM'dan yaxshi natija olish ehtimoli past.

