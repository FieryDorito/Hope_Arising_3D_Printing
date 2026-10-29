# Adding parts to the library

## The easy way (in the app)

1. Add a row to the Google Sheet ("Parts Library Sources") and fill in every column.
   `commercial_use_ok` must be `YES`, or the app won't let anyone print the part.
2. Start the app. It checks the sheet when it starts, and a pop-up lists new or changed parts,
   each with its download link.
3. Download **everything** from the part's page (a .zip is fine), drag it into the box,
   and press **Save these files**. Zips are unpacked automatically.
4. If the part came with several files, open **Library setup** → *Several files — choose options*:
   - Pick **pieces of one product** (the worker ticks which pieces to print) or
     **different versions** (the worker picks exactly one).
   - Give each file a clear name, tick which ones are selected at start, and set how many.
   - Tick **Hide** for files nobody should print (test pieces, old versions).
5. In GitHub Desktop: write a short note → **Commit** → **Push**, so the team and the shop get the files.

You can also skip the pop-up and do all of this later from **Library setup**.

## What the app does with the files

| File | What happens |
|---|---|
| .stl .3mf .obj | Printable piece or option. Operators can scale it (Size %). |
| .scad (OpenSCAD) | Printable and adjustable. Settings are read from the file's Customizer comments automatically. |
| .txt .md .pdf | Shown as **Instructions from the designer**. |
| .png .jpg | Shown as pictures. The first one becomes the library picture. |
| .zip | Unpacked into a folder next to it. |
| .fcstd .step .f3d … | Can't be printed. The app asks for an STL instead. |

- Files can sit in sub-folders, in any layout.
- Each part's files live in `models/<Category>/<part_id>/`. The app creates that folder for you.
- Pieces that don't fit on one bed are split across several print files automatically.
- Long parts are turned diagonally when that makes them fit.

## part.yaml (written by Library setup, safe to edit by hand)

```yaml
description: Small travel salt shaker ...   # helps the AI choose this part
choose: any            # any = pieces of one product; one = pick exactly one version
files:
  spodní část.stl: {label: Bottom (container), default: true, count: 1}
  sítko 5mm.stl:   {label: Sieve – 5 mm holes, default: false, count: 1}
  test-piece.stl:  {label: Test, hide: true}
scale: {min: 80, max: 150}       # optional: limit Size %
params:                          # optional: replaces the settings read from a .scad file
  - {name: outer_diameter, label: Outside diameter, default: 20, min: 6, max: 80, unit: mm}
```

Run `python -m pytest` after adding parts. Every part is sliced at its default settings,
and GitHub runs the same check on every push.
