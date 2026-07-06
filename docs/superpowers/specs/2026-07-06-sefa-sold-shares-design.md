# SeFA — Sold Shares Tracking (E*Trade Gains & Losses)

**Date:** 2026-07-06
**Status:** Approved (design)

## Problem

SeFA generates the Indian ITR Schedule FA (A3) from an E*Trade `BenefitHistory.xlsx`
(all acquisitions). It does not account for shares that were later **sold**. Today
[`parser/itr/faa3_parser.py`](../../../parser/itr/faa3_parser.py) hardcodes the
"Total gross proceeds from sale or redemption" CSV column to `0` and computes the
closing balance / peak as if every acquired share is still held. This overstates the
closing balance and omits sale proceeds. Per the current README limitation, users must
subtract sold shares manually.

## Goal

Consume an E*Trade **Gains & Losses** export (`G&L_Expanded.xlsx`) as an optional second
input and reconcile it against the acquisitions so that A3 correctly reflects sold shares:
report gross sale proceeds, zero out closing balance for sold lots, and compute peak value
only over the sub-period a lot was actually held.

Non-goals: dividends / "gross amount paid or credited" (column 11 stays `0`); PDF parsing;
multi-broker support.

## Input data

`G&L_Expanded.xlsx` has a single sheet `G&L_Expanded`. Row 0 is the header, row 1 is a
`Summary` row, remaining rows have `Record Type == "Sell"`. Relevant columns:

| Column | Use |
|---|---|
| `Record Type` | filter: keep `Sell`, skip `Summary` |
| `Symbol` | ticker (lowercased) |
| `Plan Type` | `ESPP` / `RS` |
| `Quantity` | shares sold in this lot (may be fractional, e.g. `16.008`) |
| `Date Acquired` | acquisition date (matching key) |
| `Date Sold` | sale date (buckets the sale into/around the AY window) |
| `Total Proceeds` | gross USD proceeds for the lot |

Wash-sale-adjusted columns are ignored — A3 asks for **gross** proceeds (`Total Proceeds`).

## Design

Two inputs: `BenefitHistory.xlsx` (all acquisitions, unchanged) plus an optional
`G&L_Expanded.xlsx` (sales) layered on top. Approach: **per-lot reconciliation**.

### Components

**`models/sale.py`** — new dataclass:
```python
@dataclass
class Sale:
    ticker: str
    plan_type: str          # "ESPP" | "RS"
    acquisition_date: DateObj
    sale_date: DateObj
    quantity: float
    proceeds: Price         # Total Proceeds, USD
```

**`parser/demat/etrade/etrade_gains_losses_parser.py`** — new parser:
`parse(input_file_abs_path, output_folder_abs_path) -> List[Sale]`.
Reads sheet `G&L_Expanded`, skips the `Summary` row, keeps `Record Type == "Sell"`, maps
the columns above into `Sale`, sets currency from `ticker_currency_info`, sorts by sale
date, and writes a `sales.json` debug dump (mirrors the existing `purchases.json`).
Requires an `mm/dd/yyyy` date parser in `utils/date_utils` — reuse `parse_mm_dd` if it
already accepts that format, otherwise add `parse_mm_dd_yyyy`.

**`run.py`** — add an optional arg `-g` / `--gains-losses <path>`. When present, parse the
G&L file and pass the resulting sales into `faa3_parser.parse`. When absent, behavior is
unchanged (proceeds `0`). Fully backward compatible.

**`models/itr/faa3.py`** — add field `sale_proceeds: float = 0.0` to `FAA3`.

**`parser/itr/faa3_parser.py`** — reconciliation, described below. The CSV writer sets
column 12 ("Total gross proceeds from sale or redemption of investment during the period")
to `round(entry.sale_proceeds)` instead of the hardcoded `0`. Column 11 stays `0`.

### Reconciliation

`parse(calendar_mode, purchases, assessment_year, output_folder, sales=None)` groups sales
by ticker alongside purchases and passes the per-ticker sales into `parse_org_purchases`.

For a ticker, match each sale to its acquisition lot by
`(ticker, plan_type, acquisition_date)`. For ESPP, where `Date Acquired` semantics differ
between the two files, fall back to matching on `(plan_type, purchase FMV, quantity)` with
a small numeric tolerance. Then bucket each sale by `sale_date` relative to the
assessment-year window `[start, end]`:

| Sale timing | Effect on A3 |
|---|---|
| sold **before** `start` | remove qty from the held pool; emit **no** entry (not held during the period) |
| sold **during** `[start, end]` | emit a dedicated `FAA3` entry: `peak = qty × peak_price_in_inr(ticker, max(acq_date, start), sale_date)`, `closing_price = 0`, `sale_proceeds = proceeds_usd × RBI_rate(prev-mon of sale_date)`, `purchase_price` computed on the sold qty exactly as held lots; subtract qty from the held pool |
| sold **after** `end` | held all year → no change, no proceeds this year |

Held entries — the before-window aggregate (`previous_sum`) and each after-window lot —
use the **reduced** quantity after subtracting matched sold-before and sold-during shares.
A lot reduced to `0` is skipped. Initial value, peak, and closing for the held remainder
are computed exactly as today, on the reduced quantity.

The peak sub-period lower bound mirrors the existing held logic: after-window lots use the
purchase date, before-window lots use the window start — hence `max(acq_date, start)`.

### Fail-loud

An unmatched sale (no acquisition lot found) logs a loud warning with the sale details,
still emits the sold-during entry using the G&L row's own acquisition FMV/date so proceeds
are not lost, and warns that the held reconciliation for that ticker may need a manual
check (potential double count). We do not silently drop sales.

## Edge cases

- Fractional ESPP quantities — match with a numeric tolerance.
- A single acquisition lot sold across multiple dates — multiple sold-during entries, with
  cumulative subtraction from the held pool.
- Lot acquired before the window but sold after it — no entry change, full held qty, no
  proceeds this year.
- Wash-sale columns ignored; `Total Proceeds` (gross) is used.

## Testing

- **G&L parser** (`tests/unit/parser/demat/etrade/`): parses `Sell` rows, skips the
  `Summary` row, maps proceeds/dates/quantities, lowercases the ticker.
- **Reconciliation** (new `tests/unit/parser/itr/`): sold-before → dropped;
  sold-during → proceeds + `closing = 0` + peak to sale date; sold-after → unchanged;
  partial sale → held remainder split from sold entry. Use small synthetic fixtures in the
  style of the existing `tests/unit/parser/demat/etrade` tests.

## Docs

Update `README.md`: rewrite the "sold shares are not adjusted" limitation, document the
Gains & Losses download steps, and add the `-g/--gains-losses` CLI usage.
