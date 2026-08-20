"""Generate training/01_classifier.ipynb.

The notebook is written from source here rather than hand-edited as JSON so it
stays diffable and reviewable. Run: python scripts/build_classifier_notebook.py
"""

from __future__ import annotations

import json
from pathlib import Path

CELLS: list[tuple[str, str]] = []


def md(text: str) -> None:
    CELLS.append(("markdown", text.strip("\n")))


def code(text: str) -> None:
    CELLS.append(("code", text.strip("\n")))


md("""
# 01 — Hujjat turi klassifikatori (Colab T4)

Bu yagona majburiy training bosqichi. Qolgan hammasi (MRZ, qoidalar, LLM
xaritalash) o'rgatilgan modelsiz ishlaydi.

**Nima uchun kerak:**

| Sabab | Izoh |
|---|---|
| Yo'naltirish | Pasport MRZ yo'lidan, diplom ilovasi jadval yo'lidan boradi |
| **Rad etish** | ⭐ Asosiy maqsad — tanilmagan rasmni qabul qilmaslik |
| Narx | 30 ms CPU inference keraksiz LLM so'rovining oldini oladi |

`unknown` klassi eng muhimi. Chek rasmini yuklagan foydalanuvchi
"pasportingiz o'qildi" degan javob olmasligi kerak.

**Vaqt:** T4 da ~25-35 daqiqa. CPU da ham ishlaydi (~2 soat).
**Chiqish:** `classifier.onnx` (int8, < 6 MB) + `labels.json` + `MODEL_CARD.md`.
""")

code("""
# Colab T4 tekshiruvi. GPU bo'lmasa ham davom etadi, shunchaki sekinroq.
import subprocess
print(subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total',
                      '--format=csv,noheader'],
                     capture_output=True, text=True).stdout or 'GPU yo\\'q — CPU rejimi')
""")

code("""
!pip -q install timm==1.0.12 onnx==1.17.0 onnxruntime==1.20.1 albumentations==1.4.24
!pip -q install "numpy<3" opencv-python-headless

from google.colab import drive
drive.mount('/content/drive')

import os
DRIVE   = '/content/drive/MyDrive/ocr-docs'
DATASET = f'{DRIVE}/synthetic'      # 00_synthetic_data.ipynb chiqishi
CKPT    = f'{DRIVE}/checkpoints/classifier'
OUT     = f'{DRIVE}/models'
for p in (CKPT, OUT):
    os.makedirs(p, exist_ok=True)
print('dataset:', DATASET)
""")

md("""
## 1. Konfiguratsiya

Hamma giperparametr shu yerda. Pastda hech qanday sehrli raqam bo'lmasin —
o'zgartirish kerak bo'lsa, faqat shu katakni tahrirlaysiz.
""")

code("""
CONFIG = dict(
    # MobileNetV3-Small: T4 da tez, CPU da 30 ms, ONNX int8 da ~4 MB.
    # Aniqlik yetmasa 'efficientnet_b0' ga o'ting (sekinroq, ~16 MB).
    model_name   = 'mobilenetv3_small_100',
    image_size   = 320,
    batch_size   = 64,
    epochs       = 12,
    lr_head      = 3e-3,
    lr_backbone  = 3e-4,     # oxirgi bloklar uchun kichikroq
    weight_decay = 1e-4,
    label_smooth = 0.05,
    val_split    = 0.15,
    seed         = 1337,
    # OOD manbasi: hujjat bo'lmagan rasmlar. Bularsiz 'unknown' klassi
    # o'rgatilmaydi va model hamma narsani pasport deb ataydi.
    ood_images   = f'{DATASET}/../ood',   # ixtiyoriy papka
    num_ood      = 2000,
)
CLASSES = ['passport_bio', 'id_front', 'id_back',
           'diploma', 'diploma_supplement', 'birth_certificate', 'unknown']

import random, numpy as np, torch
random.seed(CONFIG['seed']); np.random.seed(CONFIG['seed'])
torch.manual_seed(CONFIG['seed']); torch.cuda.manual_seed_all(CONFIG['seed'])
DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'
print(DEVICE, CONFIG['model_name'])
""")

