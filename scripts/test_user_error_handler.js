/**
 * User-error handler: sanitize meta, classify kinds, log failure must not block modal.
 *
 *   docker run --rm -v "$PWD":/w -w /w node:20-alpine node scripts/test_user_error_handler.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const HTML = path.join(__dirname, '..', 'review_categorization.html');
const html = fs.readFileSync(HTML, 'utf8');

function extract(name) {
  let start = html.indexOf(`function ${name}(`);
  if (start < 0) start = html.indexOf(`async function ${name}(`);
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
  throw new Error(`unbalanced braces in ${name}()`);
}

assert.ok(html.includes('id="userErrorModal"'), 'userErrorModal markup missing');
assert.ok(html.includes('async function reportUserError('), 'reportUserError missing');
assert.ok(/void persistUserErrorLog\(payload\)/.test(html), 'log must not await before modal');
assert.ok(html.includes('quiet: true'), 'user errors must persist quietly (no Valet spam)');
assert.ok(/UI_BUILD\s*=\s*\d+/.test(html), 'UI_BUILD missing');
assert.ok(Number(html.match(/UI_BUILD\s*=\s*(\d+)/)[1]) >= 74, 'UI_BUILD should be >= 74');

const stubs = `
const WALLET_USER = 'makc';
const _valetConversationId = 'test-session';
const USER_ERROR_META_ALLOW = new Set([
  'filename', 'mimeType', 'sizeBytes', 'httpStatus', 'provider', 'engine', 'model',
  'recognizedCount', 'rawCount', 'validCount', 'failedCount', 'confidence', 'sourceLabel',
  'stage', 'errorType', 'ok',
]);
`;

const code = [
  stubs,
  extract('sanitizeUserErrorMeta'),
  extract('classifyImportErrorKind'),
  extract('defaultTipsForErrorKind'),
].join('\n');

const fns = {};
// eslint-disable-next-line no-new-func
new Function(`${code}; Object.assign(this, {
  sanitizeUserErrorMeta,
  classifyImportErrorKind,
  defaultTipsForErrorKind,
});`).call(fns);

const clean = fns.sanitizeUserErrorMeta({
  filename: 'receipt.jpg',
  mimeType: 'image/jpeg',
  sizeBytes: 12000,
  provider: 'yandex',
  rawCount: 10,
  validCount: 8,
  failedCount: 2,
  amount: 1234.5,
  amounts: [1, 2],
  apiKey: 'sk-secret',
  token: 'tok',
  receiptText: 'МОЛОКО 89',
  items: [{ name: 'x' }],
  photoBase64: 'AAAA',
  OPENROUTER_API_KEY: 'x',
});
assert.strictEqual(clean.filename, 'receipt.jpg');
assert.strictEqual(clean.rawCount, 10);
assert.strictEqual(clean.validCount, 8);
assert.strictEqual(clean.failedCount, 2);
assert.strictEqual(clean.amount, undefined);
assert.strictEqual(clean.apiKey, undefined);
assert.strictEqual(clean.token, undefined);
assert.strictEqual(clean.receiptText, undefined);
assert.strictEqual(clean.items, undefined);
assert.strictEqual(clean.photoBase64, undefined);

assert.strictEqual(
  fns.classifyImportErrorKind(new Error('HTTP 502 Bad Gateway')),
  'technical_failure'
);
assert.strictEqual(
  fns.classifyImportErrorKind(new Error('не удалось распознать'), { emptyItems: true }),
  'recognition_failed'
);
assert.strictEqual(
  fns.classifyImportErrorKind(null, { partial: true }),
  'partial_recognition'
);

const tips = fns.defaultTipsForErrorKind('partial_recognition');
assert.ok(tips.some((t) => /продолжить/i.test(t)), 'partial tips must offer continue');

// Simulate reportUserError ordering: persist may reject; modal still opens.
let modalOpened = false;
let persistCalled = false;
async function fakePersist() {
  persistCalled = true;
  throw new Error('POST /logs failed');
}
async function fakeModal() {
  modalOpened = true;
  return 'ok';
}
async function reportUserErrorSim() {
  try { await fakePersist(); } catch (_) { /* log must not break UX */ }
  return fakeModal();
}
reportUserErrorSim().then((decision) => {
  assert.strictEqual(decision, 'ok');
  assert.ok(persistCalled, 'persist attempted');
  assert.ok(modalOpened, 'modal still shown after log failure');
  console.log('test_user_error_handler: PASS');
}).catch((err) => {
  console.error(err);
  process.exit(1);
});
