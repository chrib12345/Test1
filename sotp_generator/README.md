# SOTP Excel Generator

Type a stock symbol, get an institutional-style **Sum-of-the-Parts** valuation
workbook — an 11-sheet, fully formula-driven Excel model rebuilt from the
S&P Global (SPGI) 5-segment + spin-off master template.

```bash
python sotp.py SPGI                 # build from configs/SPGI.yaml
python sotp.py SPGI --live          # also refresh market & peer quotes (yfinance)
python sotp.py MSFT -o msft.xlsx    # custom output path
python sotp.py SPGI -c my_spgi.yaml # custom config
```

Output: `<TICKER>_SOTP.xlsx` with the same engine as the template —
`Cover · Build Spec · Valuation Summary · Assumptions · SOTP · FCF Build ·
Sensitivity · Comps · Recent Trends · Street Coverage · Spin Mechanics`.

## How it works

The model's design principle (inherited from the template): **the Assumptions
tab is the single source of truth, and every other sheet is Excel formulas.**
Change a blue input and the whole model — SOTP bridge, sensitivity heat maps,
football field, reverse SOTP — recomputes. This generator's job is to rebuild
that formula skeleton and pre-fill the inputs.

```
                ┌─────────────┐
  yfinance ───► │             │      configs/<TICKER>.yaml
  (--live)      │  providers  │ ───► (segments, multiples, ──┐
  SEC EDGAR ──► │             │       net-debt, peers, text) │
                └─────────────┘                              ▼
                                                   ┌──────────────────┐
                                                   │  build_workbook  │ ──► .xlsx
                                                   │  (11 sheets,     │     (formula-
                                                   │   all formulas)  │      driven)
                                                   └──────────────────┘
```

* **Market data** (price, 52-wk range, shares, consensus target) and **peer
  quotes** refresh live with `--live` when the network allows; otherwise the
  config values are used.
* **Per-segment financials and valuation multiples** — the analyst judgment a
  SOTP is built on — live in `configs/<TICKER>.yaml`. No free "type-a-ticker"
  API returns clean reportable-segment operating profit, so these are explicit,
  editable inputs (blue = input, **yellow = estimate / refresh-critical**),
  exactly as in the template. `configs/SPGI.yaml` is a complete worked example.

## Adding a new company

1. Copy `configs/SPGI.yaml` to `configs/<TICKER>.yaml`.
2. Fill in `segments` (revenue, operating profit, depreciation, growth,
   margin, and Bear/Base/Re-Rate multiples) and each segment's `comps`.
3. Set `corporate`, `net_debt`, `fcf`, `street`, and (if applicable) `spin`.
4. `python sotp.py <TICKER> --live`.

Segment count is flexible — list 3, 5, or 8 segments and every sheet, formula,
and the sensitivity grid adjust automatically. Set `corporate.minority_segment`
and a `spin` block only when they apply (both are optional).

## Verification

The golden test regenerates SPGI from config, evaluates the formulas in-process,
and asserts the outputs tie out to the original template's cached values
(per-share in all three scenarios, entity split, reverse SOTP, sensitivity
min/max, FCF, street-implied valuation, comps):

```bash
pip install -r requirements.txt
python tests/test_golden_spgi.py
# PASS — 24 cells across 7 sheets tie out to the SPGI template.
```

## Honest limitations

* A SOTP encodes analyst judgment (segment splits, peer selection, multiples).
  The tool **scaffolds and pre-fills** what's reliably obtainable; it does not
  invent segment economics. Treat yellow cells as refresh-critical.
* `--live` needs outbound access to Yahoo Finance / SEC EDGAR. In restricted
  networks it degrades cleanly to the config values.
* Generated narrative sheets (Build Spec, Spin Mechanics) use templated
  defaults unless you supply company-specific text in the config.

## Install

```bash
pip install -r requirements.txt   # openpyxl + PyYAML required; yfinance optional
```
