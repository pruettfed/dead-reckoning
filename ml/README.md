# YOLOv8 SAR ship fine-tune

Trains the checkpoint the backend loads from `backend/models/sar_ship.pt`. Runs on a free
Colab GPU — nothing here runs on the backend. The runnable steps live in
[`backend/models/dr-training.ipynb`](../backend/models/dr-training.ipynb); this file is the
rationale.

## Why xView3 (via SARFish)

The original checkpoint was trained on HRSID (0.5–3 m/px, where a 200 m hull is 70–400 px).
The pipeline fetches 10 m/px — GRDH's native spacing and Sentinel Hub's finest for IW
`HIGH` (GRD only, no SLC) — where that hull is ~20 px. The domain gap showed up as sparse
detections on real chips.

xView3-SAR closes it on three axes: **same sensor/resolution** (Sentinel-1 IW GRD, 10 m/px),
**same task** (labels from global AIS matched to SAR, analyst-verified), and **same
rendering** — after calibration the pixels are sigma0 in dB, the identical quantity
`backend/app/sar.py` renders, so `prepare_xview3.py` chips through the *same* dB→uint8 window
(a test asserts the two windows match).

| Source | Role |
|---|---|
| **SARFish** (HF) | GRD imagery, permanent links — [ConnorLuckettDSTG/SARFish](https://huggingface.co/datasets/ConnorLuckettDSTG/SARFish) |
| **DIU** (iuu.xview.us) | `GRD_train.csv` labels — small, permanent, SHA-verified |

> **GRD + `GRD_*.csv` only, never SLC.** SLC is finer (2.3 × 14.1 m) and reopens the
> resolution gap; its labels are also indexed to a different grid.

> **Labels are points, not boxes.** `prepare_xview3.py` uses the supplied box where present
> and synthesises a square from `vessel_length_m` otherwise — precision barely matters since
> `detect.py` collapses each prediction to a centroid.

## Pipeline (what the notebook does)

1. **`safe_to_db.py`** — calibrate each SARFish `.SAFE.zip` (raw uint16 amplitude) to a
   `VV_dB.tif` (sigma0 in dB), streamed in row blocks to fit Colab RAM.
2. **`prepare_xview3.py`** — chip the dB rasters + labels into an 800×800 YOLO dataset,
   split by scene, capping ship-free background chips.
3. **`train.py`** — fine-tune YOLOv8 (defaults: `yolov8s`, 150 epochs, Drive-backed so a
   timeout doesn't cost the run).
4. **`eval.py`** — mAP on the held-out scenes.

The notebook also has label-QA spot-check cells and a checkpoint-comparison cell that
surfaces **recall** (the production failure was *missing* ships).

## Two placeholders before running

- **Cell 1:** GitHub username + read token.
- **Cell 2a:** the `GRD_train.csv` link from your iuu.xview.us download page (SHA-1
  `64a3a294a1c9914e92f832d82fe01e68824c70ce`).

## After training

1. Install the checkpoint: `mv best.pt backend/models/sar_ship.pt` (mounted into the
   container by `docker-compose.yml`, no rebuild).
2. **Re-tune the confidence buckets.** `CONF_HIGH` / `CONF_MEDIUM` in `backend/app/detect.py`
   are set to the old model's score scale; a new model's raw scores differ, so set them to
   its distribution.
3. **Confirm on a real chip** with `backend/scripts/fetch_chip.py` + `bench_detector.py`:
   fetch one Sentinel-1 pass (the only PU spend), then compare checkpoints at 0 PU — scoring
   detections against a live AIS snapshot, or `--no-ais` for a detection-count-only check.
