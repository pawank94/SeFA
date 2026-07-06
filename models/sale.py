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
