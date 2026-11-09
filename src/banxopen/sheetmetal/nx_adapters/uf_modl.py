"""Every NXOpen.UF call the sheet metal library makes, in one place.

NX 2412 ships no Python stubs for `NXOpen.UF`, so neither pyright nor the tests can check these. Each function states
the Python shape it assumes; `scripts/smoke_sheetmetal.py` prints what NX actually returns. [verify] marks an
assumption not yet confirmed in NX.
"""

# pyright: reportAttributeAccessIssue=false, reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportArgumentType=false, reportMissingImports=false

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

Point = tuple[float, float, float]


def curve_point(uf: Any, curve_tag: Any, parameter: float) -> Point:
    """UF_MODL_ask_curve_props at a normalised parameter (0 start, 1 end). [verify] Python returns
    (point, tangent, p_norm, b_norm, torsion, rad_of_cur) rather than filling out-arrays."""
    result = uf.Modl.AskCurveProps(curve_tag, parameter)
    point = result[0]
    return float(point[0]), float(point[1]), float(point[2])


def nearest_face_normal(uf: Any, face_tags: Sequence[Any], point: Point) -> Point | None:
    """The normal of the face nearest `point`, at the nearest point on it, or None when there are no faces.
    [verify] AskMinimumDist(obj1, obj2=Tag.Null, guess1_given, guess1, guess2_given, guess2) returns
    (distance, pt_on_obj1, pt_on_obj2); AskFaceParm(face, point) returns (parm, face_pnt); AskFaceProps(face, parm)
    returns (point, u1, v1, u2, v2, unit_norm, radii)."""
    import NXOpen

    nearest: tuple[float, Any, Any] | None = None
    for face in face_tags:
        distance, on_face, _ = uf.Modl.AskMinimumDist(face, NXOpen.Tag.Null, 0, [0.0, 0.0, 0.0], 1, list(point))
        if nearest is None or distance < nearest[0]:
            nearest = (distance, face, on_face)
    if nearest is None:
        return None
    _, face, on_face = nearest
    parameter, _ = uf.Modl.AskFaceParm(face, on_face)
    normal = uf.Modl.AskFaceProps(face, parameter)[5]
    return float(normal[0]), float(normal[1]), float(normal[2])


def fail_message(uf: Any, code: int) -> str:
    """UF_get_fail_message. [verify] Python returns the message string."""
    return str(uf.UF.GetFailMessage(code))


def is_deleted(uf: Any, tag: Any) -> bool:
    """UF_OBJ_ask_status is UF_OBJ_DELETED, or NX will not say (treated as deleted). Not "not alive": a temporary
    object, such as the Section a curve block builds while its dialog is open, is not alive but is still in use.
    [verify] Python returns the status as an int."""
    import NXOpen
    import NXOpen.UF

    try:
        return int(uf.Obj.AskStatus(tag)) == NXOpen.UF.UFConstants.UF_OBJ_DELETED
    except NXOpen.NXException:
        return True
