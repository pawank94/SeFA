# SeFA Sold-Shares Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consume an E*Trade Gains & Losses export (`G&L_Expanded.xlsx`) to correctly reflect sold shares in the generated ITR Schedule FA (A3): report gross sale proceeds, zero the closing balance for sold lots, and compute peak value only over the sub-period a lot was held.

**Architecture:** `BenefitHistory.xlsx` stays the base (all acquisitions). A new optional input `G&L_Expanded.xlsx` is parsed into `Sale` objects. In `faa3_parser`, a **pure reconciliation** function subtracts sold shares from the held pool and emits dedicated FAA3 entries for lots sold within the assessment-year window. A new optional CLI flag `-g/--gains-losses` wires it in; without the flag behavior is unchanged.

**Tech Stack:** Python 3.8+, pandas, openpyxl, pytest. Existing utils: `date_utils`, `share_data_utils`, `rbi_rates_utils`, `file_utils`, `ticker_mapping`.

## Global Constraints

- Python `>=3.8` (per `pyproject.toml`). No new third-party dependencies — `pandas`/`openpyxl` already installed.
- Dates in the G&L file are `mm/dd/yyyy` — parse with the existing `date_utils.parse_mm_dd`.
- Money in the G&L file is USD; convert to INR with `rbi_rates_utils.get_rate_for_prev_mon_for_time_in_ms(currency_code, time_in_ms)`, same as existing purchase logic.
- A3 wants **gross** proceeds — use the `Total Proceeds` column, ignore wash-sale-adjusted columns.
- Column 11 of the CSV ("Total gross amount paid/credited", i.e. dividends) stays `0` — out of scope.
- Follow existing patterns: `@dataclass` models, parser module per source, tests under `tests/unit/parser/...` using `pd.DataFrame` / `MagicMock(spec=pd.ExcelFile)` mocks (see `tests/unit/parser/demat/etrade/conftest.py`).
- Run all test commands from the repo root `/Users/pawakum/Downloads/SeFA` with the venv active.

---

### Task 0: Environment setup + baseline

**Files:** none (environment only).

- [ ] **Step 1: Create venv and install**

```bash
cd /Users/pawakum/Downloads/SeFA
python3 -m venv .venv
source .venv/bin/activate
pip install .
pip install pytest
```

- [ ] **Step 2: Record the test baseline**

Run: `python -m pytest -q`
Expected: tests collect and run. NOTE: some pre-existing `etrade_benefit_history` tests may already fail (they call `parse_espp(mock)` with one argument while the current source signature is `parse_espp(xl, time_bounds)`). Record which tests fail **before** any change so later failures can be attributed correctly. Do not fix these here — they are out of scope.

---

### Task 1: `Sale` model + Gains & Losses parser

**Files:**
- Create: `models/sale.py`
- Create: `parser/demat/etrade/etrade_gains_losses_parser.py`
- Test: `tests/unit/parser/demat/etrade/test_etrade_gains_losses_parser.py`

**Interfaces:**
- Consumes: `models.purchase.Price(price: float, currency_code: str)`; `date_utils.parse_mm_dd(str) -> DateObj`; `ticker_currency_info: Dict[str, str]`.
- Produces:
  - `models.sale.Sale(ticker: str, plan_type: str, acquisition_date: DateObj, sale_date: DateObj, quantity: float, acquisition_fmv: Price, proceeds: Price)`
  - `etrade_gains_losses_parser.parse_sell_row(data: pd.Series) -> Optional[Sale]`
  - `etrade_gains_losses_parser.parse(input_file_abs_path: str, output_folder_abs_path: str) -> List[Sale]`
  - `etrade_gains_losses_parser.GAINS_LOSSES_SHEET_NAME = "G&L_Expanded"`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/parser/demat/etrade/test_etrade_gains_losses_parser.py`:

```python
import pandas as pd
from unittest.mock import MagicMock

from parser.demat.etrade import etrade_gains_losses_parser


