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
    assert mm["settings"]["table_saw_kerf"] == "3.175"
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
    assert r["ok"] and r["sheets"][0]["title"].startswith('#1 · 3/4" birch · 96" × 48"')


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


def test_boards_through_the_app(tmp_path):
    a = api(tmp_path)
    board = {"kind": "board"}
    s = state_with(
        [dict(board, name="rail", length="30", width="3", thickness="3/4", qty="3"),
         dict(board, name="leg", length="29", width="1-1/2", thickness="1-1/2", qty="2")],
        [dict(board, name="cherry", length="80", width="7", thickness="1", qty="1", rough=True)],
    )
    r = a.optimize(s)
    # The 1" board makes the rails; the 1-1/2" legs go on the shopping list.
    assert r["ok"] and r["stats"]["parts"] == 3 and r["stats"]["find"] == 2
    assert "joint &amp; plane" in r["sheets"][0]["steps"] and "8/4 hardwood" in r["shopping"]
    s["result"] = r["plan"]
    sheet = next(x for x in r["sheets"] if x["options"] > 1)
    other = a.choose(s, sheet["number"], 1)
    assert other["ok"] and other["sheets"][sheet["number"] - 1]["option"] == 1


def test_opening_keeps_the_saved_cut_list_even_after_an_update(tmp_path):
    a = api(tmp_path)
    s = state_with([{"name": "side", "length": "30", "width": "20", "thickness": "3/4", "qty": "5"}])
    first = a.optimize(s)
    s["result"] = dict(first["plan"], key="from-an-older-version")  # fingerprint no longer matches
    kept = a.optimize(s, True)
    assert kept["ok"] and kept["plan"]["sheets"] == first["plan"]["sheets"]
    assert kept["plan"]["key"] == first["plan"]["key"]  # refreshed, so later runs match
    # If it truly doesn't fit any more, say so instead of quietly recalculating.
    s["parts"][0]["length"] = "60"
    stale = a.optimize(s, True)
    assert not stale["ok"] and stale["stale"]


SAWS = ("track_saw_kerf", "table_saw_kerf", "miter_saw_kerf", "jig_saw_kerf")


def test_old_single_kerf_becomes_every_saw(tmp_path):
    import json

    path = tmp_path / "state.json"
    path.write_text(json.dumps({"settings": {"kerf": "1/2"}}))
    st = Api(path).load_state()["settings"]
    assert [st[k] for k in SAWS] == ["1/2"] * 4
    assert "kerf" not in st


def test_kerf_per_cut_becomes_kerf_per_saw(tmp_path):
    import json

    old = {"sheet_kerf": "1/16", "rip_kerf": "1/8", "crosscut_kerf": "3/32", "rough_crosscut_kerf": "1/4"}
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"settings": old}))
    st = Api(path).load_state()["settings"]
    assert [st[k] for k in SAWS] == ["1/16", "1/8", "3/32", "1/4"] and st["sheet_saw"] == "track"
    assert not set(old) & set(st)


def test_saws_map_to_cuts(tmp_path):
    from plywood.app.api import parse_state
    from plywood.core.units import parse_length

    s = state_with([{"name": "a", "length": "10", "width": "10", "thickness": "3/4"}])
    s["settings"].update(track_saw_kerf="1/16", table_saw_kerf="1/8", miter_saw_kerf="3/32", jig_saw_kerf="1/4")
    _, _, st, _, _ = parse_state(s)
    inch = parse_length("1", "in")
    assert (st.sheet_kerf, st.rip_kerf, st.crosscut_kerf, st.rough_crosscut_kerf) == (inch / 16, inch / 8, inch * 3 / 32, inch / 4)
    s["settings"]["sheet_saw"] = "table"
    assert parse_state(s)[2].sheet_kerf == inch / 8


def test_on_hand_zero_means_buy_as_needed(tmp_path):
    from plywood.app.api import parse_state

    s = state_with([{"name": "a", "length": "10", "width": "10", "thickness": "3/4"}],
                   [{"length": "96", "width": "48", "thickness": "3/4", "qty": q} for q in ("0", "", "2")])
    _, stocks, _, _, errors = parse_state(s)
    assert not errors and [st.qty for st in stocks] == [None, None, 2]


def test_stock_label_and_old_names_ignored(tmp_path):
    s = state_with([{"name": "a", "length": "10", "width": "10", "thickness": "3/4", "tag": "birch ply"}],
                   [{"name": "my old name", "length": "96", "width": "48", "thickness": "3/4", "tag": "birch ply", "qty": "1"}])
    r = api(tmp_path).optimize(s)
    assert r["sheets"][0]["title"] == '#1 · 3/4" birch ply · 96" × 48" (on hand)'
    assert "my old name" not in r["shopping"]


def test_hardwood_shopping_list(tmp_path):
    s = state_with([{"name": "leg", "length": "29", "width": "1-1/2", "thickness": "1-1/2", "qty": "4", "kind": "hardwood", "tag": "maple"}])
    r = api(tmp_path).optimize(s)
    assert r["ok"] and r["sheets"] == [] and r["stats"]["find"] == 4
    # 29" + 4" snipe and a crosscut at each end; 1-1/2" + 1/16" jointed; 10% spare of 4 = 1
    assert "<h4>8/4 maple</h4>" in r["shopping"]
    assert '<td>4 + 1 spare</td><td>at least 37-1/4" × 1-9/16"</td><td>4 × leg</td>' in r["shopping"]
    assert "about 5 (4.0 + 25%) bd ft" in r["shopping"]


def test_extras_dont_change_the_layout(tmp_path):
    from plywood.app.api import inputs_key, parse_state

    s = state_with([{"name": "a", "length": "10", "width": "10", "thickness": "3/4"}])
    key = inputs_key(*parse_state(s)[:3])
    s["settings"].update(spare_sheets="2", spare_pct="50", waste_pct="10")
    assert inputs_key(*parse_state(s)[:3]) == key


def test_dimensional_stock_without_length_buys_the_best_length(tmp_path):
    s = state_with([{"name": "rail", "length": "50", "width": "3-1/2", "thickness": "1-1/2", "qty": "2", "kind": "dimensional"}],
                   [{"length": "", "width": "3-1/2", "thickness": "1-1/2", "kind": "dimensional"}])
    s["settings"]["default_dimensional"] = False
    r = api(tmp_path).optimize(s)
    assert r["ok"] and [x["title"] for x in r["sheets"]] == ["#1 · 2x4 · 10' (buy)"]
    assert "<td>2x4 · 10&#x27;</td><td>1 + 1 spare</td>" in r["shopping"] and r["stats"]["shop"] == 2


def test_old_board_rows_become_hardwood(tmp_path):
    import json

    path = tmp_path / "state.json"
    path.write_text(json.dumps({"parts": [{"name": "a", "kind": "board"}], "stock": [{"length": "1", "kind": "board"}],
                                "settings": {"board_width": "6", "default_boards": True}}))
    st = Api(path).load_state()
    assert st["parts"][0]["kind"] == st["stock"][0]["kind"] == "hardwood"
    assert "board_width" not in st["settings"] and "default_boards" not in st["settings"]


def test_stock_csv_material_column_and_zero_qty(tmp_path):
    r = api(tmp_path).import_stock("length,width,thickness,qty,material,kind\n96,48,3/4,0,birch ply,sheet\n80,7,1,2,cherry,board\n")
    assert r["ok"]
    assert [(row["qty"], row["tag"], row["kind"]) for row in r["rows"]] == [("", "birch ply", "sheet"), ("2", "cherry", "hardwood")]
