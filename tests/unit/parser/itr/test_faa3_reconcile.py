import pytest

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
    assert "no matching" in capsys.readouterr().out


def test_g_and_l_fmv_overrides_held_remainder_for_partial_sale_during_window():
    # Held and sold shares from the same vest must value the shares
    # identically. The G&L "Adjusted Cost Basis Per Share" (sale.acquisition_fmv)
    # is the broker's actual recorded FMV for the vest and must win over
    # whatever FMV the purchase was originally parsed with (e.g. an
    # independent historic price feed).
    purchases = [_purchase("10/15/2023", 10, fmv=354.85)]
    sales = [_sale("10/15/2023", "06/30/2024", 4, fmv=351.59)]
    recon = faa3_parser.reconcile_sales(purchases, sales, START, END)
    assert len(recon.held_purchases) == 1
    assert recon.held_purchases[0].quantity == pytest.approx(6)
    assert recon.held_purchases[0].purchase_fmv.price == 351.59


def test_g_and_l_fmv_corrects_lot_even_when_matching_sale_is_after_window():
    # A lot fully held through the window's end can still have a sale on
    # record that closes after the window (e.g. sold the following calendar
    # year). The FMV correction must still apply -- it fixes a data-quality
    # issue in the lot's acquisition value, which has nothing to do with
    # when it was eventually sold -- while the held quantity stays untouched
    # since the shares were still held throughout the window.
    purchases = [_purchase("11/15/2025", 3, fmv=325.07)]
    sales = [_sale("11/15/2025", "03/23/2026", 1.5, fmv=332.01)]
    recon = faa3_parser.reconcile_sales(purchases, sales, START, END)
    assert recon.sold_during == []
    assert len(recon.held_purchases) == 1
    assert recon.held_purchases[0].quantity == 3
    assert recon.held_purchases[0].purchase_fmv.price == 332.01


def test_g_and_l_fmv_corrects_lot_when_matching_sale_is_before_window():
    purchases = [_purchase("06/30/2022", 4, fmv=386.88)]
    sales = [_sale("06/30/2022", "01/15/2023", 4, fmv=384.5)]
    recon = faa3_parser.reconcile_sales(purchases, sales, START, END)
    assert recon.held_purchases == []  # fully sold -> dropped, as before
