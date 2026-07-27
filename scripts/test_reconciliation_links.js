/**
 * Reconciliation links from «Уточнение»: Да, это одна запись → silent skip next time.
 *
 *   docker run --rm -v "$PWD":/w -w /w node:20-alpine node scripts/test_reconciliation_links.js
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
const storeServiceMatch = html.match(/const STORE_SERVICE_WORDS = new Set\(\[[\s\S]*?\]\);/);
assert.ok(translitMapMatch && storeServiceMatch);

const stubs = `
const state = {
  personal: [], business: [], importedOps: [], purchases: [],
  drawerOps: [], rejectedOps: [], reconciliationLinks: [], cardAccountLinks: [],
  merchants: {}, categories: [], confirmedIds: new Set(), deletedIds: new Set(),
};
const DATA = { sections: [], expensePresets: {}, storeDirectory: {} };
function ensureImportedOps() {}
function ensureLearnState() {}
function ensurePurchases() { if (!Array.isArray(state.purchases)) state.purchases = []; }
function ensureDrawerOps() {}
function ensureRejectedOps() { if (!Array.isArray(state.rejectedOps)) state.rejectedOps = []; }
function seedMerchantsFromHistory() {}
function openDrawerOps() { return []; }
function isGone() { return false; }
function preferAccountId() { return ''; }
function getMerchant() { return null; }
function topOutcome() { return null; }
function canonicalStoreName(s) { return s; }
function nextImportOpId() { nextImportOpId._n = (nextImportOpId._n || 900000) + 1; return nextImportOpId._n; }
function sourceOperationById() { return null; }
function bankIdFromSource() { return ''; }
function candidateBankId() { return ''; }
function attachSourceToExpense() {}
function saveState() {}
function accountLabel(id) { return id || '—'; }
function money(v) { return String(v); }
function formatDate(v) { return String(v || ''); }
`;

const names = [
  'normForSearch', 'translitFold', 'datePartIso', 'rowDateToIso', 'opFingerprint',
  'opHardFingerprint', 'opSoftKey', 'opSoftNormKey', 'rowIdentityStore', 'addOpFingerprintAliases',
  'rowMatchesKnownHard', 'productNameCanonical', 'productNameSimilar', 'isReceiptSide',
  'rowsMatchSameOp', 'findSimilarRecordedExpense', 'iterRecordedOps', 'rowMatchesRecordedOp',
  'isReceiptDuplicateInput', 'normalizeStoreForGateway', 'meaningfulStoreTokens', 'storeMatchSignal',
  'storesRelated', 'storeKey', 'normalizeStoreKey', 'storeNameVariants', 'amountSignVariants',
  'absCost', 'accountHintFromBank', 'duplicateExplanation', 'sourceTypeFromLabel',
  'extractPhoneDigits', 'extractCardHints', 'extractAccountHints', 'normalizePhoneId',
  'bankMatchIdentifiers', 'ledgerMatchIdentifiers', 'identifiersOverlap',
  'normalizeCardMask', 'normalizeReconciliationLink', 'normalizeCardAccountLink',
  'ensureReconciliationLinks', 'ensureCardAccountLinks',
  'buildBankReconFingerprint', 'bankReconLookupKeys', 'findExpenseByOpId',
  'findReconciliationLinkForRow', 'accountIdForCardHint', 'rememberCardAccountLink',
  'rememberReconciliationLink', 'findDuplicateCandidates', 'classifyImportRowDuplicate',
  'collectKnownHardFingerprints', 'collectKnownSoftKeys', 'collectKnownFiscalIds',
  'planImportStatementRows', 'personNameKey', 'stripTransferPrefix',
];

let body = stubs + storeServiceMatch[0] + '\n' + translitMapMatch[0] + '\n';
for (const n of names) body += extract(n) + '\n';

const api = new Function(`${body}; return {
  state,
  classifyImportRowDuplicate,
  planImportStatementRows,
  rememberReconciliationLink,
  findReconciliationLinkForRow,
  reset() {
    state.personal.length = 0;
    state.business.length = 0;
    state.importedOps.length = 0;
    state.purchases.length = 0;
    state.reconciliationLinks.length = 0;
    state.cardAccountLinks.length = 0;
    nextImportOpId._n = 900000;
  },
};`)();

const { state } = api;
function reset() { api.reset(); }

function receiptLine(store, amount, product, date = '2026-07-24') {
  return {
    date, store, amount: -Math.abs(amount), kind: 'expense',
    comment: product, productName: product, sourceType: 'receipt',
    bank: { receiptItem: true, desc: product, merchant: store },
  };
}

function bankRow(store, amount, date, extra = {}) {
  return {
    date, store, amount: -Math.abs(amount), kind: 'expense',
    comment: extra.comment || '',
    bank: { desc: extra.desc || store, card: extra.card || '', category: '', ...(extra.bank || {}) },
    sourceType: 'bank_statement',
  };
}

// --- R1: first time Servisnyy vs NSP → possible; after link → stored ---
reset();
state.personal.push({
  opId: 501, date: '21.07.26 14:59', store: 'NSP', expense: 'Бады',
  cost: 10511, category: 'Здоровье', accountId: 'acc-t',
});
const r1 = bankRow('Servisnyy tsentr', 10511, '2026-07-21T14:59', {
  comment: 'карта *0507', card: '*0507',
});
let c1 = api.classifyImportRowDuplicate(r1);
assert.strictEqual(c1.bucket, 'possible', 'R1: first time → Уточнение (possible)');
const link = api.rememberReconciliationLink(r1, state.personal[0], { rememberCard: true });
assert.ok(link, 'R1: link created');
assert.strictEqual(String(link.linkedExpenseOpId), '501');
assert.strictEqual(link.confirmedByUser, true);
assert.ok(state.reconciliationLinks.length >= 1, 'R1: persisted in state');
assert.ok(state.cardAccountLinks.some((x) => x.card === '*0507' && x.accountId === 'acc-t'), 'R1: card↔account remembered');

// --- R2: same op again → confident skip via link ---
c1 = api.classifyImportRowDuplicate(r1);
assert.strictEqual(c1.bucket, 'confident', 'R2: silent skip via reconciliation link');
assert.ok(c1.best && c1.best.reconLink, 'R2: reconLink on best');
let plan = api.planImportStatementRows([r1]);
assert.strictEqual(plan.skippedHard, 1, 'R2: skippedHard');
assert.strictEqual(plan.reviewItems.length, 0, 'R2: no Razbor');
assert.strictEqual((plan.possibleDupItems || []).length, 0, 'R2: no Уточнение');

// --- R3: other merchant same day/amount must NOT reuse link ---
const r3 = bankRow('Аптека', 10511, '2026-07-21T16:00', { comment: 'карта *9999', card: '*9999' });
const c3 = api.classifyImportRowDuplicate(r3);
assert.notStrictEqual(c3.bucket, 'confident', 'R3: not auto-linked to NSP');
assert.ok(!c3.best || !c3.best.reconLink, 'R3: no reconLink');

// --- R4: receipt re-upload still works ---
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

console.log('test_reconciliation_links: PASS');
