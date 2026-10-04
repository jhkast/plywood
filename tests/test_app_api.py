from plywood.app.api import Api, default_state


def state_with(parts, stock=(), units="in"):
    s = default_state()
    s["units"] = units
    s["parts"] = [dict({"name": "", "length": "", "width": "", "thickness": "", "qty": "", "grain": "", "kind": "sheet", "tag": ""}, **p) for p in parts]
    s["stock"] = [dict({"name": "", "length": "", "width": "", "thickness": "", "qty": "", "kind": "sheet", "tag": ""}, **r) for r in stock]
    s["settings"]["tries"] = 50
    return s


def api(tmp_path):
    return Api(tmp_path / "state.json")


def test_optimize_returns_sheets(tmp_path):
    s = state_with([{"name": "side", "length": "30", "width": "23-1/4", "thickness": "3/4", "qty": "2"}, {}])
    r = api(tmp_path).optimize(s)
    assert r["ok"] and r["stats"]["parts"] == 2 and r["sheets"][0]["svg"].startswith("<svg")


def test_bad_cells_are_reported_by_position(tmp_path):
    s = state_with(
        [{"name": "a", "length": "abc", "width": "10", "thickness": "3/4"}, {"name": "b", "length": "10", "width": "", "thickness": "3/4"}],
        [{"name": "ply", "length": "abc", "width": "48", "thickness": "3/4"}],
    )
    r = api(tmp_path).optimize(s)
    got = {(e["table"], e["index"], e["field"]) for e in r["errors"]}
    assert got == {("parts", 0, "length"), ("parts", 1, "width"), ("stock", 0, "length")}


def test_blank_rows_are_ignored(tmp_path):
    r = api(tmp_path).optimize(state_with([{}, {}]))
    assert r["ok"] and r.get("empty")


def test_convert_units_round_trip(tmp_path):
    a = api(tmp_path)
    s = state_with([{"name": "side", "length": "23-5/8", "width": "600mm", "thickness": "23/32", "qty": "1"}])
    mm = a.convert_units(s, "mm")
    assert mm["parts"][0]["length"] == "600.075" and mm["parts"][0]["width"] == "600"
    assert mm["settings"]["kerf"] == "3.175"
    back = a.convert_units(mm, "in")
    assert back["parts"][0]["length"] == "23-5/8" and back["parts"][0]["thickness"] == "23/32"


def test_import_onshape_bom_rows(tmp_path):
    text = "Item,Quantity,Name,Title 1\n1,2,side,762 x 590.55 x 19.05 mm sheet grain=L\n2,8,screw,\n"
    r = api(tmp_path).import_parts(text, "in", 16)
    assert r["source"] == "onshape" and r["skipped"] == ["screw"]
    assert r["rows"] == [{"name": "side", "length": "30", "width": "23-1/4", "thickness": "3/4", "qty": "2",
                          "grain": "length", "tag": "", "kind": "sheet"}]


def test_state_round_trip(tmp_path):
    a = api(tmp_path)
    s = state_with([{"name": "x", "length": "1", "width": "1", "thickness": "1"}])
    a.save_state(s)
    assert a.load_state()["parts"][0]["name"] == "x"


def test_tag_aliases_apply_when_optimizing(tmp_path):
    s = state_with(
        [{"name": "a", "length": "10", "width": "10", "thickness": "3/4", "tag": "Brich"}],
        [{"name": "birch ply", "length": "96", "width": "48", "thickness": "3/4", "tag": "birch"}],
    )
    s["tag_aliases"] = {"brich": "birch"}
    r = api(tmp_path).optimize(s)
    assert r["ok"] and "birch ply" in r["sheets"][0]["title"]


def test_saved_cut_list_is_reused_until_inputs_change(tmp_path):
    a = api(tmp_path)
    s = state_with([{"name": "side", "length": "30", "width": "20", "thickness": "3/4", "qty": "5"}])
    first = a.optimize(s)
    s = a.convert_units(s, "mm")  # display only
    s["result"] = first["plan"]
    assert a.optimize(s)["plan"] == first["plan"]
    # a different layout for sheet 1 is kept as long as nothing else changes
    assert first["sheets"][0]["option"] == 0 and first["sheets"][0]["options"] > 1
    other = a.choose(s, 1, 2)
    assert other["ok"] and other["plan"]["sheets"] != first["plan"]["sheets"] and other["sheets"][0]["option"] == 2
    s["result"] = other["plan"]
    assert a.optimize(s)["plan"] == other["plan"]
    s["parts"][0]["qty"] = "6"
    assert a.optimize(s)["plan"]["key"] != first["plan"]["key"]
