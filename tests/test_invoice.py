import pytest

from app.blend.invoice import parse_invoice, settle_invoice
from app.blend.money import cents, yuan
from app.faults import LedgerError

FLOWS = ('applied_cents', 'loss_cents', 'returned_cents', 'disputed_cents')


def invoice(rows, invoice='INV-1', paid='2610.00'):
    head = 'invoice,lot,list_currency,list_amount,paid_currency,paid_amount\n'
    body = '\n'.join(f'{invoice},{lot},CNY,{price},CNY,{paid}' for lot, price in rows)
    return head + body + '\n'


DIRTY_ROWS = [('R-017', '1200.00'), ('W-041', '1000.00'), ('B-003', '800.00')]


def test_money_uses_integer_cents_with_fullwidth_and_grouping():
    assert cents('1,234.56') == cents('１２３４．５６') == 123456
    assert yuan(123456) == '1,234.56'
    with pytest.raises(ValueError):
        cents('12.345')


def test_discount_spread_by_list_price_then_actual_mass(dirty):
    parsed = parse_invoice(invoice(DIRTY_ROWS))
    assert parsed['invalid'] == []
    report = settle_invoice(parsed['invoices'][0], dirty.balance)
    # 折扣按原价占比：3000 原价、390 折扣，R-017 占 1200/3000。
    r017 = next(line for line in report['lines'] if line['lot'] == 'R-017')
    assert r017['discount_cents'] == 15600
    # 各批净价再按实称毫克比例摊到四种去向，含尾差补齐。
    for line in report['lines']:
        assert line['entry_cents'] == sum(line[k] for k in FLOWS)
        assert line['net_cents'] == line['entry_cents']
    # 上件、损耗、回桶、留罐入账金额（含尾差）恰好等于实付。
    assert sum(line[k] for line in report['lines'] for k in FLOWS) == report['paid_cents']
    assert report['entry_total_cents'] == report['paid_cents'] == 261000
    assert report['discount_total_cents'] == 39000


def test_residual_material_carries_discount_not_fully_expensed(dirty):
    """折扣不能全抵本期：回桶与留罐按实称分料承担折扣后的净价。"""
    report = settle_invoice(parse_invoice(invoice(DIRTY_ROWS))['invoices'][0], dirty.balance)
    w041 = next(line for line in report['lines'] if line['lot'] == 'W-041')
    # W-041 净价 870 元；回桶 80g/200g 与留罐 20g/200g 必须带价，未被高估为原价。
    assert w041['returned_cents'] == 34800 and w041['disputed_cents'] == 8700
    assert w041['list_cents'] == 100000 and w041['net_cents'] == 87000


def test_rounding_residue_is_deterministic_and_balances(clean):
    text = invoice([('R-017', '1500.00'), ('W-041', '1200.00'), ('B-003', '900.00')],
                   invoice='INV-2', paid='3060.00')
    report = settle_invoice(parse_invoice(text)['invoices'][0], clean.balance)
    totals = {key: sum(line[key] for line in report['lines']) for key in FLOWS}
    assert sum(totals.values()) == 306000
    # 同一份输入重复试算结果稳定（余数按批号定序）。
    again = settle_invoice(parse_invoice(text)['invoices'][0], clean.balance)
    assert again == report


def test_zero_discount_keeps_full_list_value(dirty):
    text = invoice(DIRTY_ROWS, paid='3000.00')
    report = settle_invoice(parse_invoice(text)['invoices'][0], dirty.balance)
    assert report['discount_total_cents'] == 0
    assert report['entry_total_cents'] == 300000
    assert {line['lot']: line['net_cents'] for line in report['lines']} == {
        'R-017': 120000, 'W-041': 100000, 'B-003': 80000}


def test_missing_and_unknown_lot_are_rejected(dirty):
    parsed = parse_invoice(invoice([('R-017', '1200.00'), ('GHOST', '5.00')]))
    with pytest.raises(LedgerError) as err:
        settle_invoice(parsed['invoices'][0], dirty.balance)
    assert err.value.code == 'INVOICE_LOT_UNKNOWN'
    with pytest.raises(LedgerError) as err:
        settle_invoice(parse_invoice(invoice(DIRTY_ROWS[:2]))['invoices'][0], dirty.balance)
    assert err.value.code == 'INVOICE_LOT_MISSING'


def test_bad_rows_are_isolated_not_guessed():
    text = ('invoice,lot,list_currency,list_amount,paid_currency,paid_amount\n'
            'INV-1,R-017,CNY,1200.00,CNY,2610.00\n'
            'INV-1,W-041,CNY,bogus,CNY,2610.00\n'
            'INV-1,B-003,USD,800.00,CNY,2610.00\n')
    parsed = parse_invoice(text)
    assert {row['code'] for row in parsed['invalid']} == {'BAD_MONEY', 'BAD_CURRENCY'}


def test_conflicting_paid_total_and_duplicate_lot_isolated():
    text = ('invoice,lot,list_currency,list_amount,paid_currency,paid_amount\n'
            'INV-1,R-017,CNY,1200.00,CNY,2610.00\n'
            'INV-1,W-041,CNY,1000.00,CNY,2609.00\n'
            'INV-1,R-017,CNY,1200.00,CNY,2610.00\n')
    codes = {row['code'] for row in parse_invoice(text)['invalid']}
    assert codes == {'INVOICE_TOTAL_CONFLICT', 'INVOICE_LOT_DUPLICATE'}


def test_paid_above_list_and_nonpositive_list_rejected(dirty):
    over = invoice([('R-017', '100.00'), ('W-041', '100.00'), ('B-003', '100.00')], paid='400.00')
    with pytest.raises(LedgerError) as err:
        settle_invoice(parse_invoice(over)['invoices'][0], dirty.balance)
    assert err.value.code == 'INVOICE_PAID_INVALID'