md("""
## 2. Ma'lumot

Kutilgan tuzilma — klass nomi bilan papkalar:

```
synthetic/classify/passport_bio/*.jpg
synthetic/classify/id_front/*.jpg
...
ood/*.jpg          ← hujjat bo'lmagan rasmlar (COCO namunasi, telefon suratlari)
```

⚠️ **`unknown` uchun ma'lumot topish** — eng ko'p o'tkazib yuboriladigan qadam.
Sintetik generator faqat hujjat chiqaradi, shuning uchun OOD rasmlarni alohida
qo'shish kerak. Papka bo'sh bo'lsa, quyidagi katak protsedural shovqin
generatsiya qiladi — bu yomonroq, lekin hech narsadan yaxshi.
""")

code("""
import glob, cv2
from pathlib import Path
from torch.utils.data import Dataset, DataLoader
import albumentations as A

ROOT = Path(DATASET) / 'classify'
items = []                                  # (path, class_index)
for idx, name in enumerate(CLASSES):
    if name == 'unknown':
        continue
    files = sorted(glob.glob(str(ROOT / name / '*.*')))
    items += [(f, idx) for f in files]
    print(f'{name:22} {len(files):6d}')

# --- unknown ---------------------------------------------------------------
UNKNOWN = CLASSES.index('unknown')
ood = sorted(glob.glob(f"{CONFIG['ood_images']}/*.*"))[:CONFIG['num_ood']]
if ood:
    items += [(f, UNKNOWN) for f in ood]
    print(f"{'unknown (real OOD)':22} {len(ood):6d}")
else:
    # Zaxira: protsedural shovqin va tekstura. Real OOD o'rnini bosolmaydi —
    # imkon topilganda haqiqiy rasmlar bilan almashtiring.
    synth_dir = Path('/content/ood_synth'); synth_dir.mkdir(exist_ok=True)
    n = min(CONFIG['num_ood'], max(400, len(items) // 6))
    rng = np.random.default_rng(CONFIG['seed'])
    for i in range(n):
        h, w = rng.integers(300, 700), rng.integers(300, 700)
        img = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
        k = int(rng.choice([5, 11, 21, 41]))
        img = cv2.GaussianBlur(img, (k | 1, k | 1), 0)
        cv2.imwrite(str(synth_dir / f'{i:05d}.jpg'), img)
    ood = sorted(glob.glob(str(synth_dir / '*.jpg')))
    items += [(f, UNKNOWN) for f in ood]
    print(f"{'unknown (sintetik)':22} {len(ood):6d}  ⚠️ real OOD qo'shing")

assert items, 'Dataset bo\\'sh. Avval 00_synthetic_data.ipynb ni ishlating.'
print('jami:', len(items))
""")