def _sell_series(**overrides):
    row = {
        "Record Type": "Sell",
        "Symbol": "ADBE",
        "Plan Type": "RS",
        "Quantity": 4,
        "Date Acquired": "10/15/2024",
        "Date Sold": "01/28/2025",
        "Adjusted Cost Basis Per Share": 510.925,
        "Total Proceeds": 1742.55,
    }
    row.update(overrides)
    return pd.Series(row)


def test_parse_sell_row_maps_fields():
    sale = etrade_gains_losses_parser.parse_sell_row(_sell_series())
    assert sale is not None
    assert sale.ticker == "adbe"
    assert sale.plan_type == "RS"
    assert sale.quantity == 4.0
    assert sale.acquisition_date["orig_disp_time"] == "10/15/2024"
    assert sale.sale_date["orig_disp_time"] == "01/28/2025"
    assert sale.acquisition_fmv.price == 510.925
    assert sale.acquisition_fmv.currency_code == "USD"
    assert sale.proceeds.price == 1742.55
    assert sale.proceeds.currency_code == "USD"


def test_parse_sell_row_skips_non_sell():
    assert etrade_gains_losses_parser.parse_sell_row(
        pd.Series({"Record Type": "Summary"})
    ) is None


def test_parse_skips_summary_and_returns_sales(tmp_path):
    df = pd.DataFrame(
        {
            "Record Type": ["Summary", "Sell", "Sell"],
            "Symbol": [None, "ADBE", "ADBE"],
            "Plan Type": [None, "RS", "ESPP"],
            "Quantity": [None, 4, 6],
            "Date Acquired": [None, "10/15/2024", "12/31/2024"],
            "Date Sold": [None, "01/28/2025", "01/23/2025"],
            "Adjusted Cost Basis Per Share": [None, 510.925, 444.680333],
            "Total Proceeds": [None, 1742.55, 2610.250002],
        }
    )
    xl = MagicMock(spec=pd.ExcelFile)
    xl.sheet_names = [etrade_gains_losses_parser.GAINS_LOSSES_SHEET_NAME]
    xl.parse.return_value = df
    xl.__enter__.return_value = xl
    xl.__exit__.return_value = False

    import parser.demat.etrade.etrade_gains_losses_parser as mod
    mod.pd.ExcelFile = MagicMock(return_value=xl)

    sales = etrade_gains_losses_parser.parse("ignored.xlsx", str(tmp_path))
    assert len(sales) == 2
    # sorted by sale date -> 01/23 before 01/28
    assert sales[0].sale_date["orig_disp_time"] == "01/23/2025"
    assert sales[0].plan_type == "ESPP"
    assert sales[1].plan_type == "RS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/parser/demat/etrade/test_etrade_gains_losses_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'parser.demat.etrade.etrade_gains_losses_parser'` (and `models.sale`).

- [ ] **Step 3: Create the `Sale` model**

Create `models/sale.py`:

```python
from dataclasses import dataclass
from models.purchase import Price
from utils.date_utils import DateObj


@dataclass
class Sale:
    ticker: str
    plan_type: str
    acquisition_date: DateObj
    sale_date: DateObj
    quantity: float
    acquisition_fmv: Price
    proceeds: Price
```

- [ ] **Step 4: Create the parser**

Create `parser/demat/etrade/etrade_gains_losses_parser.py`:

