# Changelog

All notable changes to the public API (`banxopen.sheetmetal`, `banxopen.sheetmetal.nx`). Versioning is semantic, measured on the public API.

## [1.1.0] - 2026-10-28

Found while porting the two UI tools: two NX operations the C# dialogs made themselves now sit behind the facade.

### Changed
- `SheetMetalServices.set_preferences(row)` holds its own undo mark ("Set Sheet Metal Preferences"); a caller no longer
  needs one.
- `BeadTool.selection_changed` leaves out selected objects NX reports as deleted (a sketch drawn on the fly and then
  cancelled). This was the C# bead dialog's `BlockAccessor.NotDeleted`; the UF call is `uf_modl.is_deleted` [verify].

## [1.0.0] - 2026-10-28

### Added
- Python port of C# BANxOpen.SheetMetal for NX 2412 (CPython 3.11).
- The tool facades. The decisions the C# dialogs made themselves now live in the library:
  - `BeadTool` returns a `BeadView` on every gesture;
  - `SheetMetalAssignmentSupport` serves the Material Assignment dialog.
- `SheetMetalServices.create` builds:
  - the material rule modules for `MaterialEngine.create`;
  - `set_preferences(row)`;
  - `read_part_preference()`.
- A SPEC workbook reader that uses the standard library only, with no third-party runtime dependency.