code("""
S = CONFIG['image_size']
MEAN = (0.485, 0.456, 0.406)
STD  = (0.229, 0.224, 0.225)

# Augmentatsiya telefon suratining buzilishlarini taqlid qiladi. Bu yerda
# ortiqcha kuchli bo'lmasin: klassifikator uchun umumiy shakl muhim, matn emas.
TRAIN_AUG = A.Compose([
    A.LongestMaxSize(S), A.PadIfNeeded(S, S, border_mode=cv2.BORDER_CONSTANT),
    A.Perspective(scale=(0.02, 0.08), p=0.6),
    A.Rotate(limit=15, border_mode=cv2.BORDER_REPLICATE, p=0.7),
    A.RandomBrightnessContrast(0.3, 0.3, p=0.7),
    A.ImageCompression(quality_range=(35, 95), p=0.5),
    A.MotionBlur(blur_limit=7, p=0.3),
    A.Normalize(MEAN, STD),
])
VAL_AUG = A.Compose([
    A.LongestMaxSize(S), A.PadIfNeeded(S, S, border_mode=cv2.BORDER_CONSTANT),
    A.Normalize(MEAN, STD),
])

class DocDataset(Dataset):
    def __init__(self, rows, aug): self.rows, self.aug = rows, aug
    def __len__(self): return len(self.rows)
    def __getitem__(self, i):
        path, label = self.rows[i]
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            img = np.zeros((S, S, 3), np.uint8)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = self.aug(image=img)['image']
        return torch.from_numpy(img.transpose(2, 0, 1)), label

random.shuffle(items)
cut = int(len(items) * (1 - CONFIG['val_split']))
train_ds, val_ds = DocDataset(items[:cut], TRAIN_AUG), DocDataset(items[cut:], VAL_AUG)
train_dl = DataLoader(train_ds, CONFIG['batch_size'], shuffle=True,  num_workers=2, pin_memory=True, drop_last=True)
val_dl   = DataLoader(val_ds,   CONFIG['batch_size'], shuffle=False, num_workers=2, pin_memory=True)
print('train', len(train_ds), '| val', len(val_ds))
""")

md("""
## 3. Training

Transfer learning: backbone muzlatilgan, faqat head va oxirgi bloklar
o'rgatiladi. Har epoxdan keyin Drive'ga checkpoint yoziladi — Colab sessiyasi
uzilishi normal hodisa, uni oldindan hisobga oling.
""")

code("""
import timm, torch.nn as nn
from torch.amp import autocast, GradScaler

model = timm.create_model(CONFIG['model_name'], pretrained=True,
                          num_classes=len(CLASSES)).to(DEVICE)

# Head + oxirgi ikki blok o'rgatiladi, qolgani muzlatiladi.
for p in model.parameters():
    p.requires_grad = False
trainable = list(model.get_classifier().parameters())
blocks = getattr(model, 'blocks', None)
if blocks is not None:
    for blk in list(blocks)[-2:]:
        for p in blk.parameters():
            p.requires_grad = True
for p in model.get_classifier().parameters():
    p.requires_grad = True

head_ids = {id(p) for p in model.get_classifier().parameters()}
backbone = [p for p in model.parameters() if p.requires_grad and id(p) not in head_ids]
opt = torch.optim.AdamW([
    {'params': list(model.get_classifier().parameters()), 'lr': CONFIG['lr_head']},
    {'params': backbone, 'lr': CONFIG['lr_backbone']},
], weight_decay=CONFIG['weight_decay'])
sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=CONFIG['epochs'])
lossf = nn.CrossEntropyLoss(label_smoothing=CONFIG['label_smooth'])
scaler = GradScaler(DEVICE, enabled=DEVICE == 'cuda')
print('trainable params:', sum(p.numel() for p in model.parameters() if p.requires_grad))
""")

