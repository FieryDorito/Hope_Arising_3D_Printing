# Adding a part to the library

1. **Add a row to the Google Sheet** ("Parts Library Sources"). Fill in every column.
   `commercial_use_ok` must be `YES`, or the app won't let anyone print the part.
2. **Find its part_id.** Take the part name, lowercase it, and replace anything that isn't a letter or number with `-`.
   - `Sturdy wall hook` → `sturdy-wall-hook`
   - `Desk Organizer / Plant Pot Cover` → `desk-organizer-plant-pot-cover`

   If you rename the part in the sheet, its id changes and the files need a new folder name.
   To avoid that, add a `part_id` column to the sheet; the app uses that column when it exists.
3. **Create the folder** `models/<Category>/<part_id>/` (e.g. `models/Household/sturdy-wall-hook/`) and put the model inside it:
   The category folder uses the standard spelling (Household, Plumbing, Agricultural, Utility), even if the sheet has a typo.

   | You have | Put in the folder | What the operator can change |
   |---|---|---|
   | STL / 3MF / OBJ only | `model.stl` | Overall size (%) |
   | OpenSCAD source | `model.scad` + `part.yaml` | Each dimension you list |

   Export the STL in the orientation it should print in, flat side down. The app doesn't rotate parts.
   For FreeCAD/Fusion/STEP files, export an STL, or rewrite simple parts in OpenSCAD.
4. **Run the tests:** `python -m pytest`. Every part with a model is sliced automatically,
   and a part that doesn't produce valid G-code fails the test.
5. Commit and push. GitHub runs the same tests.
6. On the shop computer, press **Update library**. The thumbnail picture is made automatically.

## part.yaml (adjustable parts)

```yaml
description: Round spacer / washer with a hole through the middle.   # helps the AI choose
params:
  - {name: outer_diameter, label: Outside diameter, default: 20, min: 6, max: 80, unit: mm, step: 0.5}
  - {name: inner_diameter, label: Hole diameter,    default: 8,  min: 2, max: 70, unit: mm, step: 0.5}
  - {name: height,         label: Height,           default: 10, min: 1, max: 60, unit: mm, step: 0.5}
```

- `name` must match the variable name in `model.scad` exactly.
- Set `min`/`max` to sizes that **actually print well**. The AI can't go outside them.
- Optional fields: `help` (tooltip text) and `kind` (`int` or `bool`).

For STL parts, `part.yaml` is optional. Use it to add a `description` or limit the size range:

```yaml
description: Strong hook for heavy bags; screws to the wall.
scale: {min: 80, max: 150}
```
