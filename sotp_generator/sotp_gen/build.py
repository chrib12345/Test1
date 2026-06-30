"""Assemble the full 11-sheet workbook from a (normalized) config."""
from openpyxl import Workbook
from . import sheets_engine as E
from . import sheets_narrative as N
from .model import normalize

# Sheet order matches the SPGI template.
SHEET_ORDER = [
    ("Cover", N.build_cover),
    ("Build Spec", N.build_buildspec),
    ("Valuation Summary", E.build_valuation_summary),
    ("Assumptions", E.build_assumptions),
    ("SOTP", E.build_sotp),
    ("FCF Build", E.build_fcf),
    ("Sensitivity", E.build_sensitivity),
    ("Comps", E.build_comps),
    ("Recent Trends", N.build_trends),
    ("Street Coverage", N.build_street),
    ("Spin Mechanics", N.build_spin),
]


def build_workbook(cfg):
    """Return an openpyxl Workbook for the given config dict."""
    c = normalize(cfg)
    wb = Workbook()
    wb.remove(wb.active)
    # Comps must build before SOTP's reverse-block reads CMP slope cells, but
    # openpyxl just stores formula strings, so creation order is cosmetic; we
    # build in template order and pre-seed CMP regression cell refs lazily.
    sheets = {}
    for name, _ in SHEET_ORDER:
        sheets[name] = wb.create_sheet(name)
    # Build engine sheets that populate cross-references first.
    E.build_comps(sheets["Comps"], c)
    E.build_assumptions(sheets["Assumptions"], c)
    E.build_sotp(sheets["SOTP"], c)
    E.build_valuation_summary(sheets["Valuation Summary"], c)
    E.build_sensitivity(sheets["Sensitivity"], c)
    E.build_fcf(sheets["FCF Build"], c)
    N.build_cover(sheets["Cover"], c)
    N.build_buildspec(sheets["Build Spec"], c)
    N.build_trends(sheets["Recent Trends"], c)
    N.build_street(sheets["Street Coverage"], c)
    N.build_spin(sheets["Spin Mechanics"], c)
    return wb
