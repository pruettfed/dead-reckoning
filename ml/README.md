# YOLOv8 SAR ship fine-tune — Colab runbook

Produces `best.pt`, the checkpoint the backend loads from `backend/models/sar_ship.pt`
(path configurable via `MODEL_PATH`). Training runs on a free Colab GPU in ~1–2 h;
nothing here runs on the backend.

## Dataset options

| Dataset | Why | Source |
|---|---|---|
| **HRSID** (primary) | 5,604 × 800×800 chips, 16,951 ships, clean COCO annotations | github.com/chaozhong2010/HRSID (Google Drive link in its README) |
| **LS-SSDD-v1.0** (best domain match) | Built from Sentinel-1 IW GRD — same sensor/resolution the pipeline fetches | github.com/TianwenZhang0825/LS-SSDD-v1.0 |
| SSDD | Small (1,160 images), quick smoke-train | github.com/TianwenZhang0825/Official-SSDD |

All three are released for academic/research use — cite the papers (HRSID: Wei et al.,
IEEE Access 2020; LS-SSDD: Zhang et al., Remote Sensing 2020). `prepare_dataset.py`
handles any of them: point it at the images dir and the COCO train/val jsons.

> **Domain gap warning:** HRSID mixes 0.5–3 m imagery; the live pipeline fetches
> 10 m/px Sentinel-1. If detections on real chips are poor, retrain on LS-SSDD-v1.0
> (identical commands, different `--images`/`--*-json` paths).

## Colab cells (copy-paste in order)

> **Where each step runs:** cells 0–6 all run **in the Colab notebook in your
> browser** (on Google's GPU machine — nothing here touches your laptop).
> Only **step 7 runs in your own terminal**, on your local clone of the repo.
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

**2. Download HRSID.** Open the HRSID GitHub README, copy its Google Drive link, and
download it (Drive file id goes where marked):

```python
!pip -q install gdown
!gdown 'GOOGLE_DRIVE_FILE_ID'   # [USER: file id from the HRSID README link]
!unzip -q HRSID_JPG.zip -d /content/
```

**3. Convert to YOLO layout:**

```python
!python ml/prepare_dataset.py \
  --images /content/HRSID_JPG/JPEGImages \
  --train-json /content/HRSID_JPG/annotations/train2017.json \
  --val-json /content/HRSID_JPG/annotations/test2017.json \
  --out /content/datasets/hrsid
!yolo settings datasets_dir=/content/datasets
```

**4. Train (~1–2 h on T4):**

```python
!python ml/train.py --data ml/hrsid.yaml # optionally set number of epochs here (50 by default)
```

**5. Evaluate** (expect mAP50 ≳ 0.85 on HRSID for yolov8n):

```python
!python ml/eval.py runs/detect/train/weights/best.pt --data ml/hrsid.yaml
```

**6. Download the checkpoint** (push into local `~/Downloads/`):

```python
from google.colab import files
files.download("runs/detect/train/weights/best.pt")
```

**7. Install it locally**

```bash
mv ~/Downloads/best.pt backend/models/sar_ship.pt
```

The compose file mounts `backend/models/` into the container — no rebuild needed.

## Sanity check on a real chip (do this before trusting results)

After the checkpoint is installed and CDSE credentials are set, trigger one analysis
(`POST /api/analysis/singapore_strait`, see docs/api.md) and compare the detection
count against the live AIS vessel count for the same ROI. The Singapore Strait always
holds dozens of broadcasting ships — if the model finds almost nothing, or finds
hundreds of hits scattered over open water, the domain gap is biting: retrain on
LS-SSDD-v1.0.