```python
from utils.runtime_utils import warn_missing_module
from utils import logger, file_utils, date_utils
from utils.ticker_mapping import ticker_currency_info

warn_missing_module("pandas")
import pandas as pd
import typing as t

from models.sale import Sale
from models.purchase import Price

DEBUG = False

GAINS_LOSSES_SHEET_NAME = "G&L_Expanded"


def parse_sell_row(data: pd.Series) -> t.Optional[Sale]:
    if data["Record Type"] != "Sell":
        return None
    ticker = data["Symbol"].lower()
    currency = ticker_currency_info[ticker]
    return Sale(
        ticker=ticker,
        plan_type=data["Plan Type"],
        acquisition_date=date_utils.parse_mm_dd(data["Date Acquired"]),
        sale_date=date_utils.parse_mm_dd(data["Date Sold"]),
        quantity=float(data["Quantity"]),
        acquisition_fmv=Price(float(data["Adjusted Cost Basis Per Share"]), currency),
        proceeds=Price(float(data["Total Proceeds"]), currency),
    )


def parse(input_file_abs_path: str, output_folder_abs_path: str) -> t.List[Sale]:
    logger.DEBUG = DEBUG
    sales: t.List[Sale] = []
    with pd.ExcelFile(input_file_abs_path, engine="openpyxl") as xl:
        if GAINS_LOSSES_SHEET_NAME not in xl.sheet_names:
            logger.log(
                f"Gains & Losses sheet '{GAINS_LOSSES_SHEET_NAME}' not found; "
                "skipping sale parsing"
            )
            return []
        sheet_pd = xl.parse(
            sheet_name=GAINS_LOSSES_SHEET_NAME, skiprows=0, header=0
        )
        for _, data in sheet_pd.iterrows():
            sale = parse_sell_row(data)
            if sale is not None:
                sales.append(sale)
    sales.sort(key=lambda sale: sale.sale_date["time_in_millis"])
    file_utils.write_to_file(output_folder_abs_path, "sales.json", sales, True)
    return sales
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/parser/demat/etrade/test_etrade_gains_losses_parser.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add models/sale.py parser/demat/etrade/etrade_gains_losses_parser.py tests/unit/parser/demat/etrade/test_etrade_gains_losses_parser.py
git commit -m "feat: parse E*Trade Gains & Losses export into Sale objects"
```

---

### Task 2: `FAA3.sale_proceeds` field + CSV row helper

**Files:**
- Modify: `models/itr/faa3.py`
- Modify: `parser/itr/faa3_parser.py` (extract CSV row builder; use `sale_proceeds`)
- Test: `tests/unit/parser/itr/test_faa3_csv_row.py` (create)

**Interfaces:**
- Consumes: `models.org.Organization` (via `ticker_org_info["adbe"]`), `FAA3`.
- Produces: `FAA3(..., sale_proceeds: float = 0.0)`; `faa3_parser.faa3_to_csv_row(entry: FAA3) -> tuple` (12-element row).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/parser/itr/test_faa3_csv_row.py`:

```python
from models.itr.faa3 import FAA3
from models.purchase import Purchase, Price
from parser.itr import faa3_parser
from utils import date_utils
from utils.ticker_mapping import ticker_org_info


def _entry(sale_proceeds):
    return FAA3(
        org=ticker_org_info["adbe"],
        purchase=Purchase(
            date=date_utils.parse_mm_dd("10/15/2024"),
            purchase_fmv=Price(500.0, "USD"),
            quantity=4,
            ticker="adbe",
        ),
        purchase_price=100.0,
        peak_price=200.0,
        closing_price=0.0,
        sale_proceeds=sale_proceeds,
    )


def test_csv_row_reports_sale_proceeds_and_zero_dividends():
    row = faa3_parser.faa3_to_csv_row(_entry(1234.6))
    assert len(row) == 12
    assert row[6] == "2024-10-15"       # date of acquiring
    assert row[7] == 100                 # initial value (rounded)
    assert row[8] == 200                 # peak (rounded)
    assert row[9] == 0                   # closing (rounded)
    assert row[10] == 0                  # gross amount paid/credited (dividends)
    assert row[11] == 1235               # gross proceeds from sale (rounded)


def test_csv_row_defaults_proceeds_to_zero():
    row = faa3_parser.faa3_to_csv_row(
        FAA3(
            org=ticker_org_info["adbe"],
            purchase=_entry(0).purchase,
            purchase_price=100.0,
            peak_price=200.0,
            closing_price=300.0,
        )
    )
    assert row[11] == 0
    assert row[9] == 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/parser/itr/test_faa3_csv_row.py -v`
Expected: FAIL — `FAA3.__init__() got an unexpected keyword argument 'sale_proceeds'` and `module 'faa3_parser' has no attribute 'faa3_to_csv_row'`.

- [ ] **Step 3: Add the field to `FAA3`**

Modify `models/itr/faa3.py` to:

```python
from dataclasses import dataclass
from models.org import Organization
from models.purchase import Purchase


