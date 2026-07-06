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
