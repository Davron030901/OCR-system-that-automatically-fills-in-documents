# models/

Bu yerga **Colab'da o'rgatilgan** klassifikator modeli qo'yiladi. Lokal
kompyuterda hech narsa o'rgatilmaydi, Hugging Face ham kerak emas.

```
Colab T4  ──▶  classifier.onnx (~4 MB)  ──▶  git commit  ──▶  Render image
```

## Papkada nima bo'ladi

| Fayl | Nima | Git'da |
|---|---|---|
| `classifier.onnx` | int8 kvantizatsiyalangan ONNX model | ✅ (`.gitignore` da istisno) |
| `labels.json` | Klass tartibi va kalibrlangan chegaralar | ✅ |
| `MODEL_CARD.md` | Arxitektura, dataset, metrikalar, cheklovlar | ✅ |

`.gitignore` da `*.onnx` bloklangan, lekin `!models/classifier.onnx` istisnosi
bor. Ya'ni tasodifiy checkpoint'lar repo'ga tushmaydi, faqat shu bitta model
tushadi.

## Nima uchun repo ichida, model registry emas

4 MB — kod bilan birga versiyalash uchun yetarlicha kichik. Buning uchta
amaliy foydasi bor:

1. **Deploy = `git push`.** Render image ichida model allaqachon bor; boot
   paytida hech narsa yuklab olinmaydi, tashqi xizmatga bog'liqlik yo'q.
2. **Model va chegaralar birga yuradi.** `CLASSIFIER_MIN_CONFIDENCE` va
   `CLASSIFIER_MAX_ENERGY` aynan shu modelning validatsiya taqsimotidan
   olingan. Model alohida joyda tursa, ular osongina bir-biriga mos
   kelmay qoladi — va bu jimgina buziladi.
3. **Rollback oddiy.** Eski commit'ga qaytish eski modelni ham qaytaradi.

Model 20 MB dan oshsa bu qaror qayta ko'rib chiqilsin — o'shanda git LFS yoki
S3 mantiqiyroq bo'ladi.

## Modelni qanday olish

`training/01_classifier.ipynb` ni Colab'da (T4) oching va oxirigacha
ishlating. Oxirgi katak uchta faylni Drive'ga yozadi va ularni bevosita
yuklab olish uchun havola beradi.

Keyin:

```bash
# Yuklab olingan uchta faylni models/ ga qo'ying
git add -f models/classifier.onnx models/labels.json models/MODEL_CARD.md
git commit -m "classifier: v1, macro F1 0.96"
git push
```

`-f` kerak emas (istisno qoidasi bor), lekin zarar ham qilmaydi.

Render avtomatik qayta deploy qiladi. Tekshirish:

```
/readyz  →  "classifier": true
```

## ⚠️ Chegaralarni yangilashni unutmang

Notebook oxirida ikki qator chop etiladi:

```
CLASSIFIER_MIN_CONFIDENCE=0.612
CLASSIFIER_MAX_ENERGY=-3.104
```

Ularni Render'dagi `ocr-ml` servisining env o'zgaruvchilariga yozing. Modelni
qayta o'rgatsangiz — chegaralarni ham qayta yozing. Eski chegara yangi model
bilan ma'nosiz: u boshqa taqsimotdan olingan.

`labels.json` ichida ham shu qiymatlar saqlanadi (hujjat sifatida), lekin
ishlatiladigani — env o'zgaruvchisi.

## Modelsiz ham ishlaydi

`models/classifier.onnx` bo'lmasa `CLASSIFIER_MODEL_PATH` bo'sh qoladi va
klassifikator o'chadi. Tizim ishlashda davom etadi: MRZ (L0) va qoidalar (L1)
lokal ishlaydi, LLM (L2) hujjat turini kontekstdan tushunadi. Yo'qoladigan
narsa — tanilmagan rasmni **oldindan** rad etish qobiliyati.

MVP'ni modelsiz ishga tushirish mumkin va normal.