@dataclass
class FAA3:
    org: Organization
    purchase: Purchase
    purchase_price: float
    peak_price: float
    closing_price: float
    sale_proceeds: float = 0.0
```

- [ ] **Step 4: Extract the CSV row builder and use `sale_proceeds`**

In `parser/itr/faa3_parser.py`, add this module-level function (near the top, after imports):

```python
def faa3_to_csv_row(entry: FAA3) -> tuple:
    return (
        entry.org.country_name,
        entry.org.country_code,
        entry.org.name,
        entry.org.address,
        entry.org.zip_code,
        entry.org.nature,
        # ref https://www.reddit.com/r/IndiaTax/comments/1mhbi0w/a3_template_commonerrorscsv_row_skip_any_idea/
        date_utils.format_time(entry.purchase.date["time_in_millis"], "%Y-%m-%d"),
        round(entry.purchase_price),
        round(entry.peak_price),
        round(entry.closing_price),
        0,  # gross amount paid/credited (dividends) - out of scope
        round(entry.sale_proceeds),
    )
```

Then replace the `map(lambda entry: (...), fa_entries)` argument in the `file_utils.write_csv_to_file(...)` call with `map(faa3_to_csv_row, fa_entries)`. Delete the old inline lambda tuple (including the `0, # todo sale is not supported` line).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/parser/itr/test_faa3_csv_row.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add models/itr/faa3.py parser/itr/faa3_parser.py tests/unit/parser/itr/test_faa3_csv_row.py
git commit -m "feat: add sale_proceeds to FAA3 and extract CSV row builder"
```

---

### Task 3: Pure reconciliation functions

**Files:**
- Modify: `parser/itr/faa3_parser.py` (add `bucket_sale`, `Reconciliation`, `reconcile_sales`)
- Test: `tests/unit/parser/itr/test_faa3_reconcile.py` (create)

**Interfaces:**
- Consumes: `models.purchase.Purchase`, `models.sale.Sale`, `utils.logger.log`.
- Produces:
  - `faa3_parser.bucket_sale(sale: Sale, start_time_in_ms: int, end_time_in_ms: int) -> str` returning `"before"`, `"during"`, or `"after"`.
  - `faa3_parser.Reconciliation` dataclass with `held_purchases: List[Purchase]` and `sold_during: List[Sale]`.
  - `faa3_parser.reconcile_sales(purchases: List[Purchase], sales: List[Sale], start_time_in_ms: int, end_time_in_ms: int) -> Reconciliation`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/parser/itr/test_faa3_reconcile.py`:

