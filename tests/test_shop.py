import json
from pathlib import Path

from plywood.app import shop
from plywood.app.api import Api, default_state

EXAMPLE = Path(__file__).parents[1] / "examples" / "cherry_table.json"


def cherry_state(extra_parts=()):
    s = default_state()
    job = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    s["settings"].update(job["settings"])
    s["parts"] = job["parts"] + [dict(p) for p in extra_parts]
    s["settings"]["tries"] = 200
    return s


def job_from(state, tmp_path):
    r = Api(tmp_path / "state.json").phone_job(state)
    assert r["ok"]
    return shop.decode(r["code"])


def test_code_round_trip_and_links():
    data = {"v": 3, "job": "Table ½", "boards": [{"length": "8'", "width": "7"}]}
    code = shop.encode(data)
    assert set(code) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert shop.decode(code) == data
    assert shop.decode("https://example.com/shop/#b=" + code) == data


def test_job_link_is_small(tmp_path):
    r = Api(tmp_path / "state.json").phone_job(cherry_state())
    assert len(r["code"]) < 1500  # comfortably inside a QR code


def test_payload_has_board_parts_only_and_the_shopping_list(tmp_path):
    side = {"name": "side", "length": "30", "width": "20", "thickness": "3/4", "qty": "2", "kind": "sheet", "tag": "birch ply"}
    job = job_from(cherry_state([side]), tmp_path)
    assert {p[0] for p in job["parts"]} == {"leg", "apron", "short apron", "top slat"}
    assert job["buy"] == [{"label": '3/4" birch ply · 96" × 48"', "count": 1, "spares": 0}]
    assert [g["label"] for g in job["find"]] == ["4/4 cherry", "8/4 cherry"]
    legs = next(g for g in job["find"] if g["label"] == "8/4 cherry")
    assert legs["blanks"] == [{"count": 1, "size": '95-1/2" × 4"', "parts": "4 × leg, 1 × leg (spare)", "spares": {"leg": 1}}]


def test_replan_with_empty_cart_matches_the_desktop(tmp_path):
    job = job_from(cherry_state(), tmp_path)
    r = shop.replan(job, [])
    assert r["ok"] and r["find"] == job["find"] and not r["enough"]


def test_cart_boards_cover_parts_until_enough(tmp_path):
    job = job_from(cherry_state(), tmp_path)
    one = {"length": "8'", "width": "7", "thickness": "2", "tag": "cherry", "rough": True}
    r = shop.replan(job, [one])
    assert r["ok"] and [g["label"] for g in r["find"]] == ["4/4 cherry"]
    assert r["cart"][0]["parts"] == ['4 × leg · 29" × 1-1/2" × 1-1/2"', '1 × leg (spare) · 29" × 1-1/2" × 1-1/2"']
    thin = {"length": "10'", "width": "8", "thickness": "1", "tag": "cherry", "rough": True}
    r = shop.replan(job, [one, thin, thin, thin])
    assert r["ok"] and r["enough"] and r["find"] == []


def test_replan_reports_bad_sizes(tmp_path):
    job = job_from(cherry_state(), tmp_path)
    r = shop.replan(job, [{"length": "abc", "width": "7", "thickness": "1"}])
    assert not r["ok"] and "board 1" in r["message"]


def test_boards_come_back_as_stock_rows(tmp_path):
    job = job_from(cherry_state(), tmp_path)
    board = {"length": "8'", "width": "7", "thickness": "1", "tag": "cherry", "rough": True}
    code = shop.encode(shop.boards_payload(job, [board, board, {**board, "width": "9-1/2"}]))
    r = Api(tmp_path / "state.json").import_boards("https://x.github.io/plywood/shop/#b=" + code, "mm", 16)
    assert r["ok"]
    assert [(row["length"], row["width"], row["qty"], row["kind"], row["rough"]) for row in r["rows"]] == [
        ("2438.4", "177.8", "2", "hardwood", True),
        ("2438.4", "241.3", "1", "hardwood", True),
    ]


def test_job_link_is_not_boards(tmp_path):
    r = Api(tmp_path / "state.json")
    code = r.phone_job(cherry_state())["code"]
    assert not r.import_boards(code)["ok"]


def test_python_zip_has_what_the_phone_needs():
    import io
    import zipfile

    from plywood.app.webbuild import python_zip

    names = zipfile.ZipFile(io.BytesIO(python_zip())).namelist()
    assert "plywood/app/shop.py" in names and "plywood/core/optimize.py" in names
    assert "plywood/app/desktop.py" not in names and not any("__pycache__" in n for n in names)
