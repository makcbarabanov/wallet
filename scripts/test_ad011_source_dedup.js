/**
 * AD-011 source dedup: confident before tutor; possible asked separately; new → Разбор.
 *
 *   docker run --rm -v "$PWD":/w -w /w node:20-alpine node scripts/test_ad011_source_dedup.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const HTML = path.join(__dirname, '..', 'review_categorization.html');
const html = fs.readFileSync(HTML, 'utf8');

function extract(name) {
  let start = html.indexOf(`function ${name}(`);
  if (start < 0) start = html.indexOf(`function* ${name}(`);
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
    if (html[i] === '{') { depth += 1; seen = true; }
    else if (html[i] === '}') {
      depth -= 1;
      if (seen && depth === 0) return html.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced braces in ${name}()`);
}

const stubs = `
const state = {
  personal: [],
  business: [],
  importedOps: [],
  purchases: [],
  drawerOps: [],
  rejectedOps: [],
  reconciliationLinks: [],
  cardAccountLinks: [],
  merchants: {},
  categories: [],
  confirmedIds: new Set(),
  deletedIds: new Set(),
};
const DATA = { sections: [], expensePresets: {}, storeDirectory: {} };
function ensureImportedOps() {}
function ensureLearnState() {}
function ensurePurchases() { if (!Array.isArray(state.purchases)) state.purchases = []; }
function ensureDrawerOps() { if (!Array.isArray(state.drawerOps)) state.drawerOps = []; }
function ensureRejectedOps() { if (!Array.isArray(state.rejectedOps)) state.rejectedOps = []; }
function seedMerchantsFromHistory() {}
function openDrawerOps() { return []; }
function isGone() { return false; }
function preferAccountId() { return 'acc1'; }
function getMerchant() { return null; }
function topOutcome() { return null; }
function canonicalStoreName(s) { return s; }
function nextImportOpId() {
  nextImportOpId._n = (nextImportOpId._n || 900000) + 1;
  return nextImportOpId._n;
}
function sourceOperationById() { return null; }
function bankIdFromSource() { return ''; }
function candidateBankId() { return ''; }
`;

const names = [
  'normForSearch',
  'translitFold',
  'datePartIso',
  'rowDateToIso',
  'opFingerprint',
  'opHardFingerprint',
  'opSoftKey',
  'opSoftNormKey',
  'rowIdentityStore',
  'addOpFingerprintAliases',
  'rowMatchesKnownHard',
  'productNameCanonical',
  'productNameSimilar',
  'isReceiptSide',
  'rowsMatchSameOp',
  'findSimilarRecordedExpense',
  'iterRecordedOps',
  'rowMatchesRecordedOp',
  'isReceiptDuplicateInput',
  'normalizeStoreForGateway',
  'meaningfulStoreTokens',
  'storeMatchSignal',
  'storesRelated',
  'storeKey',
  'normalizeStoreKey',
  'storeNameVariants',
  'amountSignVariants',
  'absCost',
  'duplicateExplanation',
  'sourceTypeFromLabel',
  'extractPhoneDigits',
  'extractCardHints',
  'extractAccountHints',
  'normalizePhoneId',
  'bankMatchIdentifiers',
  'ledgerMatchIdentifiers',
  'identifiersOverlap',
  'normalizeCardMask',
  'normalizeReconciliationLink',
  'normalizeCardAccountLink',
  'ensureReconciliationLinks',
  'ensureCardAccountLinks',
  'buildBankReconFingerprint',
  'bankReconLookupKeys',
  'findExpenseByOpId',
  'findReconciliationLinkForRow',
  'accountIdForCardHint',
  'rememberCardAccountLink',
  'rememberReconciliationLink',
  'findDuplicateCandidates',
  'classifyImportRowDuplicate',
  'collectKnownHardFingerprints',
  'collectKnownSoftKeys',
  'collectKnownFiscalIds',
  'planImportStatementRows',
  'personNameKey',
  'stripTransferPrefix',
  'accountHintFromBank',
];

// translitFold uses _TRANSLIT_MAP const — extract nearby snippet
const translitMapMatch = html.match(/const _TRANSLIT_MAP = \{[\s\S]*?\n\};/);
assert.ok(translitMapMatch, '_TRANSLIT_MAP not found');
const storeServiceMatch = html.match(/const STORE_SERVICE_WORDS = new Set\(\[[\s\S]*?\]\);/);
assert.ok(storeServiceMatch, 'STORE_SERVICE_WORDS not found');

const body = stubs + storeServiceMatch[0] + '\n' + translitMapMatch[0] + '\n' + names.map(extract).join('\n');
const fn = new Function(`${body}; return {
  planImportStatementRows,
  productNameSimilar,
  classifyImportRowDuplicate,
  findDuplicateCandidates,
  getState: () => state,
  reset() {
    state.personal.length = 0;
    state.business.length = 0;
    state.importedOps.length = 0;
    state.purchases.length = 0;
    state.reconciliationLinks.length = 0;
    state.cardAccountLinks.length = 0;
    nextImportOpId._n = 900000;
  },
};`);
const api = fn();
const state = api.getState();

function reset() { api.reset(); }

function receiptLine(store, amount, product, date = '2026-07-24') {
  return {
    date,
    store,
    amount: -Math.abs(amount),
    kind: 'expense',
    comment: product,
    productName: product,
    sourceType: 'receipt',
    bank: { receiptItem: true, desc: product, merchant: store },
  };
}

// --- 1) Полный дубль: повтор того же чека → 0 в обучение ---
reset();
state.personal.push({
  opId: 1, date: '26.07.26', store: 'МАГНИТ', expense: 'РЯБА Майонез Провансаль',
  cost: 130, accountId: 'acc1',
});
state.personal.push({
  opId: 2, date: '26.07.26', store: 'МАГНИТ', expense: 'Хлеб Дарницкий',
  cost: 16, accountId: 'acc1',
});
const fullReceipt = [
  receiptLine('МАГНИТ', 130, 'РЯБА Майонез Провансаль', '2026-07-26'),
  receiptLine('МАГНИТ', 16, 'Хлеб Дарницкий', '2026-07-26'),
];
let plan = api.planImportStatementRows(fullReceipt);
assert.strictEqual(plan.skippedHard, 2, 'full re-upload: both exact skipped');
assert.strictEqual(plan.reviewItems.length, 0, 'full re-upload: no review items');
assert.strictEqual((plan.possibleDupItems || []).length, 0, 'full: no possible');
assert.strictEqual(plan.autoItems.length, 0);

// --- 2) Частичный: 2 есть + 1 новая ---
plan = api.planImportStatementRows([
  ...fullReceipt,
  receiptLine('МАГНИТ', 50, 'ЧЕСНОК', '2026-07-26'),
]);
assert.strictEqual(plan.skippedHard, 2, 'partial: 2 exact');
assert.strictEqual(plan.reviewItems.length, 1, 'partial: 1 new in Разбор');
assert.strictEqual(plan.reviewItems[0].productName, 'ЧЕСНОК');
assert.strictEqual((plan.possibleDupItems || []).length, 0);

// --- 3) Похожий товар, другая цена (37 vs 38) → possible, НЕ обычный Разбор ---
reset();
state.personal.push({
  opId: 3, date: '24.07.26', store: "ООО 'СТРОЙТОРГОВЛЯ'", expense: 'Изолента ECON',
  cost: 37, accountId: 'acc1',
});
plan = api.planImportStatementRows([
  receiptLine("ООО 'СТРОЙТОРГОВЛЯ'", 38, 'Изолента ECON'),
]);
assert.strictEqual(plan.reviewItems.length, 0, 'similar must NOT enter normal tutor queue');
assert.strictEqual((plan.possibleDupItems || []).length, 1, 'similar → possibleDupItems');
assert.strictEqual(plan.possibleDupItems[0].duplicateHint, 'similar');
assert.ok(plan.softDup >= 1);

// --- 4) Новый товар ---
reset();
plan = api.planImportStatementRows([
  receiptLine('МАГНИТ', 99, 'Совершенно новый товар', '2026-07-26'),
]);
assert.strictEqual(plan.skippedHard, 0);
assert.strictEqual(plan.reviewItems.length, 1, 'new → Разбор');
assert.strictEqual((plan.possibleDupItems || []).length, 0);

// --- 5) Вимос: OCR drift магазин + пунктуация + «д»/«d» → уверенный дубль, не обучение ---
reset();
state.business.push({
  opId: 900029,
  date: '24.07.26',
  store: "ООО 'СТРОЙТОРГОВЛЯ'",
  expense: 'Тарелка опорная резиновая под круг на липучке, d 125 мм, шпилька d 8 мм',
  cost: 360,
  accountId: 'acc1',
});
plan = api.planImportStatementRows([
  receiptLine(
    'ТД Вимос',
    360,
    'Тарелка опорная резиновая под круг на липучке. d 125 мм шпилька д 8 мм'
  ),
]);
assert.strictEqual(plan.reviewItems.length, 0, 'Vimos plate must not enter tutor');
assert.strictEqual((plan.possibleDupItems || []).length, 0, 'Vimos plate is confident, not possible');
assert.strictEqual(plan.skippedHard, 1, 'Vimos plate skipped as confident');

// --- 6) Инвариант: ни одна existing exact/similar-amount line не в reviewItems ---
reset();
state.business.push({
  opId: 10, date: '24.07.26', store: 'ВИМОС', expense: 'Выключатель эл.', cost: 208, accountId: 'acc1',
});
state.business.push({
  opId: 11, date: '24.07.26', store: 'ВИМОС', expense: 'Тарелка опорная', cost: 360, accountId: 'acc1',
});
state.business.push({
  opId: 12, date: '24.07.26', store: 'ВИМОС', expense: 'Изолента', cost: 37, accountId: 'acc1',
});
plan = api.planImportStatementRows([
  receiptLine('ВИМОС', 208, 'Выключатель эл.'),
  receiptLine('ВИМОС', 360, 'Тарелка опорная'),
  receiptLine('ВИМОС', 37, 'Изолента'),
  receiptLine('ВИМОС', 56, 'Зажигалка пьезо Flameclub'),
]);
assert.strictEqual(plan.skippedHard, 3, '3 existing confident');
assert.strictEqual(plan.reviewItems.length, 1, 'only lighter is new');
assert.strictEqual(plan.reviewItems[0].productName, 'Зажигалка пьезо Flameclub');
assert.ok(
  plan.reviewItems.every((r) => !r.softDup),
  'invariant: no existing/soft row in normal reviewItems'
);

// --- 7) fiscal Exact ---
reset();
state.purchases.push({
  id: 'p1', fiscalId: 'FP-999', date: '2026-07-26', store: 'МАГНИТ',
  totalAmount: 100, expenseOpIds: ['1'], status: 'confirmed',
});
plan = api.planImportStatementRows([
  { ...receiptLine('МАГНИТ', 100, 'Новый товар', '2026-07-26'), fiscalId: 'FP-999' },
]);
assert.strictEqual(plan.skippedHard, 1, 'fiscal Exact skips line');
assert.strictEqual(plan.reviewItems.length, 0);

// --- 8) Другой день тот же товар = новая запись ---
reset();
state.personal.push({
  opId: 4, date: '25.07.26', store: 'МАГНИТ', expense: 'РЯБА Майонез Провансаль', cost: 130, accountId: 'acc1',
});
plan = api.planImportStatementRows([
  receiptLine('МАГНИТ', 130, 'РЯБА Майонез Провансаль', '2026-07-26'),
]);
assert.strictEqual(plan.skippedHard, 0, 'other day is not exact');
assert.strictEqual(plan.reviewItems.length, 1, 'other day → new proposal');

console.log('AD-011 source dedup OK');
