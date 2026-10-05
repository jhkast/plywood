import pytest

from plywood.core.models import Grain, StockKind
from plywood.io.onshape_bom import looks_like_onshape_bom, parse_dims, read_onshape_bom, read_onshape_bom_text

IN = 25.4


def test_parse_dims_inches_full():
    d = parse_dims("30 x 23.25 x 0.75 in sheet grain=L tag=Baltic birch")
    assert (d.length, d.width, d.thickness) == pytest.approx((30 * IN, 23.25 * IN, 0.75 * IN))
    assert d.kind == StockKind.SHEET and d.grain == Grain.LENGTH and d.tag == "Baltic birch"


def test_parse_dims_mm_board_defaults():
    d = parse_dims("762 x 38.1 x 19.05 mm board")
    assert d.length == pytest.approx(762) and d.kind == StockKind.HARDWOOD
    assert d.grain == Grain.NONE and d.tag is None


@pytest.mark.parametrize("text", ["", "Steel", "30 x 20", "M6 x 20 screw"])
def test_parse_dims_rejects_other_text(text):
    assert parse_dims(text) is None


def write(tmp_path, text):
    p = tmp_path / "bom.csv"
    p.write_text(text, encoding="utf-8")
    return p


def test_flat_bom(tmp_path):
    path = write(tmp_path, (
        "Item,Quantity,Part number,Name,Material,Title 1\n"
        "1,4,,Side,,30 x 23.25 x 0.75 in sheet grain=L\n"
        "2,2,,Bottom,,34.5 x 23.25 x 0.75 in sheet\n"
        "3,16,,Screw,Steel,\n"
        "4,4,,Stile,,30 x 1.5 x 0.75 in board\n"
    ))
    assert looks_like_onshape_bom(path)
    bom = read_onshape_bom(path)
    got = {p.name: (p.qty, p.kind, p.grain) for p in bom.parts}
    assert got == {
        "Side": (4, StockKind.SHEET, Grain.LENGTH),
        "Bottom": (2, StockKind.SHEET, Grain.NONE),
        "Stile": (4, StockKind.HARDWOOD, Grain.NONE),
    }
    assert bom.skipped == ["Screw"]


def test_structured_bom_multiplies_and_merges(tmp_path):
    path = write(tmp_path, (
        "Item,Quantity,Name,Title 1\n"
        "1,2,Drawer,\n"
        "1.1,2,Drawer side,22 x 5.5 x 0.5 in sheet grain=L\n"
        "1.2,1,Drawer bottom,21.5 x 15 x 0.25 in sheet\n"
        "2,1,Carcass,\n"
        "2.1,2,Drawer side,22 x 5.5 x 0.5 in sheet grain=L\n"
    ))
    bom = read_onshape_bom(path)
    got = {p.name: p.qty for p in bom.parts}
    assert got == {"Drawer side": 6, "Drawer bottom": 2}
    assert bom.skipped == []


def test_bom_without_dims_explains_setup(tmp_path):
    path = write(tmp_path, "Item,Quantity,Name\n1,4,Side\n")
    assert not looks_like_onshape_bom(path)
    with pytest.raises(ValueError, match="Cut list dims"):
        read_onshape_bom(path)


def test_mirrored_part_borrows_original_dims(tmp_path):
    path = write(tmp_path, (
        "Item,Quantity,Name,Title 1\n"
        "1,2,deep drawer side,660.4 x 203.2 x 12.7 mm sheet\n"
        "2,2,deep drawer side-Mirrored,\n"
        "3,1,Orphan-Mirrored,\n"
    ))
    bom = read_onshape_bom(path)
    got = {p.name: p.qty for p in bom.parts}
    assert got == {"deep drawer side": 2, "deep drawer side-Mirrored": 2}
    assert bom.skipped == ["Orphan-Mirrored"]


def test_utf8_bom_prefix_is_ignored():
    from plywood.io.onshape_bom import read_onshape_bom_text

    bom = read_onshape_bom_text("﻿Item,Quantity,Name,Title 1\n1,3,Side,10 x 5 x 19.05 mm sheet\n")
    assert bom.parts[0].qty == 3


def test_material_column_fills_missing_tag():
    text = (
        "Item,Quantity,Name,Material,Title 1\n"
        "1,2,Side,Birch,30 x 23 x 0.75 in sheet\n"
        "2,1,Top,Birch,30 x 23 x 0.75 in sheet tag=walnut ply\n"
    )
    parts = {p.name: p for p in read_onshape_bom_text(text).parts}
    assert parts["Side"].tag == "Birch" and parts["Top"].tag == "walnut ply"


@pytest.mark.parametrize("word, kind", [("board", "hardwood"), ("hardwood", "hardwood"), ("dimensional", "dimensional"), ("sheet", "sheet")])
def test_kind_words(word, kind):
    assert parse_dims(f"762 x 88.9 x 38.1 mm {word}").kind == kind
