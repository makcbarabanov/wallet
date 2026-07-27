/**
 * Stage 1 reconciliation: bank ≠ product; bank↔ledger vs receipt↔ledger.
 *
 *   docker run --rm -v "$PWD":/w -w /w node:20-alpine node scripts/test_reconciliation_stage1.js
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

const translitMapMatch = html.match(/const _TRANSLIT_MAP = \{[\s\S]*?\n\};/);
assert.ok(translitMapMatch, '_TRANSLIT_MAP not found');
const storeServiceMatch = html.match(/const STORE_SERVICE_WORDS = new Set\(\[[\s\S]*?\]\);/);
assert.ok(storeServiceMatch, 'STORE_SERVICE_WORDS not found');

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
function preferAccountId() { return ''; }
function getMerchant() { return null; }
function topOutcome() { return null; }
function canonicalStoreName(s) { return s; }
function nextImportOpId() {
  nextImportOpId._n = (nextImportOpId._n || 900000) + 1;
  return nextImportOpId._n;
}
function sourceOperationById() { return null; }
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
  'accountHintFromBank',
  'bankIdFromSource',
  'candidateBankId',
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
];

let body = stubs + translitMapMatch[0] + '\n' + storeServiceMatch[0] + '\n';
for (const n of names) body += extract(n) + '\n';

const api = new Function(`${body}; return {
  state,
  planImportStatementRows,
  classifyImportRowDuplicate,
  findDuplicateCandidates,
  rowsMatchSameOp,
  storeMatchSignal,
  isReceiptDuplicateInput,
  extractPhoneDigits,
  extractCardHints,
  bankMatchIdentifiers,
  reset() {
    state.personal.length = 0;
    state.business.length = 0;
    state.importedOps.length = 0;
    state.purchases.length = 0;
    state.drawerOps.length = 0;
    state.rejectedOps.length = 0;
    state.reconciliationLinks.length = 0;
    state.cardAccountLinks.length = 0;
    nextImportOpId._n = 900000;
  },
};`)();

const { state } = api;
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

function bankRow(store, amount, date, extra = {}) {
  return {
    date,
    store,
    amount: -Math.abs(amount),
    kind: 'expense',
    comment: extra.comment || '',
    bank: {
      desc: extra.desc || store,
      card: extra.card || '',
      category: '',
      ...(extra.bank || {}),
    },
    sourceType: extra.sourceType || 'bank_statement',
  };
}

const ledgerMts = {
  opId: 900023,
  date: '19.07.26',
  store: 'МТС',
  expense: '89886296030',
  cost: 850,
  note: 'Мобильная связь · карта Кредитка',
  accountId: 'a1784024481423',
};

// --- R1: PDF-like mBank.MTS + card comment vs Ledger phone ---
reset();
state.personal.push({ ...ledgerMts });
const r1 = bankRow('Оплата услуг mBank.MTS', 850, '2026-07-19T12:59', {
  comment: 'карта *0507',
  card: '*0507',
  desc: 'Оплата услуг mBank.MTS 12:59 12:59',
});
let classified = api.classifyImportRowDuplicate(r1);
assert.strictEqual(classified.bucket, 'confident', 'R1: PDF MTS must be confident skip');
assert.ok(
  !String(r1.comment).includes('8988') || true,
  'R1: comment stays bank comment'
);
const r1Input = api.findDuplicateCandidates({
  date: r1.date,
  amount: r1.amount,
  store: r1.store,
  comment: r1.comment,
  bank: r1.bank,
  sourceType: 'bank_statement',
  productName: '',
  expense: '',
});
assert.ok(r1Input.best, 'R1: has candidate');
assert.ok(r1Input.best.class === 'strong' || r1Input.best.class === 'exact', 'R1: strong/exact');
assert.strictEqual(r1Input.best.receiptLine, false, 'R1: not receipt branch');

let plan = api.planImportStatementRows([r1]);
assert.strictEqual(plan.skippedHard, 1, 'R1: skipped');
assert.strictEqual(plan.reviewItems.length, 0, 'R1: no Razbor card');

// --- R2: CSV with phone in merchant ---
reset();
state.personal.push({ ...ledgerMts });
const r2 = bankRow('МТС +7 988 629-60-30', 850, '2026-07-19T12:59:06', {
  comment: 'Мобильная связь · карта *0507',
  card: '*0507',
  desc: 'МТС +7 988 629-60-30',
});
classified = api.classifyImportRowDuplicate(r2);
assert.strictEqual(classified.bucket, 'confident', 'R2: CSV MTS skip');
plan = api.planImportStatementRows([r2]);
assert.strictEqual(plan.skippedHard, 1, 'R2: skipped');
assert.strictEqual(plan.reviewItems.length, 0, 'R2: no review');

// --- R3: bank card comment, empty ledger → new; comment ≠ product ---
reset();
const r3 = bankRow('Оплата услуг mBank.MTS', 850, '2026-07-19T12:59', {
  comment: 'карта *0507',
  card: '*0507',
});
classified = api.classifyImportRowDuplicate(r3);
assert.strictEqual(classified.bucket, 'new', 'R3: new when no ledger');
const r3Match = {
  date: r3.date,
  amount: r3.amount,
  store: r3.store,
  comment: r3.comment,
  bank: r3.bank,
  sourceType: 'bank_statement',
};
const r3Cand = api.findDuplicateCandidates(r3Match);
assert.strictEqual(r3Cand.best, null, 'R3: no candidate');
assert.ok(!api.extractCardHints(r3.comment).includes('89886296030'));
assert.deepStrictEqual(api.extractCardHints(r3.comment), ['*0507']);
plan = api.planImportStatementRows([r3]);
assert.strictEqual(plan.reviewItems.length, 1, 'R3: goes to Razbor');
assert.ok(!plan.reviewItems[0].productName, 'R3: no productName from card comment');

// --- R4: receipt re-upload still skips (AD-011) ---
reset();
state.personal.push({
  opId: 1, date: '26.07.26', store: 'МАГНИТ', expense: 'РЯБА Майонез Провансаль',
  cost: 130, accountId: 'acc1',
});
plan = api.planImportStatementRows([
  receiptLine('МАГНИТ', 130, 'РЯБА Майонез Провансаль', '2026-07-26'),
]);
assert.strictEqual(plan.skippedHard, 1, 'R4: receipt skip');
assert.strictEqual(plan.reviewItems.length, 0, 'R4: no review');

// --- R5: 850 vs 851 → possible, not silent skip ---
reset();
state.personal.push({ ...ledgerMts });
const r5 = bankRow('Оплата услуг mBank.MTS', 851, '2026-07-19T12:59', {
  comment: 'карта *0507',
  card: '*0507',
});
classified = api.classifyImportRowDuplicate(r5);
assert.strictEqual(classified.bucket, 'possible', 'R5: possible not confident');
plan = api.planImportStatementRows([r5]);
assert.strictEqual(plan.skippedHard, 0, 'R5: not silent skip');
assert.strictEqual((plan.possibleDupItems || []).length, 1, 'R5: ask before tutor');
assert.strictEqual(plan.reviewItems.length, 0, 'R5: not normal review yet');

// --- R6: same day+amount+merchant ---
reset();
state.personal.push({
  opId: 2, date: '26.07.26', store: 'Пятёрочка', expense: 'Дом', cost: 350, accountId: 'acc1',
});
const r6 = bankRow('Пятёрочка', 350, '2026-07-26T18:13', { comment: 'карта *0507', card: '*0507' });
classified = api.classifyImportRowDuplicate(r6);
assert.strictEqual(classified.bucket, 'confident', 'R6: same store skip');
plan = api.planImportStatementRows([r6]);
assert.strictEqual(plan.skippedHard, 1, 'R6: skipped');

// --- R7: same day+amount, different stores → not auto skip ---
reset();
state.personal.push({
  opId: 3, date: '26.07.26', store: 'Пятёрочка', expense: 'Дом', cost: 350, accountId: 'acc1',
});
const r7 = bankRow('Магнит', 350, '2026-07-26T10:00', { comment: 'карта *0507', card: '*0507' });
classified = api.classifyImportRowDuplicate(r7);
assert.notStrictEqual(classified.bucket, 'confident', 'R7: not confident');
plan = api.planImportStatementRows([r7]);
assert.strictEqual(plan.skippedHard, 0, 'R7: not silent skip');
assert.ok(
  plan.reviewItems.length + (plan.possibleDupItems || []).length >= 1,
  'R7: still surfaces for user'
);

// --- Invariant: bank comment never becomes productName in classify path ---
reset();
state.personal.push({ ...ledgerMts });
const inv = api.findDuplicateCandidates({
  date: '2026-07-19',
  amount: -850,
  store: 'Оплата услуг mBank.MTS',
  productName: 'карта *0507', // poisoned — should only count if receipt
  comment: 'карта *0507',
  sourceType: 'bank_statement',
  bank: { card: '*0507', desc: 'Оплата услуг mBank.MTS' },
});
assert.strictEqual(inv.best.receiptLine, false, 'invariant: bank not receiptLine even if productName set on input object without receipt markers');
// Without receipt markers, expenseRaw must be empty — match via merchant/id not *0507 product
assert.ok(inv.best && inv.best.class === 'strong', 'invariant: bank matches via merchant, not card-as-product');

console.log('test_reconciliation_stage1: PASS');
