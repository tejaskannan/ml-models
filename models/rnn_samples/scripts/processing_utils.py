from datetime import datetime
from typing import Dict, Any, List, Optional
from math import isnan

from utils.constants import DATE_FORMAT


def parse_date(date_string: str) -> datetime:
    return datetime.strptime(date_string, DATE_FORMAT)


def extract_fields(record: Dict[str, Any], fields: List[str]) -> List[Any]:
    return [record[key] for key in fields]


def try_convert_to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except ValueError:
        return default

def isNoneOrNaN(x: Optional[float]) -> bool:
    return x is None or isnan(x)