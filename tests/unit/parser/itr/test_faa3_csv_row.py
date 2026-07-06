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
