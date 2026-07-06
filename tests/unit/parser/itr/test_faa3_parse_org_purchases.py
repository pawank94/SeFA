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
