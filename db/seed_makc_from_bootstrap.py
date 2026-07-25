#!/usr/bin/env python3
"""Seed user makc + bootstrap categories/operations into Postgres wallet DB via psql."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "review_categorization.html"
ENV = ROOT / ".env"


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v
    return out


def sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_json(obj) -> str:
    return sql_str(json.dumps(obj, ensure_ascii=False))


def fingerprint(user_key: str, client_op_id: str, date: str, store: str, amount: float, comment: str) -> str:
    raw = f"{user_key}|{client_op_id}|{date}|{store}|{amount}|{comment}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


def parse_occurred_at(value: str) -> str:
    value = (value or "").strip()
    for fmt, out_len in (
        ("%Y-%m-%dT%H:%M:%S", 19),
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d", 10),
    ):
        try:
            chunk = value[:out_len]
            datetime.strptime(chunk, fmt)
            if out_len == 10:
                return f"{chunk} 00:00:00+00"
            return chunk.replace("T", " ") + "+00"
        except ValueError:
            continue
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00")


def main() -> int:
    env = load_env(ENV)
    html = HTML.read_text(encoding="utf-8")
    m = re.search(
        r'<script id="bootstrap" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    if not m:
        print("bootstrap JSON not found", file=sys.stderr)
        return 1
    boot = json.loads(m.group(1))

    lines: list[str] = ["BEGIN;"]
    lines.append(
        """
INSERT INTO users (username, display_name)
VALUES ('makc', 'Максим')
ON CONFLICT (username) DO UPDATE
  SET display_name = EXCLUDED.display_name,
      updated_at = now();
"""
    )
    lines.append(
        """
DELETE FROM operations
WHERE user_id = (SELECT id FROM users WHERE username = 'makc')
  AND source = 'bootstrap';
DELETE FROM categories
WHERE user_id = (SELECT id FROM users WHERE username = 'makc');
DELETE FROM debts
WHERE user_id = (SELECT id FROM users WHERE username = 'makc')
  AND client_id = 'd1';
"""
    )

    for c in boot.get("categories") or []:
        lines.append(
            f"""
INSERT INTO categories (user_id, name, wallet_kind, color)
SELECT id, {sql_str(c['name'])}, {sql_str(c.get('wallet') or 'personal')}, {sql_str(c.get('color') or '#3B82F6')}
FROM users WHERE username = 'makc'
ON CONFLICT (user_id, name) DO UPDATE
  SET wallet_kind = EXCLUDED.wallet_kind,
      color = EXCLUDED.color;
"""
        )

    op_values = []
    for sec in boot.get("sections") or []:
        wallet = sec.get("wallet") or "personal"
        if wallet not in ("personal", "business"):
            wallet = "personal"
        category = sec.get("category") or ""
        expense = sec.get("expense") or ""
        for group in sec.get("groups") or []:
            group_id = group.get("groupId")
            gid_sql = "NULL" if group_id is None else str(int(group_id))
            for op in group.get("ops") or []:
                client_op_id = str(op["id"])
                date = str(op.get("date") or "")
                store = str(op.get("store") or "")
                amount = float(op.get("amount") or 0)
                comment = str(op.get("comment") or "")
                occurred = parse_occurred_at(date)
                fp = fingerprint("makc", client_op_id, date, store, amount, comment)
                meta = {
                    "section_id": sec.get("id"),
                    "section_title": sec.get("title"),
                    "group_name": group.get("name"),
                }
                op_values.append(
                    "("
                    + ", ".join(
                        [
                            "(SELECT id FROM users WHERE username = 'makc')",
                            sql_str(client_op_id),
                            sql_str(wallet),
                            "'confirmed'",
                            sql_str(occurred) + "::timestamptz",
                            sql_str(category),
                            sql_str(expense),
                            sql_str(store),
                            str(amount),
                            sql_str(comment),
                            sql_str(fp),
                            "'bootstrap'",
                            gid_sql,
                            sql_json(meta) + "::jsonb",
                        ]
                    )
                    + ")"
                )

    # batch inserts
    for i in range(0, len(op_values), 200):
        batch = ",\n".join(op_values[i : i + 200])
        lines.append(
            f"""
