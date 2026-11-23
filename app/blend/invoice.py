from app.blend.money import cents
from app.faults import LedgerError
from app.receipts.sheet import scan

INVOICE_SHEET = 'invoice'
FLOW_CENTS = ('applied_cents', 'loss_cents', 'returned_cents', 'disputed_cents')
FLOW_MG = ('applied_mg', 'loss_mg', 'returned_mg', 'disputed_mg')
FLOW_PAIRS = tuple(zip(FLOW_CENTS, FLOW_MG))


def parse_invoice(text):
    """解析发票原件：发票层实付总额 + 各批原价，坏行隔离不凭推测补账。"""
    records, summary = scan(text, INVOICE_SHEET)
    by_invoice, invalid = {}, []
    for row in records:
        fields = row['data']
        try:
            invoice = fields['invoice'].strip()
            lot = fields['lot'].strip()
            if not invoice or not lot:
                raise ValueError('BAD_KEY')
            if fields['list_currency'].strip().upper() != 'CNY' or fields['paid_currency'].strip().upper() != 'CNY':
                raise ValueError('BAD_CURRENCY')
            list_value = cents(fields['list_amount'])
            paid_value = cents(fields['paid_amount'])
        except ValueError as error:
            invalid.append(row | {'code': str(error)})
            continue
        current = by_invoice.setdefault(invoice, {'invoice': invoice, 'paid_cents': paid_value,
                                                  'lines': [], 'line_set': set()})
        if paid_value != current['paid_cents']:
            invalid.append(row | {'code': 'INVOICE_TOTAL_CONFLICT'})
            continue
        if lot in current['line_set']:
            invalid.append(row | {'code': 'INVOICE_LOT_DUPLICATE'})
            continue
        current['line_set'].add(lot)
        current['lines'].append({'lot': lot, 'list_cents': list_value, 'evidence': row})
    invoices = list(by_invoice.values())
    return {'invoices': invoices, 'invalid': invalid, 'summary': summary}


def _largest_remainder(pool, weights):
    """按权重把整数池分尽：商取整后按余数降序、键升序补尾差。"""
    base = sum(weights.values())
    if base == 0:
        return {key: 0 for key in weights}
    quotient = {key: divmod(pool * weight, base) for key, weight in weights.items()}
    shares = {key: part for key, (part, _) in quotient.items()}
    left = pool - sum(shares.values())
    for key in sorted(quotient, key=lambda k: (-quotient[k][1], k))[:left]:
        shares[key] += 1
    return shares


def settle_invoice(invoice, balance):
    """一张发票的整单折扣按各批原价占比摊到批次，再按实称分料摊到四种去向。

    上件、损耗、回桶、留罐的入账金额连同尾差必须恰好等于实付，半份账不入册。
    """
    lines = {row['lot']: row for row in balance['lines']}
    missing = [item['lot'] for item in invoice['lines'] if item['lot'] not in lines]
    if missing:
        raise LedgerError('INVOICE_LOT_UNKNOWN', f'发票批号未在工单留档：{",".join(sorted(missing))}')
    priced = {item['lot']: item for item in invoice['lines']}
    unpriced = sorted(set(lines) - set(priced))
    if unpriced:
        raise LedgerError('INVOICE_LOT_MISSING', f'工单批号缺发票原价：{",".join(unpriced)}')
    list_total = sum(item['list_cents'] for item in invoice['lines'])
    paid_total = invoice['paid_cents']
    if list_total <= 0:
        raise LedgerError('INVOICE_LIST_INVALID', '发票原价合计必须为正')
    if not 0 <= paid_total <= list_total:
        raise LedgerError('INVOICE_PAID_INVALID', '实付须介于零与原价合计之间')

    list_weights = {lot: priced[lot]['list_cents'] for lot in sorted(lines)}
    net_by_lot = _largest_remainder(paid_total, list_weights)
    rows, check = [], 0
    for lot in sorted(lines):
        mass = lines[lot]
        weights = {cent_key: mass[mg_key] for cent_key, mg_key in FLOW_PAIRS}
        allocated = _largest_remainder(net_by_lot[lot], weights)
        entry = {'lot': lot, 'list_cents': priced[lot]['list_cents'], 'net_cents': net_by_lot[lot],
                 'discount_cents': priced[lot]['list_cents'] - net_by_lot[lot], **allocated,
                 'entry_cents': sum(allocated.values())}
        rows.append(entry)
        check += entry['entry_cents']
    discount_total = list_total - paid_total
    report = {'invoice': invoice['invoice'], 'lines': rows,
              'list_total_cents': list_total, 'paid_cents': paid_total,
              'discount_total_cents': discount_total, 'entry_total_cents': check}
    if check != paid_total or sum(r['discount_cents'] for r in rows) != discount_total:
        raise LedgerError('SETTLEMENT_UNBALANCED', '入账金额与实付无法轧平')
    return report
