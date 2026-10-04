# plywood

Cut list optimizer for sheet goods and lumber, built for table saw, track saw and miter saw work. Every cut is a guillotine cut, and each sheet comes with a cut order you can follow.

## App

Open [app.py](app.py) in VS Code and press ▶, or pick **Plywood app** in Run and Debug.

- **Parts and stock tables** work like a spreadsheet. You can type sizes as `23-5/8`, `600mm` or `8'`, and paste rows from Excel or Sheets.
- **Results update as you edit.** Every sheet is drawn at the same scale, with the cut steps underneath.
- **Your stock list and current job are saved automatically** to `%APPDATA%\plywood\state.json`.
- **Jobs** can be saved and opened as `.json` files.
- **Import CSV / Onshape BOM…** loads a parts CSV or an Onshape BOM export.
- **Print report** opens a printable report in your browser.

## Command line

```bash
uv run plywood optimize examples/cabinet_parts.csv --stock examples/stock.csv
```

This writes `out/report.html` (printable diagrams and cut steps), `out/cutlist.csv`, and one SVG per sheet.

## Inputs

**Parts CSV** has the columns `name,length,width,thickness,qty,grain,tag`.
- Lengths can be written as `23-5/8`, `600mm` or `8'`. Bare numbers use `--units` (in or mm).
- `grain` is blank (free to rotate), `length`, or `width`.
- `tag` is optional free text. A tagged part only uses stock with the same tag.

**Stock CSV** has the columns `name,length,width,thickness,qty,tag,kind,rough`.
- `length` runs along the grain.
- `qty` blank means buy as needed. A number means that many pieces are on hand, and on-hand pieces are used first.
- `kind` is `sheet` or `board`.
- `rough` (`yes` or blank) marks a rough-sawn board. Every part from it gets jointed and planed.
- A sheet part matches a sheet of its thickness, within 0.5 mm.
- A board part can come from a board up to 1/4" thicker, which gets planed down. A rough board has to be at least 1/8" thicker.
- Planed segments get 4" of snipe allowance at each end and are at least 18" long.
- Thicknesses with no matching stock get unlimited 4×8 sheets, unless you pass `--no-default-sheets`.
- Board parts with no matching board get rough "boards to find" in the next standard thickness (4/4, 5/4, 6/4, 8/4…). The report lists them with board feet.

**Parts CSV** also has a `kind` column: `sheet` (the default) or `board`. A part is only ever cut from stock of the same kind.

## From Onshape

1. **Set up the feature (once).**
   - In any document, create a Feature Studio.
   - Keep the two header lines Onshape generates, and paste in everything after the header from [featurescript/cut_list_dims.fs](featurescript/cut_list_dims.fs).
   - Add the feature to your toolbar.
2. **Tag the parts.** In each Part Studio, add **Cut list dims** at the end of the feature list and select the parts. Choose Sheet or Board, a grain lock, and a tag if you need one. Use one feature for each group of settings.
3. **Export the BOM.** In the assembly's BOM, add a **Title 1** column, then export the BOM as CSV.
4. **Run it.** Point `PARTS` in `run.py` at the exported CSV. It's detected automatically, and screws and other hardware are skipped.

## Tests

```bash
uv run pytest
```
