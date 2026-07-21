# YOLOv8 SAR ship fine-tune — Colab runbook

Produces `best.pt`, the checkpoint the backend loads from `backend/models/sar_ship.pt`
(path configurable via `MODEL_PATH`). Training runs on a free Colab GPU in ~1–2 h;
nothing here runs on the backend.

## Dataset options

| Dataset | Why | Source |
|---|---|---|
| **LS-SSDD-v1.0** (primary) | 9,000 × 800×800 VV chips cut from 15 Sentinel-1 **IW GRD** scenes — the same sensor, mode and 10 m/px the pipeline actually fetches | github.com/TianwenZhang0825/LS-SSDD-v1.0-OPEN (portal: radars.ac.cn/web/data/getData) |
| HRSID | 5,604 × 800×800 chips, 16,951 ships, clean COCO annotations — but 0.5–3 m/px | github.com/chaozhong2010/HRSID (Google Drive link in its README) |
| SSDD | Small (1,160 images), quick smoke-train | github.com/TianwenZhang0825/Official-SSDD |

All are released for academic/research use — cite the papers (LS-SSDD: Zhang et al.,
*Remote Sensing* 2020; HRSID: Wei et al., *IEEE Access* 2020). `prepare_dataset.py` reads
HRSID/SSDD with `--format coco` and LS-SSDD with `--format voc`.

> **Why LS-SSDD is primary now.** HRSID mixes 0.5–3 m imagery, where a 200 m hull is
> 70–400 px. The pipeline fetches 10 m/px, where that same hull is ~20 px — and 10 m/px is
> not a choice: it is GRDH's native pixel spacing *and* Sentinel Hub's finest setting for
> IW `HIGH`, which serves GRD only (no SLC). The HRSID checkpoint showed the symptom this
> predicts: 1 detection on a fully-covered `syria_coast_sts` scene, 14 on `kerch_strait`.

## Colab cells (copy-paste in order)

