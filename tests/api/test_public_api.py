"""The public API imports without NX, and the NX API resolves lazily."""

from __future__ import annotations

import importlib

import pytest

import banxopen.sheetmetal as sheetmetal


def test_every_exported_name_exists():
    missing = [name for name in sheetmetal.__all__ if not hasattr(sheetmetal, name)]
    assert not missing


def test_the_nx_api_imports_without_nx_and_lists_its_names():
    nx = importlib.import_module("banxopen.sheetmetal.nx")
    assert set(nx.__all__) == {"BeadTool", "BlockContents", "SheetMetalServices", "create_curve_section"}


def test_an_unknown_nx_name_is_an_attribute_error():
    nx = importlib.import_module("banxopen.sheetmetal.nx")
    with pytest.raises(AttributeError):
        _ = nx.NotAThing


def test_the_tool_facades_are_pure():
    assert sheetmetal.BeadView.__module__ == "banxopen.sheetmetal.core.tools.bead_tool"
    assert sheetmetal.SheetMetalAssignmentSupport.__module__ == "banxopen.sheetmetal.core.tools.assignment_support"
