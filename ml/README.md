# YOLOv8 SAR ship fine-tune — Colab runbook

Produces `best.pt`, the checkpoint the backend loads from `backend/models/sar_ship.pt`
(path configurable via `MODEL_PATH`). Training runs on a free Colab GPU in ~1–2 h;
nothing here runs on the backend.

## Why xView3 (via SARFish)

The current checkpoint was trained on HRSID (0.5–3 m/px, where a 200 m hull is 70–400 px).
The pipeline fetches 10 m/px, where that same hull is ~20 px — and 10 m/px is not a
choice: it is GRDH's native pixel spacing *and* Sentinel Hub's finest setting for IW
`HIGH`, which serves GRD only (no SLC). The symptom this predicts is the symptom observed:
1 detection on a fully-covered `syria_coast_sts` scene, 14 on `kerch_strait`.

xView3-SAR fixes the gap on three axes at once:

- **Same sensor and resolution** — Sentinel-1 IW GRD at 10 m/px, ~1,000 scenes.
- **Same task** — labels come from global AIS matched to SAR with analyst verification.
  That is literally what this project does. ~220k instances.
- **Same rendering** — after calibration the pixels are sigma0 in decibels, the identical
  quantity `backend/app/sar.py`'s evalscript renders. `prepare_xview3.py` then applies the
  *same* dB→uint8 window; a test asserts the windows stay equal.

