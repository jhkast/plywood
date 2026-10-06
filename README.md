# plywood

Cut list optimizer for sheet goods and lumber, built for table saw, track saw and miter saw work. Every cut is a guillotine cut, and each sheet comes with a cut order you can follow.

A personal project, shared as is under the [MIT license](LICENSE): no support, and no promise of updates.

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

**Parts CSV** has the columns `name,length,width,thickness,qty,grain,material`.
- Lengths can be written as `23-5/8`, `600mm` or `8'`. Bare numbers use `--units` (in or mm).
- `grain` is blank (free to rotate), `length`, or `width`.
- `material` (or `tag`) is optional free text. A part with a material only uses stock of the same material.

**Stock CSV** has the columns `length,width,thickness,qty,trim,kind,rough,material`.
- `length` runs along the grain.
- `qty` blank or 0 means buy as needed. A number means that many pieces are on hand, and on-hand pieces are used first.
- `trim` lists the edges to trim: `l`/`r` are the ends, `b`/`t` the long sides (e.g. `lr`).
- Stock is labeled by its thickness, material and size, e.g. `3/4" birch ply · 96" × 48"`.
- `kind` is `sheet`, `dimensional` (2x4, 1x6…) or `hardwood` (older files' `board` reads as hardwood).
- Dimensional stock to buy with no length comes in whichever of the standard lengths (8', 10', 12', a setting) wastes least.
- `rough` (`yes` or blank) marks rough-sawn hardwood. Every part from it gets jointed and planed.
- A sheet part matches a sheet of its thickness, within 0.5 mm.
- A lumber part can come from a board up to 1/4" thicker, which gets planed down. Exact thickness is preferred. A rough board has to be at least 1/8" thicker, and can be as thick as the standard rough thickness for the part (8/4 for 1-1/2" legs).
- Planed segments get 4" of snipe allowance at each end and are at least 18" long.
- Thicknesses with no matching stock get unlimited 4×8 sheets, unless you pass `--no-default-sheets`. Dimensional parts with no stock get every standard size that fits.
- Hardwood parts with no board go on the **shopping list** instead of the cut list: per thickness (4/4, 5/4, 6/4, 8/4…) and species, the rough blanks to find (part + snipe + jointing), board feet, and spares. Enter the boards you buy as on-hand hardwood to get their cut list.

**Parts CSV** also has a `kind` column: `sheet` (the default), `dimensional` or `hardwood`. A part is only ever cut from stock of the same kind.

## From Onshape

1. **Set up the feature (once).**
   - In any document, create a Feature Studio.
   - Keep the two header lines Onshape generates, and paste in everything after the header from [featurescript/cut_list_dims.fs](featurescript/cut_list_dims.fs).
   - Add the feature to your toolbar.
2. **Tag the parts.** In each Part Studio, add **Cut list dims** at the end of the feature list and select the parts. Choose Sheet, Dimensional or Hardwood and a grain lock. Leave Material blank to use each part's Onshape material, or type one to override it. Use one feature for each group of settings.
3. **Export the BOM.** In the assembly's BOM, add a **Title 1** column, then export the BOM as CSV.
4. **Run it.** Point `PARTS` in `run.py` at the exported CSV. It's detected automatically, and screws and other hardware are skipped.

## Shopping list on your phone

1. In the app: **Export ▾ → Shopping list for phone**. Scan the QR code with your phone (or copy the link).
2. The phone page lists the stock to buy (tap to check off) and the hardwood pieces to find, with the parts each one is for.
3. At the yard, add each board you pick up (length × width, thickness). The phone re-plans and shows what's still needed, or **You have enough**.
4. Back home, tap **Send boards to computer**, and in the app use **Import ▾ → Boards from phone** with that link. The boards become on-hand stock.

Open the link once at home: after that the page works offline ("Ready offline" at the bottom). **Add to Home Screen** gives it an icon.

The page is static files, and the optimizer runs in the phone's browser (Pyodide), so it needs no server, just a host with HTTPS. Build it with:

```bash
uv run python -m plywood.app.webbuild
```

Every push to `main` builds it and publishes it to GitHub Pages ([.github/workflows/pages.yml](.github/workflows/pages.yml)), at https://jhkast.github.io/plywood/shop/, which the app uses by default. To host it elsewhere, put `dist/web` on any static host and paste the address of its `shop/` page into the app's QR dialog.

## Tests

```bash
uv run pytest
```
