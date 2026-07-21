"""Pure-function tests for the ml/ dataset converter.

The converter lives outside the backend package (it runs in Colab, not in the
container), so it is loaded by path to keep `cd backend && pytest` the single
test command.
"""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "prepare_dataset", Path(__file__).resolve().parents[2] / "ml" / "prepare_dataset.py"
)
prepare_dataset = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(prepare_dataset)


VOC_XML = """<annotation>
  <filename>01_1_1.jpg</filename>
  <size><width>800</width><height>800</height><depth>3</depth></size>
  <object>
    <name>ship</name>
    <bndbox><xmin>100</xmin><ymin>200</ymin><xmax>140</xmax><ymax>260</ymax></bndbox>
  </object>
</annotation>
"""

EMPTY_XML = """<annotation>
  <filename>01_1_2.jpg</filename>
  <size><width>800</width><height>800</height><depth>3</depth></size>
</annotation>
"""


class TestVocBoxToYolo:
    def test_centre_and_extent(self):
        cx, cy, w, h = prepare_dataset.voc_box_to_yolo(100, 200, 140, 260, 800, 800)
        assert (cx, cy) == pytest.approx((120 / 800, 230 / 800))
        assert (w, h) == pytest.approx((40 / 800, 60 / 800))

    def test_full_frame_box_is_unit(self):
        assert prepare_dataset.voc_box_to_yolo(0, 0, 800, 800, 800, 800) == pytest.approx(
            (0.5, 0.5, 1.0, 1.0)
        )

    def test_agrees_with_coco_on_the_same_box(self):
        """The two paths must land on identical labels for an identical box."""
        voc = prepare_dataset.voc_box_to_yolo(100, 200, 140, 260, 800, 800)
        coco = prepare_dataset.coco_bbox_to_yolo((100, 200, 40, 60), 800, 800)
        assert voc == pytest.approx(coco)


class TestParseVoc:
    def test_reads_size_and_boxes(self, tmp_path):
        xml = tmp_path / "01_1_1.xml"
        xml.write_text(VOC_XML)
        w, h, lines = prepare_dataset.parse_voc(xml)
        assert (w, h) == (800, 800)
        assert lines == ["0 0.150000 0.287500 0.050000 0.075000"]

    def test_ship_free_annotation_yields_no_lines(self, tmp_path):
        xml = tmp_path / "01_1_2.xml"
        xml.write_text(EMPTY_XML)
        assert prepare_dataset.parse_voc(xml)[2] == []


class TestWriteLabel:
    def test_background_file_is_genuinely_empty(self, tmp_path):
        path = tmp_path / "bg.txt"
        prepare_dataset.write_label(path, [])
        assert path.read_text() == ""

    def test_labels_are_newline_terminated(self, tmp_path):
        path = tmp_path / "ship.txt"
        prepare_dataset.write_label(path, ["0 0.5 0.5 0.1 0.1"])
        assert path.read_text() == "0 0.5 0.5 0.1 0.1\n"


def _build_voc_fixture(tmp_path, n_positive: int, n_background: int):
    """LS-SSDD-shaped source tree: images nested a level down, XML flat."""
    images = tmp_path / "JPEGImages_sub" / "JPEGImages_sub_train"
    annotations = tmp_path / "Annotations_sub"
    images.mkdir(parents=True)
    annotations.mkdir()
    ids = []
    for i in range(n_positive + n_background):
        image_id = f"01_1_{i}"
        ids.append(image_id)
        (images / f"{image_id}.jpg").write_bytes(b"not-a-real-jpeg")
        body = VOC_XML if i < n_positive else EMPTY_XML
        (annotations / f"{image_id}.xml").write_text(body)
    return tmp_path / "JPEGImages_sub", annotations, ids


class TestIndexImages:
    def test_finds_images_nested_in_subfolders(self, tmp_path):
        images_root, _, ids = _build_voc_fixture(tmp_path, 2, 0)
        index = prepare_dataset.index_images(images_root)
        assert set(index) == set(ids)


class TestConvertVocSplit:
    def test_keeps_every_positive_and_caps_backgrounds(self, tmp_path):
        images_root, annotations, ids = _build_voc_fixture(tmp_path, 10, 90)
        out = tmp_path / "out"
        converted, missing, backgrounds = prepare_dataset.convert_voc_split(
            ids,
            prepare_dataset.index_images(images_root),
            annotations,
            out,
            "train",
            max_background_frac=0.2,
        )
        assert missing == 0
        # 10 positives, backgrounds capped so bg/(pos+bg) <= 0.2 → 2 kept
        assert backgrounds == 2
        assert converted == 12
        assert len(list((out / "images" / "train").glob("*.jpg"))) == 12
        assert len(list((out / "labels" / "train").glob("*.txt"))) == 12

    def test_no_cap_keeps_everything(self, tmp_path):
        images_root, annotations, ids = _build_voc_fixture(tmp_path, 10, 90)
        converted, _, backgrounds = prepare_dataset.convert_voc_split(
            ids,
            prepare_dataset.index_images(images_root),
            annotations,
            tmp_path / "out",
            "val",
        )
        assert (converted, backgrounds) == (100, 90)

    def test_subsample_is_seeded(self, tmp_path):
        images_root, annotations, ids = _build_voc_fixture(tmp_path, 10, 90)
        index = prepare_dataset.index_images(images_root)
        kept = []
        for run in ("a", "b"):
            out = tmp_path / run
            prepare_dataset.convert_voc_split(
                ids, index, annotations, out, "train", max_background_frac=0.5, seed=7
            )
            kept.append(sorted(p.stem for p in (out / "images" / "train").glob("*.jpg")))
        assert kept[0] == kept[1]

    def test_missing_image_or_xml_is_counted(self, tmp_path):
        images_root, annotations, ids = _build_voc_fixture(tmp_path, 3, 0)
        (annotations / f"{ids[0]}.xml").unlink()
        _, missing, _ = prepare_dataset.convert_voc_split(
            ids + ["99_9_9"],
            prepare_dataset.index_images(images_root),
            annotations,
            tmp_path / "out",
            "train",
        )
        assert missing == 2


class TestReadIds:
    def test_strips_blank_lines(self, tmp_path):
        manifest = tmp_path / "train.txt"
        manifest.write_text("01_1_1\n01_1_2\n\n  01_1_3  \n")
        assert prepare_dataset.read_ids(manifest) == ["01_1_1", "01_1_2", "01_1_3"]