```python
from models.purchase import Purchase, Price
from models.sale import Sale
from parser.itr import faa3_parser
from utils import date_utils

# calendar 2024 window
START, END = date_utils.calendar_range("calendar", 2025)


def _purchase(date_str, qty, fmv=500.0):
    return Purchase(
        date=date_utils.parse_mm_dd(date_str),
        purchase_fmv=Price(fmv, "USD"),
        quantity=qty,
        ticker="adbe",
    )


def _sale(acq_str, sold_str, qty, fmv=500.0, proceeds=1000.0):
    return Sale(
        ticker="adbe",
        plan_type="RS",
        acquisition_date=date_utils.parse_mm_dd(acq_str),
        sale_date=date_utils.parse_mm_dd(sold_str),
        quantity=qty,
        acquisition_fmv=Price(fmv, "USD"),
        proceeds=Price(proceeds, "USD"),
    )


def test_bucket_sale_classifies_by_sale_date():
    assert faa3_parser.bucket_sale(_sale("01/24/2022", "06/30/2023", 1), START, END) == "before"
    assert faa3_parser.bucket_sale(_sale("01/24/2022", "06/30/2024", 1), START, END) == "during"
    assert faa3_parser.bucket_sale(_sale("01/24/2022", "06/30/2025", 1), START, END) == "after"


def test_sold_after_window_leaves_held_unchanged():
    purchases = [_purchase("10/15/2023", 4)]
    sales = [_sale("10/15/2023", "01/28/2025", 4)]
    recon = faa3_parser.reconcile_sales(purchases, sales, START, END)
    assert recon.sold_during == []
    assert len(recon.held_purchases) == 1
    assert recon.held_purchases[0].quantity == 4


def test_sold_before_window_drops_lot_and_emits_no_entry():
    purchases = [_purchase("06/30/2022", 4)]
    sales = [_sale("06/30/2022", "01/15/2023", 4)]
    recon = faa3_parser.reconcile_sales(purchases, sales, START, END)
    assert recon.sold_during == []
    assert recon.held_purchases == []  # fully sold -> dropped


def test_sold_during_window_reduces_held_and_records_sale():
    purchases = [_purchase("10/15/2023", 4)]
    sales = [_sale("10/15/2023", "06/30/2024", 4)]
    recon = faa3_parser.reconcile_sales(purchases, sales, START, END)
    assert len(recon.sold_during) == 1
    assert recon.held_purchases == []


def test_partial_sale_splits_held_remainder():
    purchases = [_purchase("10/15/2023", 10)]
    sales = [_sale("10/15/2023", "06/30/2024", 4)]
    recon = faa3_parser.reconcile_sales(purchases, sales, START, END)
    assert len(recon.sold_during) == 1
    assert len(recon.held_purchases) == 1
    assert abs(recon.held_purchases[0].quantity - 6) < 1e-6


def test_unmatched_sale_warns_but_still_records_during(capsys):
    purchases = [_purchase("10/15/2023", 4)]
    sales = [_sale("02/02/2020", "06/30/2024", 2, fmv=123.0)]  # no matching lot
    recon = faa3_parser.reconcile_sales(purchases, sales, START, END)
    assert len(recon.sold_during) == 1              # proceeds still reported
    assert recon.held_purchases[0].quantity == 4     # held not reduced
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/parser/itr/test_faa3_reconcile.py -v`
Expected: FAIL — `module 'faa3_parser' has no attribute 'bucket_sale'`.

- [ ] **Step 3: Implement the pure functions**

In `parser/itr/faa3_parser.py`, add near the top after the imports. First extend imports:

```python
from dataclasses import dataclass
from models.sale import Sale
from utils import logger
```

Then add:

```python
@dataclass
class Reconciliation:
    held_purchases: t.List[Purchase]
    sold_during: t.List[Sale]


def bucket_sale(sale: Sale, start_time_in_ms: int, end_time_in_ms: int) -> str:
    sold_ms = sale.sale_date["time_in_millis"]
    if sold_ms < start_time_in_ms:
        return "before"
    if sold_ms > end_time_in_ms:
        return "after"
    return "during"


def _reduce_held_lot(remaining: t.List[Purchase], sale: Sale) -> None:
    """Subtract the sold quantity from its matching acquisition lot in place.

    Matches by acquisition date; falls back to (FMV, quantity) for ESPP where
    the G&L 'Date Acquired' differs from the BenefitHistory purchase date.
    Logs loudly and leaves the held pool untouched when no lot matches."""
    acq_ms = sale.acquisition_date["time_in_millis"]
    candidates = [p for p in remaining if p.date["time_in_millis"] == acq_ms]
    if not candidates:
        candidates = [
            p
            for p in remaining
            if abs(p.purchase_fmv.price - sale.acquisition_fmv.price) < 0.01
            and p.quantity >= sale.quantity - 1e-6
        ]
    if not candidates:
        logger.log(
            f"WARNING: sold {sale.quantity} {sale.ticker} share(s) acquired "
            f"{sale.acquisition_date['disp_time']} have no matching acquisition "
            "lot; held balance may be overstated. Verify manually."
        )
        return
    lot = candidates[0]
    if sale.quantity > lot.quantity + 1e-6:
        logger.log(
            f"WARNING: sold quantity {sale.quantity} exceeds held {lot.quantity} "
            f"for {sale.ticker} lot acquired {sale.acquisition_date['disp_time']}; "
            "clamping to 0."
        )
    lot.quantity = max(0.0, lot.quantity - sale.quantity)


def reconcile_sales(
    purchases: t.List[Purchase],
    sales: t.List[Sale],
    start_time_in_ms: int,
    end_time_in_ms: int,
) -> Reconciliation:
    remaining = [
        Purchase(p.date, p.purchase_fmv, p.quantity, p.ticker) for p in purchases
    ]
    sold_during: t.List[Sale] = []
    for sale in sales:
        bucket = bucket_sale(sale, start_time_in_ms, end_time_in_ms)
        if bucket == "after":
            continue  # still held through the window end
        if bucket == "during":
            sold_during.append(sale)
        _reduce_held_lot(remaining, sale)  # before + during reduce the held pool
    held = [p for p in remaining if p.quantity > 1e-6]
    return Reconciliation(held_purchases=held, sold_during=sold_during)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/parser/itr/test_faa3_reconcile.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add parser/itr/faa3_parser.py tests/unit/parser/itr/test_faa3_reconcile.py
git commit -m "feat: add pure sale reconciliation to faa3_parser"
```