INSERT INTO operations (
    user_id, client_op_id, bucket, status, occurred_at,
    category_name, expense, store, amount, comment,
    fingerprint, source, group_id, meta
) VALUES
{batch}
ON CONFLICT (user_id, client_op_id) DO UPDATE SET
    bucket = EXCLUDED.bucket,
    status = EXCLUDED.status,
    occurred_at = EXCLUDED.occurred_at,
    category_name = EXCLUDED.category_name,
    expense = EXCLUDED.expense,
    store = EXCLUDED.store,
    amount = EXCLUDED.amount,
    comment = EXCLUDED.comment,
    fingerprint = EXCLUDED.fingerprint,
    source = EXCLUDED.source,
    group_id = EXCLUDED.group_id,
    meta = EXCLUDED.meta,
    updated_at = now();
"""
        )

    store_names = sorted({str(s) for s in (boot.get("stores") or []) if s})
    for i in range(0, len(store_names), 200):
        batch = store_names[i : i + 200]
        values = ",\n".join(
            f"((SELECT id FROM users WHERE username = 'makc'), {sql_str(s)})" for s in batch
        )
        lines.append(
            f"""
INSERT INTO merchants (user_id, store)
VALUES
{values}
ON CONFLICT (user_id, store) DO NOTHING;
"""
        )

    lines.append(
        """
INSERT INTO debts (user_id, client_id, direction, person, amount, debt_date, due_date)
SELECT id, 'd1', 'lent', 'Ринат', 5000, '12.07.26', '15.07.26'
FROM users WHERE username = 'makc'
ON CONFLICT (user_id, client_id) DO UPDATE SET
    person = EXCLUDED.person,
    amount = EXCLUDED.amount,
    debt_date = EXCLUDED.debt_date,
    due_date = EXCLUDED.due_date;
"""
    )

    lines.append(
        f"""
INSERT INTO user_settings (user_id, expense_presets)
SELECT id, {sql_json(boot.get('expensePresets') or {})}::jsonb
FROM users WHERE username = 'makc'
ON CONFLICT (user_id) DO UPDATE SET
    expense_presets = EXCLUDED.expense_presets,
    updated_at = now();
"""
    )

    lines.append(
        """
SELECT
  u.id AS user_id,
  u.username,
  (SELECT count(*) FROM categories c WHERE c.user_id = u.id) AS categories,
  (SELECT count(*) FROM operations o WHERE o.user_id = u.id) AS operations,
  (SELECT count(*) FROM operations o WHERE o.user_id = u.id AND o.bucket = 'personal') AS personal,
  (SELECT count(*) FROM operations o WHERE o.user_id = u.id AND o.bucket = 'business') AS business,
  (SELECT count(*) FROM merchants m WHERE m.user_id = u.id) AS merchants,
  (SELECT count(*) FROM debts d WHERE d.user_id = u.id) AS debts
FROM users u
WHERE u.username = 'makc';
COMMIT;
"""
    )

    sql = "\n".join(lines)
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as f:
        f.write(sql)
        sql_path = f.name

    os.environ["PGPASSWORD"] = env["DB_PASS"]
    os.environ["PGSSLMODE"] = env.get("DB_SSLMODE", "require")
    r = subprocess.run(
        [
            "psql",
            "-h",
            env["DB_HOST"],
            "-p",
            env.get("DB_PORT", "5432"),
            "-U",
            env["DB_USER"],
            "-d",
            env.get("DB_NAME", "wallet"),
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            sql_path,
        ],
        text=True,
        capture_output=True,
    )
    Path(sql_path).unlink(missing_ok=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        return r.returncode
    print(f"seeded from bootstrap ops={len(op_values)} stores={len(store_names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
