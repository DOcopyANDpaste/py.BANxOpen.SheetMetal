# BANxOpen.SheetMetal (Python)

This library covers bead SPECs, NX's sheet metal material standards file, the Sheet Metal Preferences, and the sheet metal requirements held in the part. It is the Python port of the C# `BANxOpen.SheetMetal` (NX 2412, CPython 3.11), and it depends on [BANxOpen.Foundation](https://github.com/DOcopyANDpaste/py.BANxOpen.Foundation) and [BANxOpen.Materials](https://github.com/DOcopyANDpaste/py.BANxOpen.Materials).

## Public API

| Package | For | Contents |
|---|---|---|
| `banxopen.sheetmetal` (pure) | the bead dialog | `BeadView` and `BeadControls`, the data the dialog renders and the control values it owns |
| | the Material Assignment dialog | `SheetMetalAssignmentSupport`: conflicts between bodies, the body check, the open message, the grade column, the default filter |
| | feature domains | the ports (`BeadSpecLookup`, `FeatureInventory`, ...), the rules and providers, the requirement mapper |
| | everyone | the standards file, preference and SPEC data types; `SheetMetalCodes`, `SheetMetalText` |
| `banxopen.sheetmetal.nx` | entry points | `SheetMetalServices.create`, `BeadTool`, `create_curve_section` |

The Material Assignment dialog wires sheet metal into the material engine like this:

```python
services = SheetMetalServices.create(ctx).value  # after checking .ok: show .message and stop otherwise
engine = MaterialEngine.create(ctx, services.material_modules).value
support = services.assignment_support()
```

The bead dialog runs every gesture through `BeadTool` and renders the `BeadView` it returns:

```python
tool = BeadTool.create(services)
view = tool.open(controls)  # initialize
view = tool.selection_changed(curve_objects, feature_objects, controls)  # dialog shown, and every block update
view = tool.controls_changed(controls, bead_spec_changed=True)  # radio, SPEC tree, direction, preview
applied = tool.apply(controls)  # OperationResult[str]: the message to show
tool.close()  # however the dialog closed
```

The view carries everything the dialog shows:
- the SPEC rows, and which of them are kept for selected beads;
- the selection lines and the preferences label;
- the mode, the blocking reason and the warnings;
- an info box to show once;
- the direction arrow;
- `controls`, with any pre-filled values.

Every decision sits behind these calls, so a rule change needs no tool change.

## Configuration

The sheet metal root is `SHEETMETAL` under `UGII_USER_DIR`, then `UGII_GROUP_DIR`, then `UGII_SITE_DIR`. Each Standard's bead workbooks live beside the standards file:

```text
SHEETMETAL/
  BA_NXOPEN_settings/sheetmetal-settings.json   names the standards file
  BA_NXOPEN_settings/bead-settings.json         optional
  <standards file>
  <Standard>/Features/BEAD/<bead SPEC>.xlsx
```

Samples of the two settings files are in `config-samples/`. Parsed workbooks are cached in `%TEMP%\BANxOpen.SheetMetal\cache`.

## Development

```powershell
uv venv --python 3.11
uv pip install -e ".[dev]"                 # installs ../BANxOpen.Foundation and ../BANxOpen.Materials editable
New-Item -ItemType Junction -Path .\typings -Target "$env:UGII_BASE_DIR\UGOPEN\pythonStubs"

.venv\Scripts\ruff check . ; .venv\Scripts\pyright ; .venv\Scripts\lint-imports ; .venv\Scripts\python -m pytest --cov
```

At runtime the library uses the standard library only; openpyxl is a dev dependency, used to build test workbooks. Every UF call (NX ships no UF stubs) lives in `nx_adapters/uf_modl.py`. To confirm their Python shapes, open a sheet metal part with beads in NX and run `scripts/smoke_sheetmetal.py` (Alt+F8). The script is read-only.