---

### Task 4: Wire reconciliation into entry generation

**Files:**
- Modify: `parser/itr/faa3_parser.py` (`parse` and `parse_org_purchases` signatures + sold-during entries)
- Test: `tests/unit/parser/itr/test_faa3_parse_org_purchases.py` (create)

**Interfaces:**
- Consumes: `reconcile_sales`, `share_data_utils.get_peak_price_in_inr(ticker, start_ms, end_ms)`, `rbi_rates_utils.get_rate_for_prev_mon_for_time_in_ms(currency, ms)`, `share_data_utils.get_closing_price`, `share_data_utils.get_fmv`.
- Produces:
  - `parse_org_purchases(ticker, calendar_mode, purchases, assessment_year, output_folder_abs_path, sales=None) -> List[FAA3]`
  - `parse(calendar_mode, purchases, assessment_year, output_folder_abs_path, sales=None)`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/parser/itr/test_faa3_parse_org_purchases.py`:

```python
import pytest

from models.purchase import Purchase, Price
from models.sale import Sale
from parser.itr import faa3_parser
from utils import date_utils, share_data_utils
from utils.rates import rbi_rates_utils


@pytest.fixture(autouse=True)
def _stub_prices(monkeypatch):
    monkeypatch.setattr(share_data_utils, "get_peak_price_in_inr", lambda t, s, e: 100.0)
    monkeypatch.setattr(share_data_utils, "get_closing_price", lambda t, e: 1.0)
    monkeypatch.setattr(share_data_utils, "get_fmv", lambda t, ms: 1.0)
    monkeypatch.setattr(
        rbi_rates_utils, "get_rate_for_prev_mon_for_time_in_ms", lambda c, ms: 80.0
    )


def _purchase(date_str, qty):
    return Purchase(date_utils.parse_mm_dd(date_str), Price(500.0, "USD"), qty, "adbe")


def _sale(acq_str, sold_str, qty, proceeds):
    return Sale(
        ticker="adbe",
        plan_type="RS",
        acquisition_date=date_utils.parse_mm_dd(acq_str),
        sale_date=date_utils.parse_mm_dd(sold_str),
        quantity=qty,
        acquisition_fmv=Price(500.0, "USD"),
        proceeds=Price(proceeds, "USD"),
    )


def test_sold_during_entry_has_zero_closing_and_inr_proceeds(tmp_path):
    # acquired in-window, sold in-window
    purchases = [_purchase("03/01/2024", 4)]
    sales = [_sale("03/01/2024", "06/30/2024", 4, proceeds=1000.0)]
    entries = faa3_parser.parse_org_purchases(
        "adbe", "calendar", purchases, 2025, str(tmp_path), sales
    )
    sold = [e for e in entries if e.sale_proceeds > 0]
    assert len(sold) == 1
    assert sold[0].closing_price == 0
    assert sold[0].sale_proceeds == pytest.approx(1000.0 * 80.0)  # proceeds * rate
    # fully sold -> no held entry for that lot
    assert all(e.closing_price == 0 for e in entries)