code("""
import os, time, json

CKPT_FILE = f'{CKPT}/last.pt'
start_epoch, best_f1 = 0, 0.0
if os.path.exists(CKPT_FILE):                       # resume
    state = torch.load(CKPT_FILE, map_location=DEVICE)
    model.load_state_dict(state['model']); opt.load_state_dict(state['opt'])
    start_epoch, best_f1 = state['epoch'] + 1, state['best_f1']
    print(f'resumed at epoch {start_epoch}')

def evaluate():
    model.eval()
    logits_all, labels_all = [], []
    with torch.no_grad():
        for x, y in val_dl:
            with autocast(DEVICE, enabled=DEVICE == 'cuda'):
                out = model(x.to(DEVICE, non_blocking=True))
            logits_all.append(out.float().cpu()); labels_all.append(y)
    return torch.cat(logits_all), torch.cat(labels_all)

def macro_f1(logits, labels):
    pred = logits.argmax(1)
    f1s = []
    for c in range(len(CLASSES)):
        tp = ((pred == c) & (labels == c)).sum().item()
        fp = ((pred == c) & (labels != c)).sum().item()
        fn = ((pred != c) & (labels == c)).sum().item()
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return sum(f1s) / len(f1s), f1s

for epoch in range(start_epoch, CONFIG['epochs']):
    model.train(); t0 = time.time(); running = 0.0
    for x, y in train_dl:
        x, y = x.to(DEVICE, non_blocking=True), y.to(DEVICE, non_blocking=True)
        opt.zero_grad(set_to_none=True)
        with autocast(DEVICE, enabled=DEVICE == 'cuda'):
            loss = lossf(model(x), y)
        scaler.scale(loss).backward(); scaler.step(opt); scaler.update()
        running += loss.item()
    sched.step()
    logits, labels = evaluate()
    f1, per_class = macro_f1(logits, labels)
    print(f'epoch {epoch:2d}  loss {running/max(1,len(train_dl)):.4f}  '
          f'macroF1 {f1:.4f}  unknown_F1 {per_class[UNKNOWN]:.4f}  '
          f'{time.time()-t0:.0f}s')
    best_f1 = max(best_f1, f1)
    torch.save({'model': model.state_dict(), 'opt': opt.state_dict(),
                'epoch': epoch, 'best_f1': best_f1}, CKPT_FILE)
    if f1 >= best_f1:
        torch.save(model.state_dict(), f'{CKPT}/best.pt')
print('best macro F1:', round(best_f1, 4))
""")

md("""
## 4. Baholash va chegaralarni kalibrlash ⭐

Bu bo'lim eng muhimi. `classifier.onnx` bilan birga **ikkita chegara** ishlab
chiqiladi va ular `packages/ml/classify` da ishlatiladi:

- `CLASSIFIER_MIN_CONFIDENCE` — softmax pastki chegarasi
- `CLASSIFIER_MAX_ENERGY` — erkin energiya yuqori chegarasi

Energiya nima uchun kerak: softmax har doim 1 ga yig'iladi, shuning uchun
mushuk rasmini ko'rgan model ham 94% ishonch ko'rsatadi. Energiya
`-logsumexp(logits)` esa logit **masshtabini** o'qiydi, softmax uni yo'q qiladi.
Tanish kirishda energiya past, notanishda yuqori.
""")

code("""
model.load_state_dict(torch.load(f'{CKPT}/best.pt', map_location=DEVICE))
logits, labels = evaluate()
probs = logits.softmax(1)
energy = -torch.logsumexp(logits, dim=1)

f1, per_class = macro_f1(logits, labels)
print(f'macro F1 {f1:.4f}\\n')
for name, s in zip(CLASSES, per_class):
    print(f'  {name:22} F1 {s:.4f}')

# Chalkashlik matritsasi
import collections
cm = collections.Counter(zip(labels.tolist(), logits.argmax(1).tolist()))
print('\\nchalkashlik (haqiqiy -> bashorat, 0 dan katta):')
for (a, b), n in sorted(cm.items(), key=lambda kv: -kv[1]):
    if a != b:
        print(f'  {CLASSES[a]:22} -> {CLASSES[b]:22} {n}')

# --- chegaralar ---
known = labels != UNKNOWN
unknown = ~known
# Ma'lum hujjatlarning 98% i o'tsin: bir foydalanuvchini qayta suratga
# olishga majburlash noto'g'ri hujjat chiqarishdan arzon, lekin har uchinchi
# rasmni rad etadigan tizimdan hech kim foydalanmaydi.
min_conf = float(probs[known].max(1).values.quantile(0.02))
max_energy = float(energy[known].quantile(0.98))
if unknown.any():
    caught = float((energy[unknown] > max_energy).float().mean())
    print(f'\\nOOD ushlanishi shu energiya chegarasida: {caught:.1%}')
print(f'\\nCLASSIFIER_MIN_CONFIDENCE={min_conf:.3f}')
print(f'CLASSIFIER_MAX_ENERGY={max_energy:.3f}')
print('\\n^ shu ikki qatorni .env ga ko\\'chiring')
""")

