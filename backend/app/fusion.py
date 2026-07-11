"""SAR detections ↔ AIS fusion (PostGIS ST_DWithin, 500m / 2h).

A SAR detection is flagged "dark" if no AIS position matches within ~500 m of the
scene's acquisition time. Two rules keep this honest:
  - Clip the comparison to ROI ∩ image-footprint at the single acquisition
    timestamp; a detection outside the imaged footprint is *unobserved*, not dark.
  - Never mosaic passes for the correlation — different passes have different
    times and the vessels have moved. Mosaic only as a visual backdrop.
"""
