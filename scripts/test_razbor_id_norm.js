#!/usr/bin/env node
/**
 * Guard: Разбор id normalization + settled-queue filter (string vs number).
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'review_categorization.html'),
  'utf8'
);

assert.ok(Number(html.match(/UI_BUILD\s*=\s*(\d+)/)[1]) >= 93, 'UI_BUILD >= 93');
assert.ok(/function normId\(/.test(html), 'normId helper');
assert.ok(/function idSetHas\(/.test(html), 'idSetHas helper');
assert.ok(/function isImportedOpActionNeeded\(/.test(html), 'isImportedOpActionNeeded');
assert.ok(/function pruneSettledRazborQueue\(/.test(html), 'pruneSettledRazborQueue');
assert.ok(/function markImportedOpSettled\(/.test(html), 'markImportedOpSettled');
assert.ok(/function ledgerLinksSourceOpId\(/.test(html), 'ledgerLinksSourceOpId');
assert.ok(/pruneSettledRazborQueue\(\)/.test(html), 'enter/resume calls prune');
assert.ok(
  /confirmedIds:\s*\[\.\.\.st\.confirmedIds\]\.map\(normId\)/.test(html),
  'serialize confirmedIds via normId'
);
assert.ok(
  /\.map\(normId\)\.filter\(Boolean\)/.test(html),
  'load confirmedIds/deletedIds via normId'
);
assert.ok(!/\.confirmedIds\.has\(/.test(html), 'no raw confirmedIds.has');
assert.ok(!/\.confirmedIds\.add\(/.test(html), 'no raw confirmedIds.add');

// Runtime: string vs number must match; linked/ledger ops are not "live".
function normId(id) {
  if (id == null) return '';
  return String(id).trim();
}
function idsEqual(a, b) {
  const na = normId(a);
  const nb = normId(b);
  return !!na && na === nb;
}
function idSetHas(set, id) {
  if (!set) return false;
  const n = normId(id);
  if (!n) return false;
  if (set.has(n)) return true;
  if (set.has(id)) return true;
  for (const x of set) {
    if (normId(x) === n) return true;
  }
  return false;
}
function idSetAdd(set, id) {
  const n = normId(id);
  if (!n || !set) return;
  set.add(n);
}

const confirmed = new Set(['900003']);
assert.strictEqual(idSetHas(confirmed, 900003), true, 'string set vs number id');
assert.strictEqual(idSetHas(confirmed, '900003'), true);
assert.strictEqual(idsEqual(900003, '900003'), true);

const personal = [{ opId: 900003, sourceOperationId: '900003', cost: 74 }];
function ledgerLinksSourceOpId(id) {
  const n = normId(id);
  for (const row of personal) {
    if (idsEqual(row.opId, n) || idsEqual(row.sourceOperationId, n)) return true;
  }
  return false;
}
function isImportedOpActionNeeded(op, confirmedIds, deletedIds) {
  if (!op || op.id == null) return false;
  if (idSetHas(confirmedIds, op.id) || idSetHas(deletedIds, op.id)) return false;
  if (ledgerLinksSourceOpId(op.id)) return false;
  const st = String(op.status || '').toLowerCase();
  if (st === 'linked' || st === 'merged') return false;
  return true;
}

const linked = { id: 900003, status: 'linked' };
const queuedGhost = { id: 900003, status: 'queued' }; // already in ledger
const fresh = { id: 900099, status: 'queued' };

assert.strictEqual(
  isImportedOpActionNeeded(linked, new Set(), new Set()),
  false,
  'linked status excluded'
);
assert.strictEqual(
  isImportedOpActionNeeded(queuedGhost, new Set(), new Set()),
  false,
  'ledger link excludes even if queued'
);
assert.strictEqual(
  isImportedOpActionNeeded(queuedGhost, new Set(), new Set()) ||
    idSetHas(new Set(['900003']), 900003),
  true
);
assert.strictEqual(
  isImportedOpActionNeeded(fresh, new Set(), new Set()),
  true,
  'fresh queued still needs action'
);
assert.strictEqual(
  isImportedOpActionNeeded(fresh, new Set(['900099']), new Set()),
  false,
  'confirmed excludes'
);

const settled = new Set();
idSetAdd(settled, 900003);
assert.ok(settled.has('900003') && !settled.has(900003), 'idSetAdd stores string');

console.log('test_razbor_id_norm: ok');
