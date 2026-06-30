"""Config schema, loading and normalization.

A *config* is a plain dict (loaded from YAML or assembled by the data
providers). ``normalize`` fills defaults and derives helpers the builders need
(per-segment column letters, the minority/spin segment indices, sensitivity
axes), so every downstream sheet builder reads a consistent structure.
"""
from __future__ import annotations
import copy
import yaml
from openpyxl.utils import get_column_letter

# First segment lives in column B; segment i -> column index 2 + i.
FIRST_SEG_COL = 2


def load_yaml(path):
    with open(path, "r") as fh:
        return yaml.safe_load(fh)


def _num(d, key, default=0):
    v = d.get(key)
    return default if v is None else v


def _seg_index_by_name(segments, name):
    if not name:
        return None
    for i, s in enumerate(segments):
        if s.get("name") == name or s.get("short") == name:
            return i
    return None


def normalize(cfg):
    """Return a deep-copied, fully-defaulted config with derived fields."""
    c = copy.deepcopy(cfg)
    c.setdefault("meta", {})
    c.setdefault("market", {})
    c.setdefault("corporate", {})
    c.setdefault("net_debt", {})
    c.setdefault("fcf", {})
    c.setdefault("street", {})
    c.setdefault("trends", {})
    c.setdefault("spin", {})
    c.setdefault("buildspec", [])
    segs = c.setdefault("segments", [])

    # --- per-segment derived fields -------------------------------------
    for i, s in enumerate(segs):
        s.setdefault("short", s.get("name", f"Seg{i+1}"))
        s["col"] = get_column_letter(FIRST_SEG_COL + i)
        s.setdefault("growth", 0.0)
        s.setdefault("margin_bps", 0)
        for k in ("fy_revenue", "fy_op_profit", "fy_deprec"):
            s[k] = _num(s, k)
        s.setdefault("mult_bear", s.get("mult_base", 10))
        s.setdefault("mult_base", 10)
        s.setdefault("mult_rerate", s.get("mult_base", 10))
        s.setdefault("comps", [])
        s.setdefault("source_note", "")
        s.setdefault("comp_anchor_note", "")
    c["n_segments"] = len(segs)
    c["seg_cols"] = [s["col"] for s in segs]

    # --- minority & spin segment indices --------------------------------
    corp = c["corporate"]
    c["minority_idx"] = _seg_index_by_name(segs, corp.get("minority_segment"))
    corp.setdefault("minority_pct", 0.0)
    spin = c["spin"]
    spin.setdefault("enabled", bool(spin.get("segment")))
    c["spin_idx"] = _seg_index_by_name(segs, spin.get("segment")) if spin.get("enabled") else None
    spin.setdefault("net_debt", 0)

    # --- sensitivity axes (default: two largest base-EV segments) -------
    sens = c.setdefault("sensitivity", {})
    if segs and (not sens.get("row_segment") or not sens.get("col_segment")):
        ranked = sorted(
            range(len(segs)),
            key=lambda i: (segs[i]["fy_op_profit"] + segs[i]["fy_deprec"]) * segs[i]["mult_base"],
            reverse=True,
        )
        ri = _seg_index_by_name(segs, sens.get("row_segment"))
        ci = _seg_index_by_name(segs, sens.get("col_segment"))
        if ri is None:
            ri = ranked[0]
        if ci is None:
            ci = next((i for i in ranked if i != ri), ri)
        sens["row_segment"] = segs[ri]["name"]
        sens["col_segment"] = segs[ci]["name"]
    c["sens_row_idx"] = _seg_index_by_name(segs, sens.get("row_segment"))
    c["sens_col_idx"] = _seg_index_by_name(segs, sens.get("col_segment"))
    # default step ladders centered on base multiple
    for idx, axis in ((c["sens_row_idx"], "row_steps"), (c["sens_col_idx"], "col_steps")):
        if idx is not None and not sens.get(axis):
            base = segs[idx]["mult_base"]
            if axis == "row_steps":
                sens[axis] = [round(base + (k - 4) * 0.75, 2) for k in range(9)]
            else:
                sens[axis] = [base + (k - 4) for k in range(9)]

    c["A"] = assumptions_anchors(c["n_segments"])
    c["S"] = sotp_anchors(c["n_segments"])
    c["CMP"] = comps_anchors(segs)
    return c