> **Where each step runs:** cells 0–7 all run **in the Colab notebook in your
> browser** (on Google's GPU machine — nothing here touches your laptop).
> Only **step 8 runs in your own terminal**, on your local clone of the repo.
> Cells prefixed `!` are shell commands Colab runs on its machine; `%cd` is a
> Colab magic; the rest is Python.

**0. Runtime → Change runtime type → T4 GPU**, then verify:

```python
!nvidia-smi -L
```

**1. Install + clone this repo:**

```python
from getpass import getpass
token = getpass("GitHub PAT: ")  # use a PAT with read-access
user = "USERNAME"
!pip -q install ultralytics
!git clone https://github.com/pruettfed/dead-reckoning.git
%cd dead-reckoning
```

**2. Get LS-SSDD-v1.0.** It is distributed through the radars.ac.cn portal (registration
required), not a direct link — download it once to your own Google Drive, then mount that
Drive here so re-runs don't re-download:

```python
from google.colab import drive
drive.mount('/content/drive')
!unzip -q "/content/drive/MyDrive/LS-SSDD-v1.0.zip" -d /content/
!ls /content/LS-SSDD-v1.0
```

Expected top level — check it before continuing, and adjust the paths in cell 3 if your
archive nests things one level deeper:

```
JPEGImages/        15 large scenes, 01.jpg .. 15.jpg (VV, 24000x16000)
JPEGImages_sub/    9,000 800x800 sub-images, in JPEGImages_sub_train/ and _test/
Annotations_sub/   9,000 VOC XML, 01_1_1.xml .. 15_20_30.xml
ImageSets/         train.txt, test.txt, test_inshore.txt, test_offshore.txt
JPEGImages_VH/     15 VH scenes (unused — the pipeline fetches VV only)
Tools/             images_stitch.py, user_manual.pdf
```

**3. Convert to YOLO layout.** `--images` is searched recursively, so it finds the
sub-images in either the train/ or test/ subfolder. The split comes from LS-SSDD's own
`ImageSets` manifests (scenes 1–10 train, 11–15 test), which keeps mAP comparable to the
published numbers:

```python
!python ml/prepare_dataset.py --format voc \
  --images /content/LS-SSDD-v1.0/JPEGImages_sub \
  --annotations /content/LS-SSDD-v1.0/Annotations_sub \
  --train-ids /content/LS-SSDD-v1.0/ImageSets/train.txt \
  --val-ids /content/LS-SSDD-v1.0/ImageSets/test.txt \
  --max-background-frac 0.15 \
  --out /content/datasets/lsssdd
!yolo settings datasets_dir=/content/datasets
```

> `--max-background-frac 0.15` caps ship-free chips at 15% of the **train** split (val is
> left whole). Most of LS-SSDD's 9,000 sub-images are empty water; training on that ratio
> makes the objective mostly background suppression and pushes the model toward low
> recall — the exact failure this retrain is meant to fix. Ultralytics recommends ~0–10%
> backgrounds. The cell prints the with-ships / background counts, so check them.

**4. Spot-check the conversion before burning GPU hours** — boxes should sit on visible
bright targets:

```python
import random, cv2, matplotlib.pyplot as plt
from pathlib import Path
labels = [p for p in Path('/content/datasets/lsssdd/labels/train').glob('*.txt') if p.stat().st_size]
fig, axes = plt.subplots(1, 4, figsize=(16, 4))
for ax, lbl in zip(axes, random.sample(labels, 4)):
    img = cv2.imread(f'/content/datasets/lsssdd/images/train/{lbl.stem}.jpg')
    h, w = img.shape[:2]
    for line in lbl.read_text().split('\n'):
        if not line: continue
        _, cx, cy, bw, bh = (float(v) for v in line.split())
        x1, y1 = int((cx - bw/2) * w), int((cy - bh/2) * h)
        x2, y2 = int((cx + bw/2) * w), int((cy + bh/2) * h)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
    ax.imshow(img[:, :, ::-1]); ax.set_title(lbl.stem); ax.axis('off')
plt.show()
```

**5. Train two checkpoints (~1–2 h each on T4).** Both are benchmarked later against the
same cached chips, so the training data is the only variable:

```python
# (a) from COCO weights — the clean LS-SSDD-only result
!python ml/train.py --data ml/lsssdd.yaml --name lsssdd_scratch

# (b) from the existing HRSID checkpoint — HRSID teaches what a ship-shaped bright blob
#     is from 16,951 instances, LS-SSDD adapts that to 10 m/px. LS-SSDD alone has ~6,000
#     instances and may simply underfit, so this is the likelier winner.
!python ml/train.py --data ml/lsssdd.yaml --name lsssdd_from_hrsid \
  --model backend/models/sar_ship.pt
```

> Run (b) needs the current `backend/models/sar_ship.pt`, which is gitignored and so is
> **not** in the fresh clone. Upload it to Drive alongside the dataset and point `--model`
> at the Drive copy.

**6. Evaluate both** on the held-out LS-SSDD split:

```python
!python ml/eval.py runs/detect/lsssdd_scratch/weights/best.pt   --data ml/lsssdd.yaml
!python ml/eval.py runs/detect/lsssdd_from_hrsid/weights/best.pt --data ml/lsssdd.yaml
```

LS-SSDD is a harder benchmark than HRSID — small ships against large backgrounds — so
expect mAP50 well below HRSID's ~0.85. Published baselines land in the 0.6–0.75 range.
**Do not read a lower LS-SSDD mAP as a worse model**; the numbers are not comparable
across datasets. The decision is made in step 8, on real chips.

**7. Download both checkpoints:**

```python
from google.colab import files
files.download("runs/detect/lsssdd_scratch/weights/best.pt")
files.download("runs/detect/lsssdd_from_hrsid/weights/best.pt")
```

**8. Benchmark all three locally — this is the actual decision.** Keep the current HRSID
checkpoint in place for now; the bench takes weights as arguments and does not need them
installed:

```bash
mv ~/Downloads/best.pt      backend/models/lsssdd_scratch.pt
mv ~/Downloads/best\ \(1\).pt backend/models/lsssdd_from_hrsid.pt

cd backend && .venv/bin/python scripts/bench_detector.py \
  --chip data/chips/singapore_strait_*.npy \
  --weights models/sar_ship.pt models/lsssdd_scratch.pt models/lsssdd_from_hrsid.pt
```

The winner is the one that matches the most AIS-broadcasting vessels without its unmatched
count exploding. Install it as `backend/models/sar_ship.pt` — `docker-compose.yml` mounts
`backend/models/` into the container, so no rebuild is needed.

## Why mAP is not the decision

`eval.py` scores each checkpoint on its own dataset's held-out split. That measures
generalization *within* the dataset, not onto the chips this pipeline actually fetches,
which differ in rendering (dB window, speckle) as well as content. The real test is
`backend/scripts/bench_detector.py`: it runs each checkpoint over cached full-resolution
chips through the production `run_detection` path and scores detections against an AIS
snapshot taken at the scene's acquisition time. Matched detections are near-certain true
positives; AIS positions with no nearby detection are near-certain misses, and that second
number is what explains a detection count of 1.

Chips are fetched once with `backend/scripts/fetch_chip.py` (~116 PU for the two benchmark
ROIs) and cached under `backend/data/chips/`; every comparison after that costs 0 PU.
