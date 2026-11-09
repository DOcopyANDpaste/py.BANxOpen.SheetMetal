"""NX smoke test for BANxOpen SheetMetal. Confirms the [verify] items: the UF call shapes in nx_adapters/uf_modl.py,
the bead feature type name, and the preference reads.

Run in NX 2412 with a sheet metal part open (File > Execute > Journal, Alt+F8). The Foundation, Materials and SheetMetal
src folders must be importable: this script adds them when run from the repo.

Read-only:
  1. SheetMetalServices.create (config root, standards file, bead settings)
  2. Standards and the part's Sheet Metal Preferences
  3. every feature's FeatureType (is "BEAD" what NX calls a bead?), and each bead's stamp and geometry
  4. UF curve end points on the first bead's section curve, and the direction-arrow face normal
  5. the bead tool's view on open (status, blocking, warnings), then closed
Nothing is committed: the requirement sync on open runs inside an undo scope this script never commits.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for src in (_REPO / "src", _REPO.parent / "BANxOpen.Foundation" / "src", _REPO.parent / "BANxOpen.Materials" / "src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

import NXOpen  # noqa: E402
import NXOpen.Features  # noqa: E402

from banxopen.foundation.nx import NxSessionContext, UndoScope, install_nx_logging  # noqa: E402
from banxopen.sheetmetal import BeadControls  # noqa: E402
from banxopen.sheetmetal.nx import BeadTool, SheetMetalServices  # noqa: E402
from banxopen.sheetmetal.nx_adapters import uf_modl  # noqa: E402
from banxopen.sheetmetal.nx_adapters.beads import BeadAttributes, is_bead_feature, read_bead_geometry  # noqa: E402


def main() -> None:
    session = NXOpen.Session.GetSession()
    lw = session.ListingWindow
    lw.Open()
    say = lw.WriteLine
    try:
        install_nx_logging(session)
        started = NxSessionContext.try_initialize()
        if not started.ok or started.value is None:
            say(f"[0] FAILED: {started.message}")
            return
        ctx = started.value

        created = SheetMetalServices.create(ctx)
        if not created.ok or created.value is None:
            say(f"[1] services FAILED: {created.error_code}: {created.message}")
            return
        services = created.value
        table = services.table
        say(f"[1] root={services.locator.root_directory} table={table.source_path} rows={len(table.rows)}")

        standards = services.spec_cache.list_standards()
        say(f"[2] standards: {[s.id for s in standards]}")
        say(f"    preferences: {services.read_part_preference()}")

        say("[3] features:")
        beads = []
        for feature in ctx.work_part.Features:
            kind = feature.FeatureType
            say(f"      {feature.JournalIdentifier}: FeatureType={kind!r} bead={is_bead_feature(feature)}")
            if is_bead_feature(feature):
                beads.append(feature)
        for bead in beads[:5]:
            say(f"      {bead.JournalIdentifier}: stamp={BeadAttributes.read_stamp(bead)}")
            say(f"        geometry={read_bead_geometry(ctx, bead, 0.0)}")

        if beads:
            curve = next((c for s in beads[0].GetSections() for c in s.GetOutputCurves()), None)
            if curve is not None:
                say(f"[4] AskCurveProps raw: {ctx.uf.Modl.AskCurveProps(curve.Tag, 0.0)!r}")
                start, end = uf_modl.curve_point(ctx.uf, curve.Tag, 0.0), uf_modl.curve_point(ctx.uf, curve.Tag, 1.0)
                say(f"    start={start} end={end}")
                deleted = uf_modl.is_deleted(ctx.uf, curve.Tag)
                say(f"    AskStatus raw: {ctx.uf.Obj.AskStatus(curve.Tag)!r} is_deleted={deleted} (expect False)")
                bodies = services.preferences.sheet_metal_bodies()
                if bodies:
                    point = uf_modl.curve_point(ctx.uf, curve.Tag, 0.0)
                    normal = uf_modl.nearest_face_normal(ctx.uf, [f.Tag for f in bodies[0].GetFaces()], point)
                    say(f"    face normal at start: {normal}")

        with UndoScope(ctx.session, "BANxOpen sheet metal smoke (rolled back)"):
            tool = BeadTool.create(services)
            view = tool.open(BeadControls(("B1005010",), "B1005010"))
            say(f"[5] status:\n{view.status}")
            say(f"    blocking: {view.blocking}")
            say(f"    warnings: {view.warnings}")
            say(f"    spec rows: {[r.spec_id for r in view.spec_rows]}")
            tool.close()
        say("    rolled back.")
        say("Smoke test finished.")
    except Exception:  # report everything: this is a diagnostic journal
        say("Smoke test crashed:\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