md("""
## 5. ONNX eksport va int8 kvantizatsiya

Prod'da PyTorch yo'q — faqat ONNX Runtime CPU. Kvantizatsiyadan keyin
aniqlik yo'qotilishi 1% dan oshmasligi kerak; oshsa fp32 versiyani ishlating.
""")

code("""
import onnx, json
from onnxruntime.quantization import quantize_dynamic, QuantType

model.eval().cpu()
dummy = torch.randn(1, 3, CONFIG['image_size'], CONFIG['image_size'])
fp32 = '/content/classifier_fp32.onnx'
torch.onnx.export(model, dummy, fp32, opset_version=17,
                  input_names=['input'], output_names=['logits'],
                  dynamic_axes=None)          # batch 1: kichik va tez
onnx.checker.check_model(onnx.load(fp32))

int8 = '/content/classifier.onnx'
quantize_dynamic(fp32, int8, weight_type=QuantType.QUInt8)

import onnxruntime as ort, numpy as np, time
sess = ort.InferenceSession(int8, providers=['CPUExecutionProvider'])
x = dummy.numpy()
sess.run(None, {'input': x})                       # isitish
t0 = time.time()
for _ in range(20):
    ort_logits = sess.run(None, {'input': x})[0]
print(f'CPU inference: {(time.time()-t0)/20*1000:.1f} ms')
print(f'hajm: fp32 {os.path.getsize(fp32)/1e6:.1f} MB -> int8 {os.path.getsize(int8)/1e6:.1f} MB')

# Kvantizatsiya aniqlikni buzmaganini tekshirish
with torch.no_grad():
    torch_logits = model(dummy).numpy()
print('maks farq (fp32 torch vs int8 onnx):', float(np.abs(torch_logits - ort_logits).max()))
""")

code("""
import shutil, datetime
shutil.copy(int8, f'{OUT}/classifier.onnx')

with open(f'{OUT}/labels.json', 'w') as f:
    json.dump({'classes': CLASSES,
               'min_confidence': round(min_conf, 3),
               'max_energy': round(max_energy, 3)}, f, indent=2)

card = f'''# MODEL CARD — hujjat turi klassifikatori

- **Arxitektura:** {CONFIG['model_name']}, ImageNet pretrained, head + oxirgi 2 blok fine-tune
- **Kirish:** {CONFIG['image_size']}x{CONFIG['image_size']} RGB, ImageNet normalizatsiya
- **Klasslar:** {', '.join(CLASSES)}
- **Dataset:** {len(items)} rasm ({len(train_ds)} train / {len(val_ds)} val), asosan sintetik
- **Macro F1:** {f1:.4f}
- **unknown F1:** {per_class[UNKNOWN]:.4f}
- **Chegaralar:** min_confidence={min_conf:.3f}, max_energy={max_energy:.3f}
- **Format:** ONNX opset 17, int8 dinamik kvantizatsiya
- **Sana:** {datetime.date.today()}

## Ma'lum cheklovlar

- Dataset asosan **sintetik**. Real hujjatlarda aniqlik pastroq bo'ladi —
  100-200 ta real (anonimlashtirilgan) rasm bilan qayta baholang.
- `unknown` klassi OOD manbasi sifatini to'g'ridan-to'g'ri aks ettiradi.
  Sintetik shovqin bilan o'rgatilgan bo'lsa, real hujjat bo'lmagan rasmlarda
  ishonchsiz.
- Chegaralar validatsiya taqsimotidan olingan. Model qayta o'rgatilganda
  ular ham qayta hisoblanishi SHART.

## Ishlatish

```
CLASSIFIER_MODEL_PATH=/models/classifier.onnx
CLASSIFIER_MIN_CONFIDENCE={min_conf:.3f}
CLASSIFIER_MAX_ENERGY={max_energy:.3f}
```
'''
open(f'{OUT}/MODEL_CARD.md', 'w').write(card)
print('yozildi ->', OUT)
print(card)
""")

