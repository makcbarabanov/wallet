/**
 * Operation date doubt: never invent year/day when unsure.
 *
 *   docker run --rm -v "$PWD":/w -w /w node:20-alpine node scripts/test_operation_date_doubt.js
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
      if (seen && depth === 0) {
        i += 1;
        break;
      }
    }
  }
  return html.slice(start, i);
}

const src = [
  extract('parseOperationDateParts'),
  extract('todayIsoDate'),
  extract('assessOperationDate'),
].join('\n');
// eslint-disable-next-line no-new-func
const fns = new Function(`${src}; return { parseOperationDateParts, todayIsoDate, assessOperationDate };`)();
const { assessOperationDate } = fns;

const now = new Date('2026-07-28T12:00:00Z');

{
  const a = assessOperationDate('2026-07-28', { now, source: 'receipt' });
  assert.strictEqual(a.status, 'ok');
  assert.strictEqual(a.iso, '2026-07-28');
}
{
  const a = assessOperationDate('22.05.2024', { now, source: 'receipt' });
  assert.strictEqual(a.status, 'anomalous_year');
  assert.strictEqual(a.iso, '2024-05-22');
  assert.ok(/2024/.test(a.reason));
}
{
  const a = assessOperationDate('', { now, source: 'receipt' });
  assert.strictEqual(a.status, 'missing');
  assert.strictEqual(a.suggestedIso, '2026-07-28');
}
{
  const a = assessOperationDate('not-a-date', { now, source: 'image' });
  assert.strictEqual(a.status, 'unreadable');
}
{
  const a = assessOperationDate('2026-01-01', { now, source: 'receipt', maxDays: 60 });
  assert.strictEqual(a.status, 'anomalous_far');
}
{
  // CSV-like historical year still flagged by assess; gate skips CSV at row-resolver level.
  const a = assessOperationDate('2024-05-22', { now, source: 'csv' });
  assert.strictEqual(a.status, 'anomalous_year');
}

console.log('operation_date_doubt OK');