def comps_anchors(segs):
    """Row layout for the Comps sheet: one graded peer group per segment."""
    groups = []
    row = 4  # header is row 3
    for s in segs:
        k = max(len(s.get("comps", [])), 0)
        title = row
        first = row + 1
        last = row + k
        subtotal = last + 1
        groups.append({"title": title, "first": first, "last": last,
                       "subtotal": subtotal, "k": k})
        row = subtotal + 1
    reg_hdr = row + 1
    return {"header": 3, "groups": groups, "reg_hdr": reg_hdr,
            "reg_first": reg_hdr + 2}


def assumptions_anchors(n):
    """Row anchors on the Assumptions sheet for ``n`` segments.

    Layout matches the SPGI template exactly when n == 5; for other segment
    counts the multiples block (and everything below it) shifts to stay
    consistent so cross-sheet references never break.
    """
    mult_first = 29
    NB = 30 + n                # net-debt section header (35 when n==5)
    OA = NB + 19               # other-assets section header
    ED = OA + 4                # entity-debt section header
    SC = ED + 4                # share-count section header
    BB = SC + 4                # bloomberg section header
    return {
        "title": 1, "legend": 2,
        "mkt_hdr": 4, "price": 5, "lowhigh": 6, "basic_sh": 7, "dilutive": 8,
        "mktcap": 9,
        "seg_hdr": 11, "seg_units": 12, "fy_rev": 13, "fy_op": 14, "fy_opm": 15,
        "fy_dep": 16, "fy_ebitda": 17, "fy_ebitdam": 18, "growth": 19, "bps": 20,
        "corp_hdr": 22, "fy_corp": 23, "e_corp": 24, "minority_pct": 25,
        "mult_hdr": 27, "mult_units": 28, "mult_first": mult_first,
        "mult_last": mult_first + n - 1,
        "nb_hdr": NB, "bs_date": NB + 1, "st_debt": NB + 2, "lt_debt": NB + 3,
        "other_debt": NB + 4, "gross_debt": NB + 5, "cash": NB + 6,
        "net_debt": NB + 7, "stated_lev": NB + 8, "lev_ebitda": NB + 9,
        "red_nci": NB + 10, "lev_tie": NB + 11, "buyback": NB + 12,
        "leak": NB + 13, "net_spin": NB + 14, "reclass": NB + 15,
        "nonredeem": NB + 16, "sep_cost": NB + 17,
        "oa_hdr": OA, "retained": OA + 1, "notes_rec": OA + 2,
        "ed_hdr": ED, "spin_nd": ED + 1, "remainco_nd": ED + 2,
        "sc_hdr": SC, "fd_shares": SC + 1,
        "bb_hdr": BB, "bb_intro": BB + 1, "bb_tbl": BB + 5,
    }


def sotp_anchors(n):
    """Row anchors on the SOTP sheet for ``n`` segments (== template when n==5)."""
    EV0 = 15
    SUM = EV0 + n
    return {
        "title": 1, "subtitle": 2,
        "build_hdr": 4, "seg": 5, "rev": 6, "opm": 7, "op": 8, "dep": 9,
        "ebitda": 10, "ebitdam": 11,
        "bridge_hdr": 13, "scen": 14, "ev0": EV0, "ev_last": EV0 + n - 1,
        "sum": SUM, "corp": SUM + 1, "minority": SUM + 2, "comb_ev": SUM + 3,
        "retained": SUM + 4, "notes": SUM + 5, "net_debt": SUM + 6,
        "nonredeem": SUM + 7, "sep_cost": SUM + 8, "equity": SUM + 9,
        "shares": SUM + 10, "per_share": SUM + 11, "cur_price": SUM + 12,
        "upside": SUM + 13,
        "ent_hdr": SUM + 15, "remainco": SUM + 16, "spin_eq": SUM + 17,
        "remain_ps": SUM + 18, "spin_ps": SUM + 19, "ent_total": SUM + 20,
        "rev_hdr": SUM + 22, "mkt_eq": SUM + 23, "plus_nd": SUM + 24,
        "mkt_ev": SUM + 25, "blended": SUM + 26, "memo": SUM + 27,
        "impl_row": SUM + 28, "vs_base": SUM + 29, "vs_peer": SUM + 30,
    }