def test_no_sales_behaves_like_before(tmp_path):
    purchases = [_purchase("03/01/2024", 4)]
    entries = faa3_parser.parse_org_purchases(
        "adbe", "calendar", purchases, 2025, str(tmp_path), None
    )
    assert all(e.sale_proceeds == 0 for e in entries)
    assert any(e.closing_price > 0 for e in entries)  # held, closing populated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/parser/itr/test_faa3_parse_org_purchases.py -v`
Expected: FAIL — `parse_org_purchases() takes 5 positional arguments but 6 were given`.

- [ ] **Step 3: Add the `sales` parameter and reconcile at the top of `parse_org_purchases`**

In `parser/itr/faa3_parser.py`, change the signature:

```python
def parse_org_purchases(
    ticker: str,
    calendar_mode: str,
    purchases: t.List[Purchase],
    assessment_year: int,
    output_folder_abs_path: str,
    sales: t.Optional[t.List[Sale]] = None,
):
```

Immediately after `start_time_in_ms, end_time_in_ms = date_utils.calendar_range(...)`, insert:

```python
    if sales:
        recon = reconcile_sales(purchases, sales, start_time_in_ms, end_time_in_ms)
        purchases = recon.held_purchases
        sold_during = recon.sold_during
    else:
        sold_during = []
```

The existing before/after logic now runs on the reduced `purchases` unchanged.

- [ ] **Step 4: Emit sold-during entries before writing files**

In `parse_org_purchases`, after the `for purchase in after_purchases:` loop that appends held entries and **before** the `file_utils.write_to_file(...)` calls, insert:

```python
    for sale in sold_during:
        acq_ms = sale.acquisition_date["time_in_millis"]
        peak_start_ms = max(acq_ms, start_time_in_ms)
        fa_entries.append(
            FAA3(
                org,
                purchase=Purchase(
                    sale.acquisition_date,
                    sale.acquisition_fmv,
                    sale.quantity,
                    ticker,
                ),
                purchase_price=sale.quantity
                * sale.acquisition_fmv.price
                * rbi_rates_utils.get_rate_for_prev_mon_for_time_in_ms(
                    currency_code, acq_ms
                ),
                peak_price=sale.quantity
                * share_data_utils.get_peak_price_in_inr(
                    ticker, peak_start_ms, sale.sale_date["time_in_millis"]
                ),
                closing_price=0,
                sale_proceeds=sale.proceeds.price
                * rbi_rates_utils.get_rate_for_prev_mon_for_time_in_ms(
                    currency_code, sale.sale_date["time_in_millis"]
                ),
            )
        )
```

- [ ] **Step 5: Thread `sales` through `parse`**

Change `parse` to group sales by ticker and pass them down:

```python
def parse(
    calendar_mode: str,
    purchases: t.List[Purchase],
    assessment_year: int,
    output_folder_abs_path: str,
    sales: t.Optional[t.List[Sale]] = None,
):
    sales_by_ticker: t.Dict[str, t.List[Sale]] = {}
    for sale in sales or []:
        sales_by_ticker.setdefault(sale.ticker, []).append(sale)

    ticker_attr = operator.attrgetter("ticker")
    grouped_list = groupby(sorted(purchases, key=ticker_attr), ticker_attr)

    for ticker, each_org_purchases in grouped_list:
        parse_org_purchases(
            ticker,
            calendar_mode,
            list(each_org_purchases),
            assessment_year,
            output_folder_abs_path,
            sales_by_ticker.get(ticker, []),
        )
```

Note: a ticker present only in sales (no acquisitions in `BenefitHistory.xlsx`) is not iterated. This is acceptable — BenefitHistory lists all acquisitions including later-sold lots. Leave a code comment noting the limitation.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/unit/parser/itr/ -v`
Expected: PASS (all itr tests, including Task 2 and Task 3).

- [ ] **Step 7: Commit**

```bash
git add parser/itr/faa3_parser.py tests/unit/parser/itr/test_faa3_parse_org_purchases.py
git commit -m "feat: emit A3 entries for sold shares and thread sales through parse"
```

---

### Task 5: CLI wiring (`-g/--gains-losses`) + end-to-end verification

**Files:**
- Modify: `run.py`

**Interfaces:**
- Consumes: `etrade_gains_losses_parser.parse`, `faa3_parser.parse(..., sales=...)`.
- Produces: new CLI flag `-g/--gains-losses` (dest `gains_losses_file`, default `None`).

- [ ] **Step 1: Add the import and DEBUG wiring**

In `run.py`, after `from parser.demat.etrade import etrade_holdings_bystatus_parser`, add:

```python
from parser.demat.etrade import etrade_gains_losses_parser
```

After the `etrade_holdings_bystatus_parser.DEBUG = args.debug` line, add:

```python
    etrade_gains_losses_parser.DEBUG = args.debug
