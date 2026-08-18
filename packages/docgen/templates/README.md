# Tayyor shablonlar

Bu papkadagi `.docx` fayllar **qo'lda tahrirlanmaydi** —
`scripts/build_templates.py` ularni generatsiya qiladi:

```bash
python scripts/build_templates.py
```

Sabab: `.docx` — bu XML fayllarning zip arxivi. Qo'lda commit qilingan
shablonni ko'rib chiqib bo'lmaydi, diff'i o'qilmaydi, va pull request'da
qaysi placeholder o'zgarganini hech kim ko'rmaydi. Skript ularni
generatsiya qilganda, mazmun ko'rinadigan Python kodida bo'ladi.

Generatsiyadan keyin skript har bir shablonni **tekshiradi**: analiz
xatosiz o'tishi, to'liq ma'lumot bilan ham, **bo'sh** `ExtractionResult`
bilan ham render bo'lishi shart. Ikkinchisi muhimroq — real hujjatlarda
maydonlarning yarmi ko'pincha bo'sh bo'ladi.

| Fayl | Nima | Nimani sinaydi |
|---|---|---|
| `ish_arizasi.docx` | Ishga qabul qilish arizasi | asosiy maydonlar, `pinfl_spaced` |
| `malumotnoma.docx` | Ma'lumotnoma (ish/o'quv joyidan) | jadval kataklari, `extra.*` |
| `mehnat_shartnomasi.docx` | Mehnat shartnomasi | `{% if %}` shart, `amount_words`, `upper_uz` |
| `otm_arizasi.docx` | OTM ga kirish arizasi | kirill maydonlar, `education.*` |
| `ishonchnoma.docx` | Ishonchnoma loyihasi | uzun matn ichida ko'p o'zgaruvchi |
| `diplom_ilovasi.docx` | Diplom ilovasi ma'lumotnomasi | **jadval loop** `{%tr for %}` |

## Ikki xil o'zgaruvchi

```
{{ person.pinfl }}          ← hujjatdan chiqarilgan (kanonik sxema)
{{ extra.salary }}          ← foydalanuvchi qo'lda kiritadi
```

`extra.*` — bu hech qanday pasportdan chiqmaydigan qiymatlar: ish haqi,
shartnoma raqami, lavozim. Analiz ularni `extra_fields` ro'yxatiga
yig'adi va yuklash ekrani shu ro'yxatdan qo'lda kiritish formasini
quradi. Ular "notanish o'zgaruvchi" deb belgilanmaydi.

## Shablon yozayotganda

`{{ }}` tegini **bir marta, uzluksiz** yozing. Word tegni bir necha
`<w:r>` ga bo'lib yuborsa tizim uni odatda tuzatadi (`coalesce_runs`),
lekin `{{` va `}}` har xil formatda bo'lsa (masalan yarmi qalin)
tuzatolmaydi — analiz bunda aniq xato beradi.

Mavjud filtrlar: `date_uz`, `date_uz_short`, `date_ru`, `date_iso`,
`upper_uz`, `lower_uz`, `cyrillic`, `latin`, `pinfl_spaced`,
`amount_words`, `initials`, `default`.
