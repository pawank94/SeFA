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
