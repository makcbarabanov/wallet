/**
 * AD-010.1: one Valet tutor card = one expense (no group aggregation).
 *
 *   docker run --rm -v "$PWD":/w -w /w node:20-alpine node scripts/test_tutor_one_expense_one_card.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const HTML = path.join(__dirname, '..', 'review_categorization.html');
const html = fs.readFileSync(HTML, 'utf8');

assert.ok(!html.includes('function groupValetTutorItems'), 'groupValetTutorItems must be removed');
assert.ok(!html.includes('function tutorSliceItem'), 'tutorSliceItem must be removed (group-only)');
assert.ok(!html.includes('function markTutorOpsDone'), 'markTutorOpsDone must be removed (group-only)');
assert.ok(!html.includes('function handleTutorOpLine'), 'handleTutorOpLine must be removed (group-only)');
assert.ok(html.includes('function wrapAsTutorItem'), 'wrapAsTutorItem required');
assert.ok(html.includes('Расход ${n} из ${total}'), 'card title must be Расход N из M');
assert.ok(!html.includes('Группа ${n} из ${total}'), 'must not show Группа N из M');
// Button labels (not comments/docs): mass actions forbidden in UI code
assert.ok(!/`Верно · все \$\{/.test(html), 'must not mass-confirm button');
assert.ok(!/`Назначить · все \$\{/.test(html), 'must not mass-assign button');
assert.ok(!/`Удалить · все \$\{/.test(html), 'must not mass-delete button');
assert.ok(!/`В ящик · все \$\{/.test(html), 'must not mass-drawer button');
assert.ok(!/`Доход · все \$\{/.test(html), 'must not mass-income button');
assert.ok(!/`Не верно · все \$\{/.test(html), 'must not mass-reject button');
assert.ok(html.includes("okBtn.textContent = 'Верно'"), 'single Верно button');
assert.ok(html.includes("assignBtn.textContent = 'Назначить'"), 'single Назначить button');

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
  canonicalStoreName: (s) => s,
  isUnclearRow: () => false,
};
vm.createContext(sandbox);
vm.runInContext(extract('wrapAsTutorItem'), sandbox);
const { wrapAsTutorItem } = sandbox;

function queueFromRows(rows) {
  return rows.map((r) =>
    wrapAsTutorItem({
      ...r,
      store: r.store || r.productName || '—',
      identified: false,
      guess: null,
    })
  );
}

function assertOneOpCards(items, expected) {
  assert.strictEqual(items.length, expected);
  for (const it of items) {
    assert.strictEqual(it.ops.length, 1, 'ops.length must be 1');
    assert.strictEqual(it.count, 1);
  }
}

// Test 1 — statement: 3 Lenta lines → 3 cards
{
  const items = queueFromRows([
    { store: 'Лента', amount: 100, date: '01.07.26' },
    { store: 'Лента', amount: 200, date: '01.07.26' },
    { store: 'Лента', amount: 300, date: '01.07.26' },
  ]);
  assertOneOpCards(items, 3);
  assert.deepStrictEqual(
    items.map((it) => Number(it.ops[0].amount)),
    [100, 200, 300]
  );
  // 3 confirms → 3 createExpense calls (one op each)
  const createExpenseCalls = items.reduce((n, it) => n + it.ops.length, 0);
  assert.strictEqual(createExpenseCalls, 3);
}

// Test 2 — receipt: 4 product lines → 4 cards
{
  const items = queueFromRows([
    { productName: 'Молоко', store: 'Магнит', amount: 89, sourceType: 'receipt' },
    { productName: 'Хлеб', store: 'Магнит', amount: 45, sourceType: 'receipt' },
    { productName: 'Изолента', store: 'Стройторговля', amount: 120, sourceType: 'receipt' },
    { productName: 'Выключатель', store: 'Стройторговля', amount: 350, sourceType: 'receipt' },
  ]);
  assertOneOpCards(items, 4);
  assert.deepStrictEqual(
    items.map((it) => it.productName),
    ['Молоко', 'Хлеб', 'Изолента', 'Выключатель']
  );
}

// Test 3 — same shop, different products: cannot mass-assign (one card each)
{
  const items = queueFromRows([
    { productName: 'Вимос', store: 'Стройторговля', amount: 199, sourceType: 'receipt' },
    { productName: 'Тарелка опорная', store: 'Стройторговля', amount: 450, sourceType: 'receipt' },
    { productName: 'Изолента', store: 'Стройторговля', amount: 120, sourceType: 'receipt' },
    { productName: 'Выключатель', store: 'Стройторговля', amount: 350, sourceType: 'receipt' },
  ]);
  assertOneOpCards(items, 4);
  // No shared multi-op card: each decision covers exactly one expense
  assert.ok(items.every((it) => it.ops.length === 1));
}

// Test 4 — resume mid-session: 10 expenses, 3 confirmed → continue at 4th
{
  const items = queueFromRows(
    Array.from({ length: 10 }, (_, i) => ({
      store: 'Лента',
      amount: (i + 1) * 10,
      id: `op${i + 1}`,
    }))
  );
  assertOneOpCards(items, 10);
  // Simulate advanceTutorPastItem × 3 (splice from front while index stays)
  const queue = items.slice();
  let index = 0;
  for (let confirmed = 0; confirmed < 3; confirmed += 1) {
    queue.splice(index, 1);
    index = Math.min(index, queue.length - 1);
  }
  assert.strictEqual(queue.length, 7);
  assert.strictEqual(queue[0].ops[0].id, 'op4');
  assert.strictEqual(Number(queue[0].ops[0].amount), 40);
}

console.log('OK test_tutor_one_expense_one_card');
