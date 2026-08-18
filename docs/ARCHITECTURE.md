# Arxitektura qarorlari

## ADR-001: Kanonik sxema markazda

**Muammo.** Har bir ekstraktor (MRZ, OCR, LLM) o'z formatida natija bersa,
har bir shablon har bir ekstraktorni bilishi kerak bo'ladi.

**Qaror.** `packages/schema/models.py` — barcha ekstraktorlar shunga
yozadi, barcha shablonlar shundan o'qiydi.

**Natija.** OCR dvigatelini butunlay almashtirish mumkin — shablonlar
o'zgarmaydi. LLM qatlamini olib tashlash mumkin — tizim ishlashda davom
etadi.

## ADR-002: FieldValue, oddiy satr emas

Har bir maydon `value` bilan birga `confidence`, `source`, `bbox`,
`validated` va `alternatives` tashiydi.

**Nima uchun.** Review UI uchtasini ham talab qiladi: qiymatni ko'rsatish,
qanchalik ishonchli ekanini bildirish, va rasmda qayerdan olinganini
belgilash. Bularni keyinroq qo'shish sxemani qayta yozishni anglatadi.

`source` foydalanuvchiga ko'rsatiladi: check digit bilan tasdiqlangan
qiymatga model taxminidan ko'proq ishonish — to'g'ri xatti-harakat.

## ADR-003: L0→L3 kaskad, bitta model emas

Har bosqich faqat oldingisi hal qilmagan maydonlar uchun ishlaydi.

| Bosqich | Narx | Ma'lumot chiqadimi |
|---|---|---|
| L0 MRZ | 0 | Yo'q |
| L1 Qoidalar | 0 | Yo'q |
| L2 LLM (matn) | past | Matn |
| L3 VLM (rasm) | yuqori | Rasm |

Bu optimizatsiya emas, xavfsizlik chegarasi. L2 ishga tushganda MRZ
allaqachon hujjat raqami, sanalar, jins va JSHSHIR ni check digit bilan
bergan bo'ladi — modeldan faqat u haqiqatan yaxshi bajaradigan ish
so'raladi: bosilgan manzilni o'qish, "Tug'ilgan sanasi" yorlig'i yonidagi
sanani bog'lash.

`l3_usage_rate` 15% dan oshsa — L1/L2 ni yaxshilash kerak degan signal.

## ADR-004: LibreOffice alohida servisda

**Muammo.** `.doc` faylni `.docx` ga o'girishning ishonchli sof Python
usuli yo'q. LibreOffice ~400 MB.

**Qaror.** `apps/converter` — alohida mikroservis.

**Kalit kuzatuv.** `.doc` konvertatsiyasi shablon **ro'yxatga olinganda**
bir marta bajariladi, har hujjat generatsiyasida emas. Servis kam
chaqiriladi, cold start muammo emas, API konteyneri esa kichik qoladi.

Har chaqiruvda alohida vaqtinchalik LibreOffice profili yaratiladi —
umumiy profil parallel chaqiruvlarda buziladi va nosozlik tasodifiy
ko'rinadi.

## ADR-005: Run coalescing majburiy qadam

Word `{{ person.pinfl }}` ni bir necha `<w:r>` ga bo'lib yuboradi
(imlo tekshiruvi, til belgisi, kursor joyi). docxtpl tegni topa olmaydi va
**jim** o'tkazib yuboradi — xato ham bermaydi.

Foydalanuvchi "shablonim ishlamayapti" deydi, log'da hech narsa yo'q.

Yechim: render'dan oldin bir xil formatdagi qo'shni run'larni birlashtirish.
Bir xil formatlash birlashtirilganda ko'rinish o'zgarmaydi, shuning uchun
buni shartsiz bajarish xavfsiz.

Tuzatilgan teglar hisobotda ko'rsatiladi — muallif Word teglarni bo'lib
yuborganini bilishi kerak, jimgina foyda ko'rish emas.

## ADR-006: Training keyinga surildi

Boshlang'ich rejada YOLO maydon detektori va OCR fine-tune markazda edi.

LLM qatlami maydon xaritalashni bajarganidan keyin, ular ixtiyoriy bo'lib
qoldi. Faqat hujjat turi klassifikatori qoladi (~30 daqiqa training).

**Qoida:** training faqat baholash natijasi talab qilganda. `eval/` bo'lmasa
"yaxshiladimmi?" savoliga javob yo'q, va ko'r-ko'rona training — eng qimmat
vaqt sarfi.