md("""
## 6. Modelni yuklab olish va repo'ga qo'yish

Model **repo ichida** saqlanadi (`models/classifier.onnx`, ~4 MB).
Model registry, Hugging Face yoki lokal kompyuterda training kerak emas:

```
Colab T4  ──▶  yuklab olish  ──▶  git commit  ──▶  Render image
```

Quyidagi katak uchta faylni brauzeringizga yuklab beradi.
""")

code(r"""
# Uchta faylni bevosita yuklab olish. Colab bularni brauzer orqali beradi —
# Drive'ga qayta kirish yoki boshqa xizmat kerak emas.
from google.colab import files as colab_files

for name in ['classifier.onnx', 'labels.json', 'MODEL_CARD.md']:
    src = f'{OUT}/{name}'
    if os.path.exists(src):
        print(f'{name}: {os.path.getsize(src)/1e6:.2f} MB')
        colab_files.download(src)
    else:
        print(f'{name}: TOPILMADI — yuqoridagi kataklarni ishlating')

print()
print("Keyingi qadam — loyiha repo'sida (lokal training YO'Q):")
print()
print('  1. Uchala faylni repo ichidagi models/ papkasiga qo\'ying')
print('  2. git add models/classifier.onnx models/labels.json models/MODEL_CARD.md')
print('     git commit -m \"classifier: v1\"')
print('     git push')
print()
print("  3. Render'da ocr-ml servisi env o'zgaruvchilari:")
print('       CLASSIFIER_MODEL_PATH=/srv/models/classifier.onnx')
print(f'       CLASSIFIER_MIN_CONFIDENCE={min_conf:.3f}')
print(f'       CLASSIFIER_MAX_ENERGY={max_energy:.3f}')
print()
print('  4. Deploy tugagach: /readyz -> \"classifier\": true')
print()
print('  Chegaralar shu modelga xos. Qayta o\'rgatsangiz ikkalasini ham')
print('  qayta yozing — eski chegara yangi model bilan ma\'nosiz.')
""")

md("""
## 7. Qachon qayta o'rgatish kerak

- Yangi hujjat turi qo'shilganda
- Eval'da `UNKNOWN_DOC_TYPE` xatosi 5% dan oshganda
- `unknown` klassi uchun **real** OOD rasmlar to'plangandan keyin
  (sintetik shovqin bilan o'rgatilgan model real hujjat bo'lmagan
  rasmlarda ishonchsiz)

Aniqlik yaxshi bo'lsa — **tegmang**. Yetarli darajadagi modelni
yaxshilashga urinish bu loyihada vaqtni yo'qotishning eng keng tarqalgan
usuli.

Modelni umuman o'rgatmaslik ham to'g'ri qaror: `models/classifier.onnx`
bo'lmasa klassifikator o'chadi va tizim MRZ + LLM bilan ishlashda davom
etadi. Yo'qoladigan narsa — tanilmagan rasmni oldindan rad etish.
""")


def main() -> None:
    nb = {
        "cells": [
            {
                "cell_type": kind,
                "metadata": {},
                "source": (src + "\n").splitlines(keepends=True),
                **({"outputs": [], "execution_count": None} if kind == "code" else {}),
            }
            for kind, src in CELLS
        ],
        "metadata": {
            "accelerator": "GPU",
            "colab": {"provenance": [], "gpuType": "T4"},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 0,
    }
    out = Path(__file__).resolve().parents[1] / "training" / "01_classifier.ipynb"
    out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out} ({len(CELLS)} cells)")


if __name__ == "__main__":
    main()
