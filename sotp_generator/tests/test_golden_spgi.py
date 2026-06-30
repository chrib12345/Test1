"""Golden test: regenerate the SPGI model from configs/SPGI.yaml, evaluate the
formulas in-process (via the `formulas` library), and assert the computed
outputs tie out to the uploaded template's cached values.

Run:  python tests/test_golden_spgi.py    (or: pytest tests/)
"""
import os
import sys
import tempfile
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from sotp_gen.build import build_workbook       # noqa: E402
from sotp_gen.model import load_yaml            # noqa: E402

# (sheet, cell): expected value — lifted from the template's cached results.
EXPECTED = {
    ("SOTP", "B31"): 396.963101136333,
    ("SOTP", "C31"): 485.452797333338,
    ("SOTP", "D31"): 564.139494139529,
    ("SOTP", "C33"): 0.213146734639488,
    ("SOTP", "B38"): 454.924056451982,
    ("SOTP", "B39"): 30.5287408813559,
    ("SOTP", "B40"): 485.452797333338,
    ("SOTP", "B46"): 15.9175223850625,
    ("SOTP", "B48"): 13.9305058497877,
    ("Valuation Summary", "B8"): 396.963101136333,
    ("Valuation Summary", "D8"): 564.139494139529,
    ("Valuation Summary", "H18"): 436.976703231643,
    ("Valuation Summary", "I18"): 533.928891435033,
    ("Sensitivity", "B6"): 436.976703231643,
    ("Sensitivity", "J14"): 533.928891435033,
    ("Sensitivity", "F10"): 485.452797333338,
    ("FCF Build", "B11"): 8457.43576,
    ("FCF Build", "B17"): 5889.6998928,
    ("FCF Build", "B18"): 19.9650843823729,
    ("Street Coverage", "B9"): 0.344462215113954,
    ("Street Coverage", "B22"): 21.07596371702,
    ("Comps", "K10"): 22.8960630136986,   # Moody's EV/EBITDA in generated layout
    ("Assumptions", "B40"): 13318,
    ("Assumptions", "B49"): 12058,
}


def _evaluate(path):
    import formulas
    warnings.filterwarnings("ignore")
    sol = formulas.ExcelModel().loads(path).finish().calculate()
    base = os.path.basename(path).upper()

    def get(sheet, cell):
        suf = f"]{sheet.upper()}'!{cell.upper()}"
        for k, v in sol.items():
            if k.endswith(suf):
                try:
                    return v.value[0, 0]
                except Exception:
                    return v.value
        raise KeyError(f"{sheet}!{cell} not found")
    return get


def test_golden_spgi():
    cfg = load_yaml(os.path.join(ROOT, "configs", "SPGI.yaml"))
    wb = build_workbook(cfg)
    assert len(wb.sheetnames) == 11, wb.sheetnames
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "SPGI_TEST.xlsx")
        wb.save(path)
        get = _evaluate(path)
        failures = []
        for (sheet, cell), exp in EXPECTED.items():
            got = get(sheet, cell)
            if got is None or abs(float(got) - exp) > 1e-4:
                failures.append(f"{sheet}!{cell}: got={got} exp={exp}")
        assert not failures, "Golden mismatches:\n" + "\n".join(failures)


if __name__ == "__main__":
    test_golden_spgi()
    print(f"PASS — {len(EXPECTED)} cells across 7 sheets tie out to the SPGI template.")
