import json
import os
import re
from hashlib import sha256
from tempfile import NamedTemporaryFile

from app.blend.invoice import parse_invoice, settle_invoice
from app.faults import LedgerError


class SettlementShelf:
    """发票入账册：同发票只入一次；临时文件同步后原子硬链接发布，半份账不落盘。"""

    def __init__(self, directory):
        self.directory = directory

    def _ensure(self):
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, identifier):
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', identifier):
            raise LedgerError('INVOICE_INVALID', '发票编号不合法', 400)
        return self.directory / f'{identifier}.settlement'

    def find_invoice(self, identifier):
        try:
            return json.loads(self._path(identifier).read_bytes())
        except FileNotFoundError:
            return None

    def listing(self):
        if not self.directory.exists():
            return []
        kept = sorted(self.directory.glob('*.settlement'), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        return [json.loads(path.read_bytes()) for path in kept[:60]]

    def post(self, identifier, record):
        target = self._path(identifier)
        if target.exists():
            raise LedgerError('SETTLEMENT_DUPLICATE', '同一张发票已入账，不得重复入账', 409)
        self._ensure()
        if target.exists():
            raise LedgerError('SETTLEMENT_DUPLICATE', '同一张发票已入账，不得重复入账', 409)
        with NamedTemporaryFile(dir=self.directory, prefix='.settle-') as temp:
            temp.write(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())
            temp.flush()
            os.fsync(temp.fileno())
            try:
                os.link(temp.name, target)
            except FileExistsError:
                raise LedgerError('SETTLEMENT_DUPLICATE', '同一张发票已入账，不得重复入账', 409) from None
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return record


def post_settlement(books, shelf, payload):
    """试算通过才入账；任何校验失败都不产生留档，调用方可用同一发票重试。"""
    if not isinstance(payload, dict):
        raise LedgerError('INVOICE_INVALID', '入账请求体无效')
    key = payload.get('key')
    invoice_text = payload.get('invoice')
    if not isinstance(key, str) or not isinstance(invoice_text, str) or len(invoice_text) > 150_000:
        raise LedgerError('INVOICE_INVALID', '需提供留档号与一份 UTF-8 发票表单')
    book = books.require(key)
    parsed = parse_invoice(invoice_text)
    if parsed['invalid']:
        raise LedgerError('INVOICE_INVALID', '发票存在隔离行，无法整单入账')
    if not parsed['invoices']:
        raise LedgerError('INVOICE_EMPTY', '发票没有可入账行')
    if len(parsed['invoices']) > 1:
        raise LedgerError('INVOICE_INVALID', '一次入账只能对应一张发票')
    invoice = parsed['invoices'][0]
    if shelf.find_invoice(invoice['invoice']):
        raise LedgerError('SETTLEMENT_DUPLICATE', '同一张发票已入账，不得重复入账', 409)
    report = settle_invoice(invoice, book['snapshot']['balance'])
    record = {'invoice': report['invoice'], 'book_key': key, 'order': book['manifest']['order'],
              'report': report, 'invoice_sha256': sha256(invoice_text.encode()).hexdigest()}
    return shelf.post(invoice['invoice'], record)
