/** One-shot PDF reconciliation report against dump. */
const fs = require('fs');
const html = fs.readFileSync('review_categorization.html', 'utf8');
const dump = JSON.parse(fs.readFileSync('data/dumps/makc/260726/wallet_review_v3.json', 'utf8'));
const rows = JSON.parse(fs.readFileSync('data/fixtures/tbank/pdf_rows_certificate_1.json', 'utf8'));

function extract(name) {
  let start = html.indexOf(`function ${name}(`);
  if (start < 0) start = html.indexOf(`function* ${name}(`);
  if (start < 0) throw new Error(name);
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
  throw new Error(name);
}

const translit = html.match(/const _TRANSLIT_MAP = \{[\s\S]*?\n\};/)[0];
const svc = html.match(/const STORE_SERVICE_WORDS = new Set\(\[[\s\S]*?\]\);/)[0];
const names = [
  'normForSearch', 'translitFold', 'datePartIso', 'rowDateToIso', 'opFingerprint',
  'opHardFingerprint', 'opSoftKey', 'opSoftNormKey', 'rowIdentityStore', 'addOpFingerprintAliases',
  'rowMatchesKnownHard', 'productNameCanonical', 'productNameSimilar', 'isReceiptSide',
  'rowsMatchSameOp', 'findSimilarRecordedExpense', 'iterRecordedOps', 'rowMatchesRecordedOp',
  'isReceiptDuplicateInput', 'normalizeStoreForGateway', 'meaningfulStoreTokens', 'storeMatchSignal',
  'storesRelated', 'storeKey', 'normalizeStoreKey', 'storeNameVariants', 'amountSignVariants',
  'absCost', 'accountHintFromBank', 'bankIdFromSource', 'candidateBankId', 'duplicateExplanation',
  'sourceTypeFromLabel', 'extractPhoneDigits', 'extractCardHints', 'extractAccountHints',
  'normalizePhoneId', 'bankMatchIdentifiers', 'ledgerMatchIdentifiers', 'identifiersOverlap',
  'findDuplicateCandidates', 'classifyImportRowDuplicate', 'collectKnownHardFingerprints',
  'collectKnownSoftKeys', 'collectKnownFiscalIds', 'planImportStatementRows', 'personNameKey',
  'stripTransferPrefix',
];

let body = `
const state={personal:[],business:[],importedOps:[],purchases:[],drawerOps:[],rejectedOps:[],merchants:{},categories:[],confirmedIds:new Set(),deletedIds:new Set()};
const DATA={sections:[],expensePresets:{},storeDirectory:{}};
function ensureImportedOps(){}
function ensureLearnState(){}
function ensurePurchases(){if(!Array.isArray(state.purchases))state.purchases=[];}
function ensureDrawerOps(){}
function ensureRejectedOps(){}
function seedMerchantsFromHistory(){}
function openDrawerOps(){return [];}
function isGone(){return false;}
function preferAccountId(){return '';}
function getMerchant(){return null;}
function topOutcome(){return null;}
function canonicalStoreName(s){return s;}
function nextImportOpId(){nextImportOpId._n=(nextImportOpId._n||900000)+1;return nextImportOpId._n;}
function sourceOperationById(){return null;}
function bankIdFromSource(){return '';}
function candidateBankId(){return '';}
` + svc + '\n' + translit + '\n';
for (const n of names) body += extract(n) + '\n';

const api = new Function(`${body}; return {planImportStatementRows,classifyImportRowDuplicate,state};`)();
api.state.personal = dump.personal || [];
api.state.business = dump.business || [];
api.state.purchases = dump.purchases || [];
const plan = api.planImportStatementRows(rows);
const bucket = (row) => api.classifyImportRowDuplicate(row).bucket;
const mts = rows.find((r) => String(r.store).includes('MTS'));
const pyat = rows.filter((r) => /ятёроч|ятероч/i.test(r.store));
const oooo = rows.filter((r) => /^OOO$/i.test(String(r.store).trim()));
const report = {
  after: {
    total: plan.total,
    skip: plan.skippedHard,
    possible: plan.softDup,
    newReview: (plan.reviewItems || []).length,
    auto: (plan.autoItems || []).length,
  },
  cases: {
    mts850: mts ? { store: mts.store, amount: mts.amount, bucket: bucket(mts) } : null,
    pyaterochka: pyat.map((r) => ({ store: r.store, amount: r.amount, bucket: bucket(r) })),
    oooTruncated: oooo.map((r) => ({ store: r.store, amount: r.amount, date: r.date, bucket: bucket(r) })),
  },
  baselineFromUiScreenshot: { skip: 72, new: 50, note: 'UI before Stage1 (screenshot)' },
};
console.log(JSON.stringify(report, null, 2));
fs.writeFileSync('data/fixtures/tbank/after_pdf_reconciliation.json', JSON.stringify(report, null, 2));
