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


def test_parse_skips_summary_and_returns_sales(tmp_path, monkeypatch):
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

    monkeypatch.setattr(etrade_gains_losses_parser.pd, "ExcelFile", MagicMock(return_value=xl))

    sales = etrade_gains_losses_parser.parse("ignored.xlsx", str(tmp_path))
    assert len(sales) == 2
    # sorted by sale date -> 01/23 before 01/28
    assert sales[0].sale_date["orig_disp_time"] == "01/23/2025"
    assert sales[0].plan_type == "ESPP"
    assert sales[1].plan_type == "RS"
