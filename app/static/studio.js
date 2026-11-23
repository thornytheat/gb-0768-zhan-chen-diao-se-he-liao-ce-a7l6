const parcel = document.querySelector('#parcel');
const order = document.querySelector('#job-form');
const note = document.querySelector('#studio-feedback');
function target(sample, key) {
  const url = new URL(location.href);
  url.searchParams.set('sample', sample);
  if (key) url.searchParams.set('key', key);
  else url.searchParams.delete('key');
  return url;
}
parcel.addEventListener('change', () => location.assign(target(parcel.value)));
document.querySelector('#print-receipt').addEventListener('click', () => window.print());
order.addEventListener('submit', async event => {
  event.preventDefault();
  const button = order.querySelector('[type=submit]');
  button.disabled = true;
  note.hidden = false;
  note.textContent = '正在封存秤单';
  try {
    const response = await fetch('/api/books', {method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({sample: parcel.value})});
    const saved = await response.json();
    if (!response.ok) throw new Error(saved.message);
    const next = target(parcel.value, saved.key);
    if (next.href === location.href) note.textContent = '同一收据留档已复用';
    else location.assign(next);
  } catch (error) {
    note.textContent = error.message || '封存失败，请重试';
  } finally {
    button.disabled = false;
  }
});
const pots = document.querySelector('#pot-filter');
pots?.addEventListener('change', () => {
  for (const row of document.querySelectorAll('#pot-lines tr')) {
    row.hidden = pots.value !== 'all' && row.dataset.pot !== pots.value;
  }
});

const settleForm = document.querySelector('#settle-form');
settleForm?.addEventListener('submit', async event => {
  event.preventDefault();
  const button = settleForm.querySelector('[type=submit]');
  const feedback = document.querySelector('#settle-feedback');
  const result = document.querySelector('#settle-result');
  button.disabled = true;
  feedback.hidden = false;
  feedback.textContent = '正在结算入账';
  try {
    const response = await fetch('/api/settlements', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        key: settleForm.elements.key.value,
        invoice: settleForm.elements.invoice.value
      })
    });
    const saved = await response.json();
    if (!response.ok) throw new Error(saved.message);
    feedback.textContent = `发票 ${saved.invoice} 已入账，可重试同一发票验证幂等`;
    result.hidden = false;
    result.innerHTML = renderSettlement(saved.report);
    setTimeout(() => location.reload(), 1200);
  } catch (error) {
    // 结算失败不留半份账，保留原件以便修正后重试。
    feedback.textContent = error.message || '结算失败，可修正后重试';
    result.hidden = true;
  } finally {
    button.disabled = false;
  }
});

function yuan(cents) {
  return '¥' + (cents / 100).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function renderSettlement(report) {
  const head = '<table><thead><tr><th>批号</th><th>原价</th><th>折扣</th><th>上件</th><th>损耗</th><th>回桶</th><th>留罐</th><th>入账合计</th></tr></thead><tbody>';
  const rows = report.lines.map(line =>
    `<tr><th scope="row">${line.lot}</th><td>${yuan(line.list_cents)}</td><td>${yuan(line.discount_cents)}</td>` +
    `<td>${yuan(line.applied_cents)}</td><td>${yuan(line.loss_cents)}</td><td>${yuan(line.returned_cents)}</td>` +
    `<td>${yuan(line.disputed_cents)}</td><td>${yuan(line.entry_cents)}</td></tr>`).join('');
  const foot = `</tbody></table><p class="hint">原价 ${yuan(report.list_total_cents)} － 折扣 ${yuan(report.discount_total_cents)} ` +
    `＝ 实付 ${yuan(report.paid_cents)}；入账合计 ${yuan(report.entry_total_cents)}，尾差已并入。</p>`;
  return head + rows + foot;
}
