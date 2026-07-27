/**
 * Receipt line math: unit/qty/price/amount + never auto-fix doubt.
 *
 *   docker run --rm -v "$PWD":/w -w /w node:20-alpine node scripts/test_receipt_line_math.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const HTML = path.join(__dirname, '..', 'review_categorization.html');
const html = fs.readFileSync(HTML, 'utf8');

function extract(name) {
  let start = html.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `function ${name}() not found`);
  let i = html.indexOf('(', start);
  let parens = 0;
  for (; i < html.length; i += 1) {
    if (html[i] === '(') parens += 1;
    else if (html[i] === ')') {
      parens -= 1;
      if (parens === 0) break;
    }
  }
  let depth = 0;
  let seen = false;
  for (i = html.indexOf('{', i); i < html.length; i += 1) {
    if (html[i] === '{') { depth += 1; seen = true; }
    else if (html[i] === '}') {
      depth -= 1;
      if (seen && depth === 0) return html.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced ${name}`);
}

assert.ok(html.includes('id="valetDoubtModal"'), 'valetDoubtModal missing');
assert.ok(Number(html.match(/UI_BUILD\s*=\s*(\d+)/)[1]) >= 75, 'UI_BUILD should be >= 75');
assert.ok(html.includes('resolveReceiptAmountDoubtsOnRows'), 'doubt resolver missing');
assert.ok(html.includes('qty: op.qty'), 'createExpense must pass qty');
assert.ok(html.includes('unit: op.unit'), 'createExpense must pass unit');
assert.ok(html.includes('unitPrice: op.unitPrice'), 'createExpense must pass unitPrice');
assert.ok(html.includes('receiptFields = (row)'), 'receiptFields helper');
assert.ok(/qty:\s*row\.qty/.test(html), 'plan must keep qty');

const code = [
  extract('normalizeReceiptCategory'),
  extract('receiptMoneyTol'),
  extract('receiptApproxEq'),
  extract('inferReceiptUnit'),
  extract('normalizeReceiptLineItem'),
].join('\n');

const fns = {};
// eslint-disable-next-line no-new-func
new Function(`${code}; Object.assign(this, {
  normalizeReceiptCategory,
  receiptMoneyTol,
  receiptApproxEq,
  inferReceiptUnit,
  normalizeReceiptLineItem,
});`).call(fns);

const ok = fns.normalizeReceiptLineItem({
  name: 'ПЕРЕЦ ЧИЛИ КРАСНЫЙ 1КГ',
  qty: 0.084,
  price: 1169.91,
  amount: 98.27,
});
assert.strictEqual(ok.amountStatus, 'ok');
assert.strictEqual(ok.unit, 'кг');
assert.ok(Math.abs(ok.amount - 98.27) < 0.01);
assert.ok(Math.abs(ok.price - 1169.91) < 0.01);
assert.ok(Math.abs(ok.qty - 0.084) < 1e-6);

const bad = fns.normalizeReceiptLineItem({
  name: 'ПЕРЕЦ ЧИЛИ КРАСНЫЙ 1КГ',
  qty: 0.084,
  price: 1169.91,
  amount: 1169.91,
});
assert.strictEqual(bad.amountStatus, 'doubt');
assert.strictEqual(bad.amountDoubt.kind, 'unit_price_as_amount');
assert.ok(Math.abs(bad.amount - 1169.91) < 0.01, 'must NOT auto-fix amount');
assert.ok(Math.abs(bad.amountDoubt.candidates[0].amount - 98.27) < 0.02);

const piece = fns.normalizeReceiptLineItem({
  name: 'Хлеб', qty: 1, price: 129.99, amount: 129.99,
});
assert.strictEqual(piece.amountStatus, 'ok');
assert.strictEqual(piece.unit, 'шт');

const conflict = fns.normalizeReceiptLineItem({
  name: 'X', qty: 2, price: 50, amount: 120,
});
assert.strictEqual(conflict.amountStatus, 'doubt');
assert.strictEqual(conflict.amountDoubt.kind, 'conflict');
assert.ok(Math.abs(conflict.amount - 120) < 0.01, 'conflict must not autofix');

console.log('test_receipt_line_math: PASS');
