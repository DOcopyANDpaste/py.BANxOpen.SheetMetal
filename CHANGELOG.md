# Changelog

All notable changes to the public API (`banxopen.sheetmetal`, `banxopen.sheetmetal.nx`). Versioning is semantic, measured on the public API.

## [Unreleased]

### Changed
- **Breaking:** targets NX 2606 (Designcenter) and its embedded CPython 3.12. `requires-python` is now `==3.12.*` (was `==3.11.*`), and the code uses 3.12+ syntax (type-parameter generics such as `class OperationResult[T]`), so it no longer runs on NX 2412's Python 3.11.
- Every module starts with `from __future__ import annotations`, enforced by ruff (`required-imports`). Python 3.12 evaluates annotations as soon as the code is run, so a class naming itself in an annotation, or a name imported only under `TYPE_CHECKING`, raised `NameError` on import (for example `from banxopen.foundation.nx import NxSessionContext`). Multi-exception `except` clauses are parenthesised again (`except A, B:` needs Python 3.14).

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
