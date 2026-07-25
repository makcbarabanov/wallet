/**
 * SmartSelect behaviour test: search, popularity, aliases, big lists.
 *
 * The UI lives in one HTML file, so the test pulls the pure SmartSelect
 * functions out of it and runs them against a fake state — no browser needed.
 *
 * Run (node is not installed on the host, so use the container):
 *   docker run --rm -v "$PWD":/w -w /w node:20-alpine node scripts/test_smart_select.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const HTML = path.join(__dirname, '..', 'review_categorization.html');
const html = fs.readFileSync(HTML, 'utf8');

/** Cut `function name(...) { ... }` out of the HTML by brace balance. */
function extract(name) {
  const start = html.indexOf(`function ${name}(`);
  assert.ok(start > 0, `function ${name}() not found in ${path.basename(HTML)}`);
  // Skip the parameter list first: it may hold destructuring braces.
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

const NAMES = [
  'normForSearch', 'translitFold', 'smartStrings', 'smartField', 'smartItems',
  'smartMatchRank', 'smartAllowCreate', 'directoryUsage', 'categorySmartItems',
  'expenseSmartItems', 'bizSmartItems',
];
const TRANSLIT = html.slice(html.indexOf('const _TRANSLIT_MAP'), html.indexOf('/** Fold Cyrillic'));

const state = {
  categories: [
    { name: 'Продукты', wallet: 'personal' },
    { name: 'Здоровье', wallet: 'personal' },
    { name: 'Зоотовары', wallet: 'personal' },
    { name: 'Транспорт', wallet: 'personal' },
    { name: 'Стройматериалы', wallet: 'business' },
  ],
  personal: [
    { category: 'Продукты', expense: 'Дом', store: 'Лента' },
    { category: 'Продукты', expense: 'Дом', store: 'Магнит' },
    { category: 'Продукты', expense: 'Вендинг', store: 'Магнит' },
    { category: 'Здоровье', expense: 'Аптека', store: 'Ozon' },
  ],
  business: [
    { category: 'Стройматериалы', expense: 'Плитка', object: 'Гостевой 5×8', customer: 'Феруз' },
    { category: 'Стройматериалы', expense: 'Плитка', object: 'Баня 4×6', customer: 'Феруз' },
    { category: 'Стройматериалы', expense: 'Цемент', object: 'Баня 4×6', customer: 'Азиз' },
  ],
};
const DATA = { expensePresets: { Продукты: ['Дом', 'Вендинг', 'Прочее'] } };
function catsForWallet(wallet) {
  const list = state.categories.filter((c) => c.wallet === wallet);
  return list.length ? list : state.categories;
}

const sandbox = { state, DATA, catsForWallet };
// eslint-disable-next-line no-new-func
const load = new Function(
  'state', 'DATA', 'catsForWallet',
  `${TRANSLIT}\n${NAMES.map(extract).join('\n')}\nreturn { ${NAMES.join(', ')} };`
);
const S = load(sandbox.state, sandbox.DATA, sandbox.catsForWallet);

const values = (items) => items.map((i) => i.value);
const ranked = (cfg, query) => {
  const items = S.smartItems(cfg);
  return items
    .map((it) => ({ it, rank: S.smartMatchRank(it, query) }))
    .filter((r) => r.rank >= 0)
    .sort((a, b) => a.rank - b.rank || b.it.count - a.it.count
      || a.it.value.localeCompare(b.it.value, 'ru'))
    .map((r) => r.it.value);
};

const categoryCfg = {
  source: () => S.categorySmartItems('personal'),
  displayField: 'name',
  popularity: 'count',
  allowCreate: true,
};

// 1. Categories: popularity comes from the Ledger, most used first.
const cats = S.smartItems(categoryCfg);
assert.deepStrictEqual(values(cats).slice(0, 2), ['Продукты', 'Здоровье'],
  'categories must be sorted by real usage');
assert.strictEqual(cats.find((c) => c.value === 'Продукты').count, 3);
assert.strictEqual(cats.find((c) => c.value === 'Транспорт').count, 0);

// 2. Category search narrows as you type: «з» → both, «зд» → only Здоровье
//    (Зоотовары has no «д» at all, so it cannot match «зд»).
assert.deepStrictEqual(ranked(categoryCfg, 'з'), ['Здоровье', 'Зоотовары']);
assert.deepStrictEqual(ranked(categoryCfg, 'зд'), ['Здоровье']);
assert.deepStrictEqual(ranked(categoryCfg, 'ЗДОР'), ['Здоровье'], 'search is case-insensitive');

// 3. Aliases and cross-alphabet search keep working (store field behaviour).
const storeCfg = {
  source: [
    { name: 'Ozon', count: 12, aliases: ['OZON.ru', 'Озон'] },
    { name: 'Яндекс Еда', count: 3, aliases: [] },
    { name: 'Лента', count: 1, aliases: [] },
  ],
  displayField: 'name',
  searchFields: ['name'],
  aliases: 'aliases',
  popularity: 'count',
  allowCreate: true,
};
assert.deepStrictEqual(ranked(storeCfg, 'оз'), ['Ozon'], 'Cyrillic query must find Latin name');
assert.deepStrictEqual(ranked(storeCfg, 'янд'), ['Яндекс Еда']);
assert.deepStrictEqual(ranked(storeCfg, 'ozon'), ['Ozon']);
assert.deepStrictEqual(ranked(storeCfg, 'еда'), ['Яндекс Еда'], 'word-start match inside a name');

// 4. Expense purposes: real usage first, presets still available.
const expenses = S.smartItems({
  source: () => S.expenseSmartItems('Продукты'),
  displayField: 'name',
  popularity: 'count',
});
assert.deepStrictEqual(values(expenses), ['Дом', 'Вендинг', 'Прочее']);
assert.strictEqual(expenses[0].count, 2);
assert.strictEqual(expenses.find((e) => e.value === 'Прочее').count, 0,
  'a preset never used yet has no popularity');

// 5. Business fields read the business ledger, default stays selectable.
const objects = S.smartItems({
  source: () => S.bizSmartItems('object', 'Гостевой 5×8'),
  displayField: 'name',
  popularity: 'count',
});
assert.deepStrictEqual(values(objects), ['Баня 4×6', 'Гостевой 5×8']);
const customers = S.smartItems({
  source: () => S.bizSmartItems('customer', 'Феруз'),
  displayField: 'name',
  popularity: 'count',
});
assert.deepStrictEqual(values(customers), ['Феруз', 'Азиз']);

// 6. Creating is opt-in per field.
assert.strictEqual(S.smartAllowCreate({ allowCreate: true }), true);
assert.strictEqual(S.smartAllowCreate({}), false);
assert.strictEqual(S.smartAllowCreate({ allowCreate: () => false }), false);

// 7. Plain strings work as a source, duplicates collapse.
const plain = S.smartItems({ source: ['Феруз', 'феруз ', 'Азиз'] });
assert.deepStrictEqual(values(plain), ['Азиз', 'Феруз']);

// 8. Big directories stay fast: 20k rows, build + full search well under a second.
const big = Array.from({ length: 20000 }, (_, i) => ({ name: `Контрагент ${i}`, count: i % 7 }));
const t0 = Date.now();
const bigItems = S.smartItems({ source: big, displayField: 'name', popularity: 'count' });
const matches = bigItems.filter((it) => S.smartMatchRank(it, 'контрагент 199') >= 0);
const elapsed = Date.now() - t0;
assert.strictEqual(bigItems.length, 20000);
assert.ok(matches.length > 0, 'search must find rows in a big directory');
assert.ok(elapsed < 1500, `big directory too slow: ${elapsed}ms`);

console.log('SmartSelect OK');
console.log('- categories ranked by ledger usage, «з» → Здоровье, Зоотовары; «зд» → Здоровье');
console.log('- aliases + cross-alphabet search («оз» → Ozon) unchanged');
console.log('- expense presets and business object/customer wired to real usage');
console.log(`- 20k rows: build + search ${elapsed}ms`);
