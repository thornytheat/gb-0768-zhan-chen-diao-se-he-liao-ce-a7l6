from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.ledger.settlement import SettlementShelf, post_settlement
from app.settings import DATA
from app.faults import LedgerError


def sample_invoice(name='dirty', identifier='INV-1180'):
    text = (DATA / name / 'invoice.csv').read_text()
    return text if identifier == 'INV-1180' else text.replace('INV-1180', identifier)


def sealed_key(client, name='dirty'):
    return client.post('/api/books', json={'sample': name}).json['key']


def test_post_balances_and_links_to_book(client):
    key = sealed_key(client)
    response = client.post('/api/settlements', json={'key': key, 'invoice': sample_invoice()})
    assert response.status_code == 200
    body = response.json
    report = body['report']
    assert body['invoice'] == 'INV-1180' and body['reused'] is False
    assert report['entry_total_cents'] == report['paid_cents']
    saved = client.get('/api/settlements/INV-1180').json
    assert saved['book_key'] == key and 'invoice_sha256' in saved


def test_same_invoice_is_never_posted_twice(client):
    key = sealed_key(client)
    first = client.post('/api/settlements', json={'key': key, 'invoice': sample_invoice()})
    second = client.post('/api/settlements', json={'key': key, 'invoice': sample_invoice()})
    assert first.status_code == 200 and (second.status_code, second.json['code']) == (409, 'SETTLEMENT_DUPLICATE')
    assert len(client.get('/api/settlements').json['settlements']) == 1


def test_failed_settlement_leaves_no_half_ledger_and_can_retry(client, tmp_path):
    key = sealed_key(client)
    bad = 'invoice,lot,list_currency,list_amount,paid_currency,paid_amount\nINV-9,R-017,CNY,abc,CNY,100\n'
    failed = client.post('/api/settlements', json={'key': key, 'invoice': bad})
    assert failed.status_code == 422
    shelf_dir = tmp_path / 'settlements'
    assert not shelf_dir.exists() or not list(shelf_dir.glob('*.settlement'))
    #修正原件后用同一张发票重试即可成功。
    ok = client.post('/api/settlements', json={'key': key, 'invoice': sample_invoice(identifier='INV-9')})
    assert ok.status_code == 200 and ok.json['invoice'] == 'INV-9'


def test_unknown_book_and_malformed_payload_are_recoverable(client):
    resp = client.post('/api/settlements', json={'key': 'f' * 64, 'invoice': sample_invoice()})
    assert (resp.status_code, resp.json['code']) == (404, 'BOOK_UNKNOWN')
    assert client.post('/api/settlements', json={}).status_code == 422
    assert client.post('/api/settlements').status_code == 422


def test_settlement_requires_matching_book_lots(client):
    key = sealed_key(client, 'clean')
    # dirty 发票批号与 clean 工单批号集合相同，但原价行可解析；用缺行发票触发批号缺失。
    partial = ('invoice,lot,list_currency,list_amount,paid_currency,paid_amount\n'
               'INV-3,R-017,CNY,1500.00,CNY,3060.00\nINV-3,W-041,CNY,1200.00,CNY,3060.00\n')
    resp = client.post('/api/settlements', json={'key': key, 'invoice': partial})
    assert (resp.status_code, resp.json['code']) == (422, 'INVOICE_LOT_MISSING')


def test_concurrent_posts_commit_exactly_once(tmp_path):
    from app.ledger.bookshelf import Bookshelf
    from app.ledger.closeout import close
    books = Bookshelf(tmp_path)
    record, _ = close(books, {'sample': 'dirty'})
    shelf = SettlementShelf(tmp_path / 'settlements')
    payload = {'key': record['manifest']['key'], 'invoice': sample_invoice()}
    with ThreadPoolExecutor(4) as pool:
        outcomes = list(pool.map(lambda _: _run(books, shelf, payload), range(4)))
    successes = [o for o in outcomes if o == 'OK']
    rejects = [o for o in outcomes if o == 'DUP']
    assert (len(successes), len(rejects)) == (1, 3)
    assert len(list((tmp_path / 'settlements').glob('*.settlement'))) == 1


def _run(books, shelf, payload):
    try:
        post_settlement(books, shelf, payload)
        return 'OK'
    except LedgerError as error:
        assert error.code == 'SETTLEMENT_DUPLICATE'
        return 'DUP'


def test_settlement_page_lists_entries(client):
    key = sealed_key(client)
    client.post('/api/settlements', json={'key': key, 'invoice': sample_invoice()})
    page = client.get('/studio/settlement?key=' + key).get_data(as_text=True)
    assert '发票整单折扣入账' in page and 'INV-1180' in page and '2,610.00' in page