**We get the imagery from SARFish, the labels from DIU.**
[SARFish](https://huggingface.co/datasets/ConnorLuckettDSTG/SARFish) mirrors xView3's
Sentinel-1 GRD products on Hugging Face as permanent, `git`/`huggingface-cli`-fetchable
files — no expiring signed URLs to babysit, and you download only the scenes you want. The
one cost is that SARFish ships raw Level-1 `.SAFE.zip` (uint16 amplitude), so a calibration
step (`ml/safe_to_db.py`) converts each to the `VV_dB.tif` the chipper expects. That step is
isolated and unit-tested; the runbook stays *download → calibrate → chip → train*.

| Source | Role |
|---|---|
| **SARFish** (HF) | GRD imagery, permanent links — [ConnorLuckettDSTG/SARFish](https://huggingface.co/datasets/ConnorLuckettDSTG/SARFish) |
| **DIU** (iuu.xview.us) | `GRD_train.csv` labels — a small, permanent, SHA-verified download (not the expiring imagery links) |
| LS-SSDD-v1.0 | fallback: also IW GRD 10 m/px but ~6k instances, hard-to-obtain portal. `prepare_dataset.py --format voc` still reads it |

> **Use the GRD products and `GRD_*.csv` labels — never SLC.** SARFish also offers SLC at
> 2.3 × 14.1 m, finer than the 10 m GRD the pipeline fetches; training on it reopens the
> resolution gap. And SARFish's GRD labels are indexed to the GRD product grid, so they only
> line up with GRD imagery.

> **Labels are points, not boxes.** xView3/SARFish give `detect_scene_row`/`_column` plus
> `vessel_length_m`, with `top`/`left`/`bottom`/`right` only "where available".
> `prepare_xview3.py` uses the supplied box when present and synthesises a square from vessel
> length otherwise. Box imprecision is nearly free here because `backend/app/detect.py`
> collapses every predicted box to a centroid and discards the extent — only the centre has
> to be right.

## Colab cells (copy-paste in order)

> **Where each step runs:** cells 0–7 run **in the Colab notebook in your browser** (on
> Google's GPU machine). Only **step 8 runs in your own terminal**, on your local clone.

**0. Runtime → Change runtime type → T4 GPU**, then verify:

```python
!nvidia-smi -L
```

**1. Install + clone:**

```python
!pip -q install ultralytics rasterio huggingface_hub
!git clone https://{user}:{token}@github.com/{user}/dead-reckoning.git
%cd dead-reckoning
```

**2a. Labels (once, permanent).** Register at [iuu.xview.us](https://iuu.xview.us/) (the
challenge closed in 2021; the data stays free) and, from the download-links page, grab
`GRD_train.csv` under **SARFish labels** — a small direct download with a published SHA-1,
*not* one of the expiring imagery links:

```python
!mkdir -p /content/xview3
!wget -qO /content/xview3/labels.csv "PASTE_THE_GRD_train.csv_LINK"
!sha1sum /content/xview3/labels.csv   # expect 64a3a294a1c9914e92f832d82fe01e68824c70ce
!head -1 /content/xview3/labels.csv   # confirm columns: scene_id, GRD_product_identifier, detect_scene_row/column, …
```

> Use **`GRD_train.csv`**, the SARFish GRD labels — they are indexed to the GRD product
> grid, so they line up with the SARFish GRD imagery. (`GRD_validation.csv`, SHA-1
> `a3718b4c…`, is the smaller verified split if you prefer it — then pull validation scenes
> in 2b.) The xView3 `train.csv`/`validation.csv` use a *different* grid and won't align.

**2b. Imagery from SARFish (permanent, no expiring links).** SARFish hosts each GRD scene
as a `.SAFE.zip` on Hugging Face; `huggingface_hub` pulls just the ones you name. Pick scene
IDs from your label CSV. **Start with ~10.**

```python
import shutil, subprocess
from pathlib import Path
from huggingface_hub import hf_hub_download
import pandas as pd

REPO = "ConnorLuckettDSTG/SARFish"
PARTITION = "train"            # match the label CSV you downloaded (train / validation)
N_SCENES = 10
root = Path("/content/xview3"); root.mkdir(exist_ok=True)
safe_dir = root / "safe"; safe_dir.mkdir(exist_ok=True)

labels = pd.read_csv(root / "labels.csv")
products = labels["GRD_product_identifier"].dropna().unique()[:N_SCENES]
print(f"{len(products)} products to fetch")

for i, product in enumerate(products, 1):
    free = shutil.disk_usage("/content").free / 2**30
    print(f"[{i}/{len(products)}] {free:.0f} GB free — {product}")
    if free < 8:
        print("  stopping — low disk"); break
    name = product if product.endswith(".SAFE.zip") else f"{product}.SAFE.zip"
    zip_path = hf_hub_download(
        REPO, f"GRD/{PARTITION}/{name}", repo_type="dataset", local_dir=str(safe_dir)
    )
    # Calibrate SAFE (raw uint16 amplitude) -> sigma0-dB GeoTIFF the chipper reads.
    subprocess.run([
        "python", "ml/safe_to_db.py",
        "--safe", zip_path, "--labels", str(root / "labels.csv"), "--out", str(root),
    ], check=True)
    Path(zip_path).unlink()  # drop the ~1 GB archive; keep only VV_dB.tif

print(subprocess.run(["du", "-sh", str(root)], capture_output=True, text=True).stdout)
print(sorted(p.name for p in root.iterdir() if p.is_dir() and p.name != "safe")[:3])
```

> **Disk (Colab ~78 GB, ephemeral).** The loop fetches one `.SAFE.zip` (~1 GB), calibrates
> it to `{scene_id}/VV_dB.tif` (~0.8 GB deflate-compressed), then deletes the zip — so peak
> use is ~2 GB transient, ~0.8 GB steady per scene. 10 scenes ≈ 8 GB. Re-running is free and
> permanent; there are no links to refresh.
>
> `safe_to_db.py` reads the measurement raster straight out of the zip (`/vsizip/`, no
> unzip), parses the calibration LUT, and writes `sigma0` in dB — the exact quantity
> `sar.py` renders. If it errors on the first scene, run it alone to see the message:
> `!python ml/safe_to_db.py --safe <path>.SAFE.zip --labels /content/xview3/labels.csv --out /content/xview3`

**3. Chip into a YOLO dataset.** `--scenes` points at the calibration output, whose
per-scene `{scene_id}/VV_dB.tif` dirs the chipper walks. Splits **by scene**, never by chip
— chips from one scene are near-duplicates that would leak across the split and inflate mAP:

```python
!python ml/prepare_xview3.py \
  --scenes /content/xview3 \
  --labels /content/xview3/labels.csv \
  --out /content/datasets/xview3 \
  --val-scenes 3 \
  --max-background-frac 0.15
!yolo settings datasets_dir=/content/datasets
```

> `--scenes /content/xview3` also contains the `safe/` scratch dir and `labels.csv`; the
> chipper ignores any subdir without a `VV_dB.tif`, so that's fine.

Read the printed counts. Each scene reports its with-ships / background split; the totals
follow. If `with ships` is 0 anywhere, stop — the labels and scenes aren't matching, and
the error message names what it saw.

> `--max-background-frac 0.15` caps ship-free chips at 15% of the **train** split (val is
> left whole). A scene is mostly empty ocean, so without this the objective becomes
> background suppression and the model is pushed toward low recall — the exact failure
> being fixed. Chips containing a *filtered* detection (low-confidence, or `is_vessel=False`
> such as a platform or wind turbine) are dropped entirely rather than used as background,
> so the model is never taught to suppress a bright target it should find.

**4. Spot-check before burning GPU hours** — boxes should sit on visible bright returns:

```python
import random, cv2, matplotlib.pyplot as plt
from pathlib import Path
root = Path('/content/datasets/xview3')
all_labels = list((root / 'labels/train').glob('*.txt'))
labels = [p for p in all_labels if p.stat().st_size]
print(f"{len(all_labels)} train chips, {len(labels)} with ships, {len(all_labels)-len(labels)} background")
assert labels, "no chip contains a ship — re-read cell 3's counts"
fig, axes = plt.subplots(1, 4, figsize=(16, 4))
for ax, lbl in zip(axes, random.sample(labels, min(4, len(labels)))):
    img = cv2.imread(f'{root}/images/train/{lbl.stem}.png')
    h, w = img.shape[:2]
    for line in lbl.read_text().split('\n'):
        if not line: continue
        _, cx, cy, bw, bh = (float(v) for v in line.split())
        p1 = (int((cx - bw/2) * w), int((cy - bh/2) * h))
        p2 = (int((cx + bw/2) * w), int((cy + bh/2) * h))
        cv2.rectangle(img, p1, p2, (0, 255, 0), 2)
    ax.imshow(img[:, :, ::-1]); ax.set_title(lbl.stem, fontsize=8); ax.axis('off')
plt.show()
```

**5. Train two checkpoints (~1–2 h each on T4).** Both are benchmarked against the same
cached chips later, so training data is the only variable:

```python
# (a) from COCO weights — the clean xView3-only result
!python ml/train.py --data ml/xview3.yaml --name xview3_scratch

# (b) from the existing HRSID checkpoint — HRSID teaches what a ship-shaped bright blob
#     is from 16,951 instances; xView3 adapts that to 10 m/px.
!python ml/train.py --data ml/xview3.yaml --name xview3_from_hrsid \
  --model /content/drive/MyDrive/sar_ship.pt
```

> Run (b) needs the current `backend/models/sar_ship.pt`, which is gitignored and so is
> **not** in the fresh clone — upload it to Drive and point `--model` at it. Skip (b) if
> that's inconvenient; (a) is the one that matters.

**6. Evaluate both** on the held-out scenes:

```python
!python ml/eval.py runs/detect/xview3_scratch/weights/best.pt    --data ml/xview3.yaml
!python ml/eval.py runs/detect/xview3_from_hrsid/weights/best.pt --data ml/xview3.yaml
```

xView3 is a hard benchmark — small vessels in large empty scenes — so expect mAP50 well
below HRSID's ~0.85. **Do not read a lower number as a worse model**; scores are not
comparable across datasets. The decision happens in step 8.

**7. Download both checkpoints:**

```python
from google.colab import files
files.download("runs/detect/xview3_scratch/weights/best.pt")
files.download("runs/detect/xview3_from_hrsid/weights/best.pt")
```

**8. Benchmark locally — this is the actual decision.** Leave the current checkpoint
installed; the bench takes weights as arguments:

```bash
mv ~/Downloads/best.pt        backend/models/xview3_scratch.pt
mv ~/Downloads/best\ \(1\).pt backend/models/xview3_from_hrsid.pt

cd backend && .venv/bin/python scripts/bench_detector.py \
  --chip data/chips/singapore_strait_*.npy \
  --weights models/sar_ship.pt models/xview3_scratch.pt models/xview3_from_hrsid.pt
```

Winner = most AIS-matched detections without the unmatched count exploding. Install it as
`backend/models/sar_ship.pt`; `docker-compose.yml` mounts `backend/models/`, so no rebuild.

### Free extra candidates for the bench

Evaluating a checkpoint costs minutes and 0 PU, so pretrained SAR models are worth adding
as rows rather than betting on. The only drop-in YOLOv8 one found (0 downloads, no
published Sentinel-1 evaluation — treat as unvetted):

```bash
huggingface-cli download MeWan2808/yolov8n-sar-vessel-detection \
  unquantized/best.pt --local-dir backend/models/mewan
```

It was trained on SAR-Ship-Dataset (256×256 chips, Gaofen-3 at 1–5 m mixed with S1), so it
likely carries a version of the same resolution gap as HRSID — but the bench will say.

Not a drop-in, and the highest-ceiling option if everything above disappoints: AI2's xView3
competition model ([allenai/sar_vessel_detect](https://github.com/allenai/sar_vessel_detect),
weights on S3). It is a torchvision Faster R-CNN rather than ultralytics, and consumes
VV+VH plus bathymetry and wind rasters where this pipeline fetches VV only — so adopting it
means a second inference path in `detect.py` and +1/3 PU per analysis for VH.

## Why mAP is not the decision

`eval.py` scores each checkpoint on its own dataset's held-out split — generalization
*within* the dataset, not onto the chips this pipeline fetches. The real test is
`backend/scripts/bench_detector.py`: it runs each checkpoint over cached full-resolution
chips through the production `run_detection` path and scores detections against an AIS
snapshot taken at the scene's acquisition time. Matched detections are near-certain true
positives; AIS positions with no nearby detection are near-certain misses, and that second
number is what explains a detection count of 1.

Chips are fetched once with `backend/scripts/fetch_chip.py` (~116 PU for the two benchmark
ROIs) and cached under `backend/data/chips/`; every comparison after that costs 0 PU.
