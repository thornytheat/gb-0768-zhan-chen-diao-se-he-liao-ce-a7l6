import re
import unicodedata
from decimal import Decimal

MONEY_FORM = re.compile(r'[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,6})?')


def cents(raw):
    """发票金额按 NFKC、千分位归一后转整数分，杜绝浮点尾差。"""
    raw = unicodedata.normalize('NFKC', raw).strip()
    if len(raw) > 24 or not MONEY_FORM.fullmatch(raw):
        raise ValueError('BAD_MONEY')
    value = Decimal(raw.replace(',', '')) * 100
    if not 0 <= value <= 10**12 or value != value.to_integral_value():
        raise ValueError('BAD_MONEY')
    return int(value)


def yuan(value):
    return f'{Decimal(value) / 100:,.2f}' if value is not None else '—'
