/**
 * Drawer restore: «Вернуть назад» returns parked op to Разбор queue.
 *
 *   docker run --rm -v "$PWD":/w -w /w node:20-alpine node scripts/test_drawer_restore.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const HTML = path.join(__dirname, '..', 'review_categorization.html');
const html = fs.readFileSync(HTML, 'utf8');

assert.ok(html.includes('data-drawer-restore'), 'restore button in drawer UI');
assert.ok(html.includes('Вернуть назад'), 'label Вернуть назад');
assert.ok(html.includes('function restoreDrawerOpToReview'), 'restoreDrawerOpToReview');
assert.ok(html.includes('function importedOpFromDrawer'), 'importedOpFromDrawer');
assert.ok(Number(html.match(/UI_BUILD\s*=\s*(\d+)/)[1]) >= 77, 'UI_BUILD >= 77');

function extract(name) {
  let start = html.indexOf(`function ${name}(`);
  assert.ok(start > 0, `function ${name}() not found`);
  let parens = 0;
  let i = html.indexOf('(', start);
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
    if (html[i] === '{') {
      depth += 1;
      seen = true;
    } else if (html[i] === '}') {
      depth -= 1;
      if (seen && depth === 0) return html.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced braces in ${name}()`);
}

const sandbox = {
  console,
  SOURCE_STATUSES: new Set(['queued', 'confirmed', 'rejected', 'drawer']),
  DUPLICATE_RESOLUTIONS: new Set(['none', 'use_existing', 'create_new', 'skip']),
  sourceTypeFromLabel: (v) => {
    const s = String(v || '').toLowerCase();
    if (s.includes('receipt') || s.includes('чек')) return 'receipt';
    if (s.includes('excel') || s.includes('csv')) return 'excel';
    return s || 'excel';
  },
  normalizeStoreForGateway: (s) => String(s || '').toLowerCase().trim(),
  bankIdFromSource: () => null,
  buildSourceFingerprint: (o) =>
    [o.date, o.store, o.amount, o.comment || ''].join('|'),
  nextDrawerId: () => 'dr_test',
  nextImportOpId: () => 4242,
};
vm.createContext(sandbox);
vm.runInContext(extract('normalizeSourceOperation'), sandbox);
vm.runInContext(extract('normalizeDrawerOp'), sandbox);
vm.runInContext(extract('importedOpFromDrawer'), sandbox);

const parked = sandbox.normalizeDrawerOp({
  id: 'dr1',
  sourceOpId: '901',
  date: '01.07.26',
  store: 'Лента',
  storeRaw: 'Лента',
  amount: -350,
  kind: 'expense',
  comment: '',
  fingerprint: 'fp1',
  reason: 'отложено',
  source: 'tutor',
  productName: '',
  sourceType: 'excel',
  status: 'open',
});
assert.strictEqual(parked.sourceOpId, '901');

const restored = sandbox.importedOpFromDrawer(parked);
assert.ok(restored);
assert.strictEqual(String(restored.id), '901');
assert.strictEqual(restored.store, 'Лента');
assert.strictEqual(Number(restored.amount), -350);
assert.strictEqual(restored.status, 'queued');

const receiptParked = sandbox.normalizeDrawerOp({
  id: 'dr2',
  sourceOpId: '902',
  date: '01.07.26',
  store: 'Молоко',
  storeRaw: 'Магнит',
  amount: -89,
  productName: 'Молоко',
  receiptMerchant: 'Магнит',
  sourceType: 'receipt',
  unit: 'шт',
  qty: 1,
  price: 89,
  status: 'open',
});
const receiptRestored = sandbox.importedOpFromDrawer(receiptParked);
assert.strictEqual(receiptRestored.productName, 'Молоко');
assert.strictEqual(receiptRestored.receiptMerchant, 'Магнит');
assert.strictEqual(receiptRestored.unit, 'шт');
assert.strictEqual(Number(receiptRestored.qty), 1);

console.log('OK test_drawer_restore');