```

- [ ] **Step 2: Add the argument**

After the `--skip-refresh` argument definition, add:

```python
    parser.add_argument(
        "-g",
        "--gains-losses",
        action="store",
        dest="gains_losses_file",
        default=None,
        help="Optional absolute path to an E*Trade Gains & Losses export "
        "(G&L_Expanded.xlsx). When provided, sold shares are reconciled into "
        "the A3 output (closing balance and sale proceeds).",
    )
```

- [ ] **Step 3: Parse sales and pass them to faa3_parser**

Replace the final `faa3_parser.parse(...)` call in `main()` with:

```python
    sales = []
    if args.gains_losses_file:
        sales = etrade_gains_losses_parser.parse(
            args.gains_losses_file, args.output_folder
        )

    faa3_parser.parse(
        args.calendar_mode, purchases, args.assessment_year, args.output_folder, sales
    )
```

- [ ] **Step 4: Full suite green**

Run: `python -m pytest -q`
Expected: all new tests pass; the pre-existing baseline failures from Task 0 (if any) are unchanged.

- [ ] **Step 5: End-to-end manual run**

Run (adjust the BenefitHistory path if present; if you only have the G&L file, use any existing `BenefitHistory.xlsx` you have):

```bash
python run.py \
  -i "/absolute/path/to/BenefitHistory.xlsx" \
  -g "/Users/pawakum/Downloads/G&L_Expanded.xlsx" \
  -ay 2025 -v
```

Expected: console logs show sales being parsed (`output/.../sales.json` written); `output/adbe/fa_entries.csv` now has non-zero values in the last column ("Total gross proceeds from sale...") for lots sold during calendar 2024, and those sold lots show `0` closing balance. Verify a couple of rows by hand against `G&L_Expanded.xlsx`.

- [ ] **Step 6: Commit**

```bash
git add run.py
git commit -m "feat: add -g/--gains-losses CLI flag to reconcile sold shares"
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the G&L download + usage**

In `README.md`:
1. Under the download section, add steps to export the Gains & Losses file: At Work → Holdings → **Gains & Losses** → Download → **Download Expanded** → `G&L_Expanded.xlsx`.
2. In the "Run the script" usage block, add the optional `-g/--gains-losses` flag with an example:
   ```sh
   ./run.py -i ".../BenefitHistory.xlsx" -g ".../G&L_Expanded.xlsx" -ay 2025
   ```
3. In the "Limitations" section, rewrite the bullet that currently reads *"If you have sold any shares, the script will not adjust those. You have to subtract the `BenefitHistory.xlsx` manually"* to state that sold shares are now reconciled automatically when the `-g/--gains-losses` file is supplied, and note the remaining caveat: dividends ("gross amount paid/credited", column 11) are still not populated.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document Gains & Losses input and sold-share support"
```

---

## Self-Review Notes

- **Spec coverage:** Sale model (T1), G&L parser (T1), CLI flag (T5), FAA3 field + CSV column (T2), per-lot reconciliation with before/during/after buckets and peak-to-sale (T3+T4), fail-loud unmatched handling (T3), fractional/partial/multi-date edges (T3 tests), tests (T1–T4), docs (T6). All spec sections mapped.
- **Type consistency:** `Sale`, `Reconciliation`, `bucket_sale`, `reconcile_sales`, `faa3_to_csv_row`, and the `parse`/`parse_org_purchases` `sales=` parameter names are used identically across tasks.
- **Known limitation (documented in T4):** a ticker appearing only in the G&L file (never in BenefitHistory) is not emitted; acceptable because BenefitHistory lists all acquisitions.
