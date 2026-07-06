import os
import typing as t
from itertools import groupby
import operator
from dataclasses import dataclass

from utils import date_utils, share_data_utils, file_utils, logger
from utils.ticker_mapping import ticker_org_info, ticker_currency_info
from utils.rates import rbi_rates_utils
from models.purchase import Purchase, Price
from models.sale import Sale
from models.itr.faa3 import FAA3


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


def parse_org_purchases(
    ticker: str,
    calendar_mode: str,
    purchases: t.List[Purchase],
    assessment_year: int,
    output_folder_abs_path: str,
    sales: t.Optional[t.List[Sale]] = None,
):
    start_time_in_ms, end_time_in_ms = date_utils.calendar_range(
        calendar_mode, assessment_year
    )
    if sales:
        recon = reconcile_sales(purchases, sales, start_time_in_ms, end_time_in_ms)
        purchases = recon.held_purchases
        sold_during = recon.sold_during
    else:
        sold_during = []
    org = ticker_org_info[ticker]
    currency_code = ticker_currency_info[ticker]
    before_purchases = list(
        filter(
            lambda purchase: purchase.date["time_in_millis"] < start_time_in_ms,
            purchases,
        )
    )
    after_purchases = list(
        filter(
            lambda purchase: purchase.date["time_in_millis"] >= start_time_in_ms
            and purchase.date["time_in_millis"] <= end_time_in_ms,
            purchases,
        )
    )
    # for a in before_purchases:
    #     t = a.date["disp_time"]
    #     print(f"a = {a.quantity} on da = {t}")

    previous_sum = sum(map(lambda purchase: purchase.quantity, before_purchases))
    print(
        f"{ticker}: Previous period(before {date_utils.display_time(start_time_in_ms)}) total share = {previous_sum}"
    )

    after_sum = sum(map(lambda purchase: purchase.quantity, after_purchases))
    print(
        f"{ticker}: This period(from {date_utils.display_time(start_time_in_ms)} to {date_utils.display_time(end_time_in_ms)}) total share = {after_sum}"
    )

    fa_entries: t.List[FAA3] = []
    before_purchases_last_date = f"31-Dec-{assessment_year - 2}"
    before_purchase_date = date_utils.parse_named_mon(before_purchases_last_date)
    closing_rbi_rate = rbi_rates_utils.get_rate_for_prev_mon_for_time_in_ms(
        currency_code, end_time_in_ms
    )
    closing_share_price = share_data_utils.get_closing_price(ticker, end_time_in_ms)
    closing_inr_price = closing_share_price * closing_rbi_rate
    print(
        f"{ticker}: Closing price(INR) = {closing_inr_price}, closing_share_price({ticker_currency_info[ticker]}) = {closing_share_price} closing_rbi_rate(INR) = {closing_rbi_rate}"
    )
    fmv_price_on_start = share_data_utils.get_fmv(
        ticker, before_purchase_date["time_in_millis"]
    )
    print(
        f"{ticker}: Queried FMV on {before_purchases_last_date} is {fmv_price_on_start}. This is used for accumulated sum for previous purchases"
    )
    if previous_sum != 0:
        fa_entries.append(
            FAA3(
                org,
                purchase=Purchase(
                    before_purchase_date,
                    Price(
                        fmv_price_on_start,
                        ticker_currency_info[ticker],
                    ),
                    quantity=previous_sum,
                    ticker=ticker,
                ),
                purchase_price=previous_sum
                * fmv_price_on_start
                * rbi_rates_utils.get_rate_for_prev_mon_for_time_in_ms(
                    currency_code, start_time_in_ms
                ),
                peak_price=previous_sum
                * share_data_utils.get_peak_price_in_inr(
                    ticker, start_time_in_ms, end_time_in_ms
                ),
                closing_price=previous_sum * closing_inr_price,
            )
        )

    for purchase in after_purchases:
        fa_entries.append(
            FAA3(
                org,
                purchase=purchase,
                peak_price=purchase.quantity
                * share_data_utils.get_peak_price_in_inr(
                    ticker,
                    purchase.date["time_in_millis"],
                    end_time_in_ms,
                ),
                purchase_price=purchase.quantity
                * purchase.purchase_fmv.price
                * rbi_rates_utils.get_rate_for_prev_mon_for_time_in_ms(
                    currency_code, purchase.date["time_in_millis"]
                ),
                closing_price=purchase.quantity * closing_inr_price,
            )
        )

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

    file_utils.write_to_file(
        os.path.join(output_folder_abs_path, ticker),
        "raw_fa_entries.json",
        fa_entries,
        True,
    )
    file_utils.write_to_file(
        os.path.join(output_folder_abs_path, ticker),
        "raw_fa_entries.json",
        fa_entries,
        True,
    )
    file_utils.write_csv_to_file(
        os.path.join(output_folder_abs_path, ticker),
        "fa_entries.csv",
        [
            "Country/Region name",
            "Country Name and Code",
            "Name of entity",
            "Address of entity",
            "ZIP Code",
            "Nature of entity",
            "Date of acquiring the interest",
            "Initial value of the investment",
            "Peak value of investment during the Period",
            "Closing balance",
            "Total gross amount paid/credited with respect to the holding during the period",
            "Total gross proceeds from sale or redemption of investment during the period",
        ],
        map(faa3_to_csv_row, fa_entries),
        True,
        print_path_to_console=True,
    )
    return fa_entries


def parse(
    calendar_mode: str,
    purchases: t.List[Purchase],
    assessment_year: int,
    output_folder_abs_path: str,
    sales: t.Optional[t.List[Sale]] = None,
):
    # Group sales by ticker. A ticker present only in sales (never in
    # BenefitHistory purchases) is not emitted here — acceptable because
    # BenefitHistory lists all acquisitions, including later-sold lots.
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
