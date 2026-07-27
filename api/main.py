"""Wallet state API — Postgres is the source of truth for the HTML UI."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DEFAULT_USER = os.getenv("WALLET_DEFAULT_USER", "makc")
# Point-in-time JSON dumps (live SoT stays in Postgres user_app_state).
DUMP_ROOT = Path(os.getenv("WALLET_DUMP_DIR", "/data/dumps")).resolve()


def db_connect():
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        dbname=os.environ.get("DB_NAME", "wallet"),
        sslmode=os.environ.get("DB_SSLMODE", "require"),
    )


app = FastAPI(title="Wallet State API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StatePut(BaseModel):
    payload: dict[str, Any]
    source: str = "ui"
    # Optimistic locking: if set and force is false, must match DB revision.
    base_revision: int | None = None
    force: bool = False
    # When false, only update user_app_state JSON (fast). Normalized tables
    # catch up on the next full save (import / sync / tutor finish).
    normalize: bool = True


class DumpCreate(BaseModel):
    reason: str = "manual"
    payload: dict[str, Any] | None = None
    note: str = ""


class StateResponse(BaseModel):
    username: str
    user_id: int
    revision: int
    source: str
    updated_at: datetime | None
    payload: dict[str, Any]
    stats: dict[str, int] = Field(default_factory=dict)


def user_id_by_name(cur, username: str) -> int:
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"user {username!r} not found")
    return int(row[0])


def payload_stats(payload: dict[str, Any]) -> dict[str, int]:
    return {
        "personal": len(payload.get("personal") or []),
        "business": len(payload.get("business") or []),
        "imported": len(payload.get("importedOps") or []),
        "rejected": len(payload.get("rejectedOps") or []),
        "categories": len(payload.get("categories") or []),
        "accounts": len(payload.get("accounts") or []),
        "merchants": len(payload.get("merchants") or {}),
    }


def parse_occurred_at(value: Any) -> datetime:
    s = str(value or "").strip()
    candidates = [s]
    if "T" in s:
        candidates.append(s.replace("T", " ")[:19])
    if len(s) >= 10 and s[4] == "-":
        candidates.append(s[:10])
    for cand in candidates:
        for fmt in (
            "%d.%m.%y %H:%M",
            "%d.%m.%y",
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(cand, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return datetime.now(timezone.utc)


def fingerprint(user_id: int, client_op_id: str, date_s: str, store: str, amount: float, comment: str) -> str:
    raw = f"{user_id}|{client_op_id}|{date_s}|{store}|{amount}|{comment}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


def sync_normalized(cur, user_id: int, payload: dict[str, Any]) -> None:
    """Replace queryable tables from full UI payload."""
    cur.execute("DELETE FROM operations WHERE user_id = %s", (user_id,))
    cur.execute("DELETE FROM categories WHERE user_id = %s", (user_id,))
    cur.execute("DELETE FROM accounts WHERE user_id = %s", (user_id,))
    cur.execute("DELETE FROM debts WHERE user_id = %s", (user_id,))
    cur.execute("DELETE FROM merchants WHERE user_id = %s", (user_id,))
    cur.execute("DELETE FROM account_log WHERE user_id = %s", (user_id,))
    cur.execute("DELETE FROM plans WHERE user_id = %s", (user_id,))

    for c in payload.get("categories") or []:
        cur.execute(
            """
            INSERT INTO categories (user_id, name, wallet_kind, color)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, name) DO NOTHING
            """,
            (
                user_id,
                c.get("name") or "",
                c.get("wallet") if c.get("wallet") in ("personal", "business") else "personal",
                c.get("color") or "#3B82F6",
            ),
        )

    account_id_map: dict[str, int] = {}
    for a in payload.get("accounts") or []:
        client_id = str(a.get("id") or "")
        if not client_id:
            continue
        cur.execute(
            """
            INSERT INTO accounts (user_id, client_id, name, amount)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, client_id) DO UPDATE
              SET name = EXCLUDED.name, amount = EXCLUDED.amount, updated_at = now()
            RETURNING id
            """,
            (user_id, client_id, a.get("name") or "Без названия", float(a.get("amount") or 0)),
        )
        account_id_map[client_id] = int(cur.fetchone()[0])

    def insert_ops(rows: list, bucket: str, status: str) -> None:
        batch: list[tuple] = []
        for r in rows or []:
            client_op_id = str(r.get("opId") if r.get("opId") is not None else r.get("id") or "")
            if not client_op_id:
                continue
            # UI stores positive cost for personal/business; imported/deleted keep bank sign
            cost = float(r.get("cost") if r.get("cost") is not None else r.get("amount") or 0)
            if bucket in ("imported", "deleted"):
                amount = float(r.get("amount") if r.get("amount") is not None else -abs(cost))
                if bucket == "imported" and amount > 0 and str(r.get("kind") or "") == "expense":
                    amount = -amount
            else:
                amount = -abs(cost)
            date_s = str(r.get("date") or "")
            store = str(r.get("store") or "")
            comment = str(r.get("note") or r.get("comment") or "")
            occurred = parse_occurred_at(date_s)
            acc_client = str(r.get("accountId") or "") or None
            acc_pk = account_id_map.get(acc_client) if acc_client else None
            bank = r.get("bank")
            batch.append(
                (
                    user_id,
                    client_op_id,
                    bucket,
                    status,
                    occurred,
                    r.get("category") or "",
                    r.get("expense") or "",
                    store,
                    amount,
                    comment,
                    acc_pk,
                    r.get("object") or None,
                    r.get("customer") or None,
                    psycopg.types.json.Json(bank) if bank is not None else None,
                    fingerprint(user_id, client_op_id, date_s, store, amount, comment),
                    psycopg.types.json.Json({k: r.get(k) for k in ("unit", "qty", "price") if k in r}),
                )
            )
        if not batch:
            return
        cur.executemany(
            """
            INSERT INTO operations (
                user_id, client_op_id, bucket, status, occurred_at,
                category_name, expense, store, amount, comment,
                account_id, object_name, customer, bank, fingerprint, source, meta
            ) VALUES (
                %s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,'ui',%s::jsonb
            )
            ON CONFLICT (user_id, client_op_id) DO UPDATE SET
                bucket = EXCLUDED.bucket,
                status = EXCLUDED.status,
                occurred_at = EXCLUDED.occurred_at,
                category_name = EXCLUDED.category_name,
                expense = EXCLUDED.expense,
                store = EXCLUDED.store,
                amount = EXCLUDED.amount,
                comment = EXCLUDED.comment,
                account_id = EXCLUDED.account_id,
                object_name = EXCLUDED.object_name,
                customer = EXCLUDED.customer,
                bank = EXCLUDED.bank,
                fingerprint = EXCLUDED.fingerprint,
                source = EXCLUDED.source,
                meta = EXCLUDED.meta,
                updated_at = now()
            """,
            batch,
        )

    insert_ops(payload.get("personal") or [], "personal", "confirmed")
    insert_ops(payload.get("business") or [], "business", "confirmed")
    insert_ops(payload.get("importedOps") or [], "imported", "pending")
    insert_ops(payload.get("rejectedOps") or [], "deleted", "deleted")

    debts = payload.get("debts") or {}
    for direction in ("lent", "borrowed"):
        for d in debts.get(direction) or []:
            client_id = str(d.get("id") or "")
            if not client_id:
                continue
            cur.execute(
                """
                INSERT INTO debts (
                    user_id, client_id, direction, person, amount, debt_date, due_date,
                    note, is_closed, meta
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id, client_id) DO UPDATE SET
                    direction = EXCLUDED.direction,
                    person = EXCLUDED.person,
                    amount = EXCLUDED.amount,
                    debt_date = EXCLUDED.debt_date,
                    due_date = EXCLUDED.due_date,
                    note = EXCLUDED.note,
                    is_closed = EXCLUDED.is_closed,
                    meta = EXCLUDED.meta
                """,
                (
                    user_id,
                    client_id,
                    direction,
                    d.get("person") or "",
                    float(d.get("amount") or 0),
                    str(d.get("date") or ""),
                    str(d.get("due") or "—"),
                    str(d.get("note") or ""),
                    bool(d.get("isClosed")),
                    psycopg.types.json.Json(
                        {
                            k: d.get(k)
                            for k in (
                                "closedAt",
                                "closedReason",
                                "closedAccountId",
                                "closedAccountName",
                                "closedAmount",
                            )
                            if d.get(k) is not None
                        }
                    ),
                ),
            )

    for key, m in (payload.get("merchants") or {}).items():
        store = (m or {}).get("name") or key
        cur.execute(
            """
            INSERT INTO merchants (user_id, store, auto, user_auto, trust, learn, outcomes)
            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (user_id, store) DO UPDATE SET
                auto = EXCLUDED.auto,
                user_auto = EXCLUDED.user_auto,
                trust = EXCLUDED.trust,
                learn = EXCLUDED.learn,
                outcomes = EXCLUDED.outcomes,
                updated_at = now()
            """,
            (
                user_id,
                store,
                bool((m or {}).get("auto")),
                bool((m or {}).get("userAuto")),
                int((m or {}).get("trust") or 0),
                int((m or {}).get("learn") or 0),
                psycopg.types.json.Json(m or {}),
            ),
        )

    for entry in payload.get("accountLog") or []:
        log_date = None
        raw_d = entry.get("date")
        if raw_d:
            try:
                log_date = date.fromisoformat(str(raw_d)[:10])
            except ValueError:
                log_date = None
        cur.execute(
            """
            INSERT INTO account_log (user_id, client_id, log_date, balances, fact, meta)
            VALUES (%s,%s,%s,%s::jsonb,%s,%s::jsonb)
            """,
            (
                user_id,
                str(entry.get("id") or "") or None,
                log_date,
                psycopg.types.json.Json(entry.get("balances") or {}),
                bool(entry.get("fact")),
                psycopg.types.json.Json(entry),
            ),
        )

    for period, lines in (payload.get("plans") or {}).items():
        cur.execute(
            """
            INSERT INTO plans (user_id, period, lines)
            VALUES (%s,%s,%s::jsonb)
            ON CONFLICT (user_id, period) DO UPDATE SET
                lines = EXCLUDED.lines, updated_at = now()
            """,
            (user_id, str(period)[:7], psycopg.types.json.Json(lines)),
        )

    default_acc = str(payload.get("defaultAccountId") or "") or None
    default_pk = account_id_map.get(default_acc) if default_acc else None
    cur.execute(
        """
        INSERT INTO user_settings (
            user_id, plan_month, plan_pace_modes, plan_envelopes,
            default_account_id, import_skip_preview, updated_at
        ) VALUES (%s,%s,%s::jsonb,%s::jsonb,%s,%s,now())
        ON CONFLICT (user_id) DO UPDATE SET
            plan_month = EXCLUDED.plan_month,
            plan_pace_modes = EXCLUDED.plan_pace_modes,
            plan_envelopes = EXCLUDED.plan_envelopes,
            default_account_id = EXCLUDED.default_account_id,
            import_skip_preview = EXCLUDED.import_skip_preview,
            updated_at = now()
        """,
        (
            user_id,
            payload.get("planMonth") or None,
            psycopg.types.json.Json(payload.get("planPaceModes") or {}),
            psycopg.types.json.Json(payload.get("planEnvelopes") or {}),
            default_pk,
            bool(payload.get("importSkipPreview")),
        ),
    )

    # Deferred «ящик» — payload is SoT; replace open rows for this user.
    cur.execute("DELETE FROM wallet_drawer WHERE user_id = %s", (user_id,))
    for d in payload.get("drawerOps") or []:
        if not isinstance(d, dict):
            continue
        client_id = str(d.get("id") or "")
        if not client_id:
            continue
        cur.execute(
            """
            INSERT INTO wallet_drawer (
                user_id, client_id, occurred_at, store, amount, kind, comment,
                bank, fingerprint, reason, source, filename, status, meta
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,
                %s::jsonb,%s,%s,%s,%s,%s,%s::jsonb
            )
            ON CONFLICT (user_id, client_id) DO UPDATE SET
                occurred_at = EXCLUDED.occurred_at,
                store = EXCLUDED.store,
                amount = EXCLUDED.amount,
                kind = EXCLUDED.kind,
                comment = EXCLUDED.comment,
                bank = EXCLUDED.bank,
                fingerprint = EXCLUDED.fingerprint,
                reason = EXCLUDED.reason,
                source = EXCLUDED.source,
                filename = EXCLUDED.filename,
                status = EXCLUDED.status,
                meta = EXCLUDED.meta,
                updated_at = now()
            """,
            (
                user_id,
                client_id,
                str(d.get("date") or ""),
                str(d.get("store") or ""),
                float(d.get("amount") or 0),
                str(d.get("kind") or "unknown"),
                str(d.get("comment") or d.get("note") or ""),
                psycopg.types.json.Json(d.get("bank") or {}),
                str(d.get("fingerprint") or ""),
                str(d.get("reason") or ""),
                str(d.get("source") or "ui"),
                str(d.get("filename") or ""),
                str(d.get("status") or "open"),
                psycopg.types.json.Json(
                    {k: d.get(k) for k in ("softDup", "at", "from") if d.get(k) is not None}
                ),
            ),
        )


@app.get("/api/health")
def health():
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        db_ok = True
    except Exception as e:  # noqa: BLE001
        db_ok = False
        return {"status": "degraded", "db": False, "error": str(e)[:200]}
    return {"status": "ok", "db": db_ok, "default_user": DEFAULT_USER}


@app.get("/api/v1/users/{username}/state", response_model=StateResponse)
def get_state(username: str):
    with db_connect() as conn, conn.cursor() as cur:
        uid = user_id_by_name(cur, username)
        cur.execute(
            """
            SELECT revision, source, updated_at, payload
            FROM user_app_state WHERE user_id = %s
            """,
            (uid,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "state not found — seed or PUT first")
        revision, source, updated_at, payload = row
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        return StateResponse(
            username=username,
            user_id=uid,
            revision=int(revision),
            source=str(source),
            updated_at=updated_at,
            payload=payload,
            stats=payload_stats(payload),
        )


@app.put("/api/v1/users/{username}/state", response_model=StateResponse)
def put_state(username: str, body: StatePut):
    payload = body.payload
    if not isinstance(payload, dict):
        raise HTTPException(400, "payload must be an object")
    # normalize sets → lists if client sent them oddly
    for key in ("confirmedIds", "deletedIds"):
        if isinstance(payload.get(key), list):
            pass
        elif payload.get(key) is None:
            payload[key] = []

    with db_connect() as conn, conn.cursor() as cur:
        uid = user_id_by_name(cur, username)
        cur.execute(
            "SELECT revision FROM user_app_state WHERE user_id = %s",
            (uid,),
        )
        row = cur.fetchone()
        current_rev = int(row[0]) if row else 0
        if (
            not body.force
            and body.base_revision is not None
            and row is not None
            and int(body.base_revision) != current_rev
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "conflict: state was modified",
                    "current_revision": current_rev,
                    "base_revision": int(body.base_revision),
                },
            )

        if body.normalize:
            sync_normalized(cur, uid, payload)
        cur.execute(
            """
            INSERT INTO user_app_state (user_id, payload, source, revision, updated_at)
            VALUES (%s, %s::jsonb, %s, 1, now())
            ON CONFLICT (user_id) DO UPDATE SET
                payload = EXCLUDED.payload,
                source = EXCLUDED.source,
                revision = user_app_state.revision + 1,
                updated_at = now()
            RETURNING revision, source, updated_at, payload
            """,
            (uid, psycopg.types.json.Json(payload), body.source or "ui"),
        )
        revision, source, updated_at, stored = cur.fetchone()
        conn.commit()
        if isinstance(stored, str):
            stored = json.loads(stored)
        return StateResponse(
            username=username,
            user_id=uid,
            revision=int(revision),
            source=str(source),
            updated_at=updated_at,
            payload=stored,
            stats=payload_stats(stored),
        )


@app.get("/api/v1/users/{username}/summary")
def summary(username: str):
    with db_connect() as conn, conn.cursor() as cur:
        uid = user_id_by_name(cur, username)
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM operations o WHERE o.user_id = %s AND o.bucket = 'personal'),
              (SELECT count(*) FROM operations o WHERE o.user_id = %s AND o.bucket = 'business'),
              (SELECT count(*) FROM operations o WHERE o.user_id = %s AND o.bucket = 'imported'),
              (SELECT revision FROM user_app_state s WHERE s.user_id = %s),
              (SELECT source FROM user_app_state s WHERE s.user_id = %s),
              (SELECT updated_at FROM user_app_state s WHERE s.user_id = %s)
            """,
            (uid, uid, uid, uid, uid, uid),
        )
        p, b, i, rev, source, updated = cur.fetchone()
        return {
            "username": username,
            "user_id": uid,
            "personal": int(p),
            "business": int(b),
            "imported": int(i),
            "revision": int(rev or 0),
            "source": source,
            "updated_at": updated,
        }


def _load_state_row(username: str) -> tuple[int, int, str, Any, dict[str, Any]]:
    with db_connect() as conn, conn.cursor() as cur:
        uid = user_id_by_name(cur, username)
        cur.execute(
            """
            SELECT revision, source, updated_at, payload
            FROM user_app_state WHERE user_id = %s
            """,
            (uid,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "state not found")
        revision, source, updated_at, payload = row
        if isinstance(payload, str):
            payload = json.loads(payload)
        return uid, int(revision), str(source), updated_at, payload


def _write_dump_files(
    username: str,
    *,
    reason: str,
    note: str,
    revision: int,
    source: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    # Folder name: DDMMYY (Moscow date) — easy to scan in a file manager.
    try:
        from zoneinfo import ZoneInfo

        now_local = datetime.now(ZoneInfo("Europe/Moscow"))
    except Exception:  # noqa: BLE001
        now_local = datetime.now(timezone.utc)
    stamp = now_local.strftime("%d%m%y")
    dump_dir = DUMP_ROOT / username / stamp
    dump_dir.mkdir(parents=True, exist_ok=True)
    full_path = dump_dir / "wallet_review_v3.json"
    min_path = dump_dir / "wallet_review_v3.min.json"
    meta_path = dump_dir / "meta.json"

    full_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    min_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    meta = {
        "id": stamp,
        "username": username,
        "reason": reason,
        "note": note,
        "revision": revision,
        "state_source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "local_date": now_local.strftime("%Y-%m-%d"),
        "stats": payload_stats(payload),
        "accounts": payload.get("accounts") or [],
        "plan_month": payload.get("planMonth") or "",
        "paths": {
            "dir": str(dump_dir),
            "full": str(full_path),
            "min": str(min_path),
        },
        "bytes": {
            "full": full_path.stat().st_size,
            "min": min_path.stat().st_size,
        },
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


@app.post("/api/v1/users/{username}/dumps")
def create_dump(username: str, body: DumpCreate):
    """Save a point-in-time JSON dump under WALLET_DUMP_DIR (default /data/dumps)."""
    uid, revision, source, _updated, db_payload = _load_state_row(username)
    payload = body.payload if body.payload is not None else db_payload
    if not isinstance(payload, dict):
        raise HTTPException(400, "payload must be an object")
    reason = (body.reason or "manual").strip()[:64] or "manual"
    note = (body.note or "").strip()[:500]
    try:
        meta = _write_dump_files(
            username,
            reason=reason,
            note=note,
            revision=revision,
            source=source,
            payload=payload,
        )
    except OSError as e:
        raise HTTPException(500, f"cannot write dump: {e}") from e
    meta["user_id"] = uid
    return meta


@app.get("/api/v1/users/{username}/dumps")
def list_dumps(username: str, limit: int = 20):
    """List recent dump folders for a user (newest first)."""
    with db_connect() as conn, conn.cursor() as cur:
        user_id_by_name(cur, username)
    root = DUMP_ROOT / username
    if not root.is_dir():
        return {"username": username, "dumps": []}
    limit = max(1, min(int(limit or 20), 100))

    def sort_key(p: Path) -> tuple:
        # Prefer DDMMYY (150726) newest-first; fall back to name for legacy UTC stamps.
        name = p.name
        if len(name) == 6 and name.isdigit():
            dd, mm, yy = int(name[:2]), int(name[2:4]), int(name[4:6])
            year = 2000 + yy if yy < 70 else 1900 + yy
            return (0, year, mm, dd, name)
        return (1, name)

    dirs = sorted(
        [p for p in root.iterdir() if p.is_dir()],
        key=sort_key,
        reverse=True,
    )[:limit]
    items = []
    for d in dirs:
        meta_path = d / "meta.json"
        if meta_path.is_file():
            try:
                items.append(json.loads(meta_path.read_text(encoding="utf-8")))
                continue
            except json.JSONDecodeError:
                pass
        items.append({"id": d.name, "username": username, "paths": {"dir": str(d)}})
    return {"username": username, "dumps": items}


# --- Statement parse (PDF / screenshot OCR) ---------------------------------

from fastapi import File, UploadFile  # noqa: E402

from statement_parse import parse_statement_upload  # noqa: E402
from receipt_parse import parse_receipt_upload  # noqa: E402


@app.post("/api/v1/users/{username}/statement/parse")
async def statement_parse(username: str, file: UploadFile = File(...)):
    """Extract expense rows from T-Bank PDF or screenshot. Client applies dedup via planImportStatementRows."""
    with db_connect() as conn, conn.cursor() as cur:
        user_id_by_name(cur, username)
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "file too large (max 15 MB)")
    if not data:
        raise HTTPException(400, "empty file")
    try:
        parsed = await parse_statement_upload(data, file.filename or "", file.content_type or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"parse failed: {exc}") from exc
    rows = parsed.get("rows") or []
    return {
        "username": username,
        "source": parsed.get("source"),
        "rows": rows,
        "count": len(rows),
        "warnings": parsed.get("warnings") or [],
        "critical_alerts": parsed.get("critical_alerts") or [],
        "meta": parsed.get("meta") or {},
    }


@app.post("/api/v1/users/{username}/receipt/parse")
async def receipt_parse(username: str, file: UploadFile = File(...)):
    """Extract structured receipt (AD-010 Wave 1). Never writes expenses — client confirms via Gateway."""
    with db_connect() as conn, conn.cursor() as cur:
        user_id_by_name(cur, username)
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "file too large (max 15 MB)")
    if not data:
        raise HTTPException(400, "empty file")
    try:
        parsed = await parse_receipt_upload(data, file.filename or "", file.content_type or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"receipt parse failed: {exc}") from exc
    receipt = parsed.get("receipt") or {}
    return {
        "username": username,
        "ok": bool(parsed.get("ok")),
        "source": "receipt",
        "receipt": receipt,
        "itemCount": len(receipt.get("items") or []),
        "warnings": parsed.get("warnings") or [],
        "critical_alerts": parsed.get("critical_alerts") or [],
        "meta": parsed.get("meta") or {},
    }


# --- Valet (phase 0: read-only navigator + learning loop) -----------------

from valet_knowledge import (  # noqa: E402
    DONT_KNOW_REPLY,
    deferred_answer_text,
    format_knowledge_block,
    is_overdue,
    resolve_product_answer,
    satisfaction_thanks,
)
from valet_llm import valet_draft_deferred_answer, valet_reply  # noqa: E402
from valet_plan import plan_summary  # noqa: E402
from critical_alerts import (  # noqa: E402
    build_system_status_report,
    format_alert_message,
    format_alerts_message,
)


class ValetChatIn(BaseModel):
    message: str = ""
    greeting: bool = False
    conversation_id: str | None = None


class ValetCriticalIn(BaseModel):
    conversation_id: str | None = None
    alerts: list[dict[str, Any]] = []


class ValetGuessStatsIn(BaseModel):
    source: str = ""
    filename: str = ""
    total: int = 0
    identified: int = 0
    correct: int = 0
    learned: int = 0
    skipped_hard: int = 0
    soft_dup: int = 0
    known_merchants: int = 0
    accuracy_pct: float | None = None
    meta: dict[str, Any] = {}


class ValetRatingIn(BaseModel):
    rating: int
    comment: str = ""


class ValetMessagePatch(BaseModel):
    needs_attention: bool | None = None
    is_dont_know: bool | None = None
    hallucination_flag: bool | None = None


def _log_critical_alerts(
    *,
    username: str,
    conversation_id: str,
    alerts: list[dict[str, Any]],
    plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Persist critical alerts as Valet assistant messages; return logged items for UI.

    Dedupes by alert code within the same conversation (6h window) so greeting
    probes don't spam the chat. Codes starting with ``user_error:`` are never
    deduped (each client incident must appear in /logs).
    """
    if not alerts:
        return []
    plan = plan or {}
    logged: list[dict[str, Any]] = []
    recent_codes: set[str] = set()
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT meta->'critical_alert'->>'code'
            FROM valet_messages
            WHERE conversation_id = %s
              AND message_kind = 'critical'
              AND created_at > now() - interval '6 hours'
            """,
            (conversation_id,),
        )
        for row in cur.fetchall():
            if row[0]:
                recent_codes.add(str(row[0]))

    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        alert = _sanitize_critical_alert(alert)
        code = str(alert.get("code") or "")
        is_user_error = code.startswith("user_error:") or isinstance(alert.get("user_error"), dict)
        if code and code in recent_codes and not is_user_error:
            continue
        text = format_alert_message(alert)
        mid = _log_valet_message(
            conversation_id,
            "assistant",
            text,
            plan=plan,
            result={"ok": False, "source": "system", "model": "critical", "error": alert.get("code")},
            needs_attention=True,
            message_kind="critical",
            meta={"critical_alert": alert},
        )
        logged.append({"message_id": mid, "content": text, "alert": alert})
        if code:
            recent_codes.add(code)
    return logged


_USER_ERROR_META_ALLOW = frozenset(
    {
        "filename",
        "mimeType",
        "sizeBytes",
        "httpStatus",
        "provider",
        "engine",
        "model",
        "recognizedCount",
        "rawCount",
        "validCount",
        "failedCount",
        "confidence",
        "sourceLabel",
        "stage",
        "errorType",
        "ok",
    }
)


def _sanitize_critical_alert(alert: dict[str, Any]) -> dict[str, Any]:
    """Drop secrets / receipt body / amounts from persisted user_error payloads."""
    out = dict(alert)
    ue = out.get("user_error")
    if not isinstance(ue, dict):
        return out
    clean: dict[str, Any] = {
        "timestamp": str(ue.get("timestamp") or "")[:40],
        "source": str(ue.get("source") or "")[:40],
        "stage": str(ue.get("stage") or "")[:40],
        "error_type": str(ue.get("error_type") or "")[:60],
        "user_message": str(ue.get("user_message") or "")[:500],
        "technical_message": str(ue.get("technical_message") or "")[:500],
        "stack": str(ue.get("stack") or "")[:2000],
        "session_id": str(ue.get("session_id") or "")[:80],
        "user": str(ue.get("user") or "")[:64],
    }
    meta_in = ue.get("metadata") if isinstance(ue.get("metadata"), dict) else {}
    meta_out: dict[str, Any] = {}
    for k, v in meta_in.items():
        if k not in _USER_ERROR_META_ALLOW:
            continue
        if v is None:
            continue
        if isinstance(v, str):
            meta_out[k] = v[:200]
        elif isinstance(v, (int, float, bool)):
            meta_out[k] = v
    clean["metadata"] = meta_out
    out["user_error"] = clean
    if not out.get("detail"):
        out["detail"] = clean["user_message"]
    return out


@app.post("/api/v1/users/{username}/valet/critical")
async def valet_critical(username: str, body: ValetCriticalIn):
    """Client reports critical system errors into Valet chat (e.g. OpenRouter down during OCR)."""
    alerts = [a for a in (body.alerts or []) if isinstance(a, dict)]
    if not alerts:
        raise HTTPException(400, "alerts required")
    conversation_id = _valet_conversation_id(body.conversation_id)
    _prepare_valet_conversation(username, conversation_id)
    payload = _load_payload(username)
    summary = plan_summary(payload)
    logged = _log_critical_alerts(
        username=username,
        conversation_id=conversation_id,
        alerts=alerts,
        plan=summary,
    )
    return {
        "username": username,
        "conversation_id": conversation_id,
        "messages": logged,
        "text": format_alerts_message(alerts),
    }


@app.post("/api/v1/users/{username}/valet/guess-stats")
def valet_guess_stats_create(username: str, body: ValetGuessStatsIn):
    """Record one import-tutoring accuracy snapshot (date + %)."""
    total = max(0, int(body.total or 0))
    correct = max(0, int(body.correct or 0))
    accuracy = body.accuracy_pct
    if accuracy is None and total > 0:
        accuracy = round(100.0 * correct / total, 2)
    with db_connect() as conn, conn.cursor() as cur:
        uid = user_id_by_name(cur, username)
        cur.execute(
            """
            INSERT INTO valet_guess_stats (
                user_id, source, filename, total, identified, correct, learned,
                skipped_hard, soft_dup, known_merchants, accuracy_pct, meta
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id, created_at, accuracy_pct
            """,
            (
                uid,
                (body.source or "")[:32],
                (body.filename or "")[:500],
                total,
                max(0, int(body.identified or 0)),
                correct,
                max(0, int(body.learned or 0)),
                max(0, int(body.skipped_hard or 0)),
                max(0, int(body.soft_dup or 0)),
                max(0, int(body.known_merchants or 0)),
                accuracy,
                psycopg.types.json.Json(body.meta or {}),
            ),
        )
        row = cur.fetchone()
        cur.execute(
            """
            SELECT accuracy_pct, created_at, total, correct
            FROM valet_guess_stats
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 12
            """,
            (uid,),
        )
        recent = [
            {
                "accuracy_pct": float(r[0]) if r[0] is not None else None,
                "created_at": r[1].isoformat() if r[1] else None,
                "total": int(r[2] or 0),
                "correct": int(r[3] or 0),
            }
            for r in cur.fetchall()
        ]
    return {
        "username": username,
        "id": int(row[0]),
        "created_at": row[1].isoformat() if row[1] else None,
        "accuracy_pct": float(row[2]) if row[2] is not None else None,
        "recent": recent,
    }


@app.get("/api/v1/users/{username}/valet/guess-stats")
def valet_guess_stats_list(username: str, limit: int = 30):
    lim = max(1, min(100, int(limit or 30)))
    with db_connect() as conn, conn.cursor() as cur:
        uid = user_id_by_name(cur, username)
        cur.execute(
            """
            SELECT id, created_at, source, filename, total, identified, correct, learned,
                   skipped_hard, soft_dup, known_merchants, accuracy_pct
            FROM valet_guess_stats
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (uid, lim),
        )
        items = []
        for r in cur.fetchall():
            items.append(
                {
                    "id": int(r[0]),
                    "created_at": r[1].isoformat() if r[1] else None,
                    "source": r[2],
                    "filename": r[3],
                    "total": int(r[4] or 0),
                    "identified": int(r[5] or 0),
                    "correct": int(r[6] or 0),
                    "learned": int(r[7] or 0),
                    "skipped_hard": int(r[8] or 0),
                    "soft_dup": int(r[9] or 0),
                    "known_merchants": int(r[10] or 0),
                    "accuracy_pct": float(r[11]) if r[11] is not None else None,
                }
            )
        avg = None
        if items:
            vals = [i["accuracy_pct"] for i in items if i["accuracy_pct"] is not None]
            if vals:
                avg = round(sum(vals) / len(vals), 2)
    return {"username": username, "items": items, "avg_accuracy_pct": avg}


class KnowledgeIn(BaseModel):
    slug: str | None = None
    title: str
    body: str
    is_active: bool = True
    is_actual: bool = False
    sort_order: int = 100
    source_message_id: int | None = None
    source_question_id: int | None = None


class KnowledgePatch(BaseModel):
    slug: str | None = None
    title: str | None = None
    body: str | None = None
    is_active: bool | None = None
    is_actual: bool | None = None
    sort_order: int | None = None
    # After save: push article body as deferred answer to related open questions
    reply_open_questions: bool | None = None


class KnowledgeFromMessageIn(BaseModel):
    message_id: int
    title: str | None = None
    body: str | None = None


class BacklogIn(BaseModel):
    title: str
    body: str = ""
    status: str = "open"
    source_question_id: int | None = None
    source_message_id: int | None = None


class BacklogPatch(BaseModel):
    title: str | None = None
    body: str | None = None
    status: str | None = None


class QuestionSendIn(BaseModel):
    answer: str | None = None


def _load_payload(username: str) -> dict[str, Any]:
    with db_connect() as conn, conn.cursor() as cur:
        uid = user_id_by_name(cur, username)
        cur.execute(
            "SELECT payload FROM user_app_state WHERE user_id = %s",
            (uid,),
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            raise HTTPException(404, "no app state")
        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise HTTPException(500, "payload not object")
        return payload


def _valet_conversation_id(raw: str | None) -> str:
    if not raw:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(400, "invalid conversation_id") from exc


def _load_knowledge_rows() -> list[dict[str, Any]]:
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, body, is_active, is_actual, sort_order
            FROM valet_knowledge
            WHERE is_active = TRUE
            ORDER BY sort_order, id
            """
        )
        return [
            {
                "id": r[0],
                "title": r[1],
                "body": r[2],
                "is_active": r[3],
                "is_actual": r[4],
                "sort_order": r[5],
            }
            for r in cur.fetchall()
        ]


def _load_knowledge_text() -> str:
    return format_knowledge_block(_load_knowledge_rows())


def _prepare_valet_conversation(username: str, conversation_id: str) -> list[dict[str, str]]:
    with db_connect() as conn, conn.cursor() as cur:
        uid = user_id_by_name(cur, username)
        cur.execute(
            """
            INSERT INTO valet_conversations (id, user_id)
            VALUES (%s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (conversation_id, uid),
        )
        cur.execute(
            "SELECT user_id FROM valet_conversations WHERE id = %s",
            (conversation_id,),
        )
        owner = cur.fetchone()
        if not owner or int(owner[0]) != uid:
            raise HTTPException(404, "conversation not found")
        cur.execute(
            """
            SELECT role, content
            FROM (
                SELECT id, role, content
                FROM valet_messages
                WHERE conversation_id = %s
                  AND message_kind IN ('normal', 'deferred_promise', 'deferred_answer')
                ORDER BY id DESC
                LIMIT 8
            ) recent
            ORDER BY id
            """,
            (conversation_id,),
        )
        return [{"role": str(row[0]), "content": str(row[1])} for row in cur.fetchall()]


def _log_valet_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    plan: dict[str, Any],
    result: dict[str, Any] | None = None,
    is_dont_know: bool = False,
    needs_attention: bool = False,
    message_kind: str = "normal",
    related_user_message_id: int | None = None,
    open_question_id: int | None = None,
    delivered_at: datetime | None = None,
    meta: dict[str, Any] | None = None,
) -> int:
    result = result or {}
    plan_json = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO valet_messages (
                conversation_id, role, content, provider, model, status, usage,
                error, provider_message_id, plan_hash, plan_summary,
                is_dont_know, needs_attention, message_kind,
                related_user_message_id, open_question_id, delivered_at, meta
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s
            )
            RETURNING id
            """,
            (
                conversation_id,
                role,
                content,
                result.get("source"),
                result.get("model"),
                "ok" if result.get("ok", True) else "fallback",
                psycopg.types.json.Json(result.get("usage")) if result.get("usage") is not None else None,
                result.get("error"),
                result.get("message_id"),
                hashlib.sha256(plan_json.encode("utf-8")).hexdigest(),
                psycopg.types.json.Json(plan),
                bool(is_dont_know),
                bool(needs_attention),
                message_kind,
                related_user_message_id,
                open_question_id,
                delivered_at,
                psycopg.types.json.Json(meta or {}),
            ),
        )
        msg_id = int(cur.fetchone()[0])
        cur.execute(
            "UPDATE valet_conversations SET updated_at = now() WHERE id = %s",
            (conversation_id,),
        )
        return msg_id


def _create_open_question(
    *,
    username: str,
    conversation_id: str,
    user_message_id: int,
    question_text: str,
    promise_message_id: int | None = None,
) -> int:
    with db_connect() as conn, conn.cursor() as cur:
        uid = user_id_by_name(cur, username)
        cur.execute(
            """
            INSERT INTO valet_open_questions (
                user_id, conversation_id, user_message_id, promise_message_id, question_text, status
            ) VALUES (%s,%s,%s,%s,%s,'waiting')
            RETURNING id
            """,
            (uid, conversation_id, user_message_id, promise_message_id, question_text),
        )
        qid = int(cur.fetchone()[0])
        if promise_message_id:
            cur.execute(
                "UPDATE valet_messages SET open_question_id = %s WHERE id = %s",
                (qid, promise_message_id),
            )
        cur.execute(
            "UPDATE valet_messages SET open_question_id = %s, needs_attention = TRUE WHERE id = %s",
            (qid, user_message_id),
        )
        return qid


@app.get("/api/v1/users/{username}/valet/plan-summary")
def valet_plan_summary(username: str):
    if username != DEFAULT_USER and username != "makc":
        pass
    payload = _load_payload(username)
    summary = plan_summary(payload)
    return {"username": username, "plan_summary": summary}


@app.post("/api/v1/users/{username}/valet/chat")
async def valet_chat(username: str, body: ValetChatIn):
    payload = _load_payload(username)
    summary = plan_summary(payload)
    knowledge_rows = _load_knowledge_rows()
    knowledge = format_knowledge_block(knowledge_rows)
    conversation_id = _valet_conversation_id(body.conversation_id)
    history = _prepare_valet_conversation(username, conversation_id)
    msg = (body.message or "").strip()
    user_message_id = None
    greeting = bool(body.greeting) and not msg
    if msg:
        msg = msg[:4000]
        user_message_id = _log_valet_message(conversation_id, "user", msg, plan=summary)
        history.append({"role": "user", "content": msg})
    result = await valet_reply(
        user_messages=history,
        plan=summary,
        greeting=greeting,
        knowledge=knowledge,
    )
    raw_reply = result.get("reply") or ""
    resolved = resolve_product_answer(
        user_message=msg,
        model_reply=raw_reply,
        knowledge_rows=knowledge_rows,
        greeting=greeting,
    )
    cleaned = resolved["reply"]
    dont_know = bool(resolved["dont_know"])
    open_question_id = None
    show_continue = False
    message_id = None

    if dont_know and user_message_id and msg:
        reply = DONT_KNOW_REPLY
        message_id = _log_valet_message(
            conversation_id,
            "assistant",
            reply,
            plan=summary,
            result=result,
            is_dont_know=True,
            needs_attention=True,
            message_kind="deferred_promise",
            related_user_message_id=user_message_id,
            meta={"raw_model_reply": (raw_reply or "")[:2000]},
        )
        open_question_id = _create_open_question(
            username=username,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            question_text=msg,
            promise_message_id=message_id,
        )
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE valet_messages SET open_question_id = %s WHERE id = %s",
                (open_question_id, message_id),
            )
        show_continue = True
    else:
        reply = cleaned or raw_reply

    # Mandatory report always includes channel status (OK or critical) on greeting.
    system_status = None
    if greeting:
        system_status = build_system_status_report(list(result.get("critical_alerts") or []))
        block = (system_status.get("report_text") or "").strip()
        if block and block not in (reply or ""):
            reply = (reply or "").rstrip() + "\n\n" + block
        summary = dict(summary)
        summary["system_status"] = system_status

    if not (dont_know and user_message_id and msg):
        message_id = _log_valet_message(
            conversation_id,
            "assistant",
            reply,
            plan=summary,
            result=result,
            message_kind="normal",
            related_user_message_id=user_message_id,
            meta={
                "from_knowledge": bool(resolved.get("from_knowledge")),
                "knowledge_id": resolved.get("knowledge_id"),
                "system_status": system_status,
            },
        )

    critical_alerts = list(result.get("critical_alerts") or [])
    critical_logged = _log_critical_alerts(
        username=username,
        conversation_id=conversation_id,
        alerts=critical_alerts,
        plan=summary,
    )
    # On greeting the status is already inside the report — don't also dump red bubbles.
    critical_messages_out = [] if greeting else critical_logged

    return {
        "username": username,
        "conversation_id": conversation_id,
        "plan_summary": summary,
        "reply": reply,
        "message_id": message_id,
        "user_message_id": user_message_id,
        "dont_know": bool(dont_know and user_message_id),
        "open_question_id": open_question_id,
        "show_continue": show_continue,
        "from_knowledge": bool(resolved.get("from_knowledge")),
        "ok": bool(result.get("ok")),
        "source": result.get("source"),
        "model": result.get("model"),
        "usage": result.get("usage"),
        "error": result.get("error"),
        "system_status": system_status,
        "critical_alerts": critical_alerts,
        "critical_messages": critical_messages_out,
        "phase": 0,
        "writes_enabled": False,
    }


@app.get("/api/v1/users/{username}/valet/inbox")
def valet_inbox(username: str, conversation_id: str | None = None):
    """Deferred answers ready to show (and mark delivered)."""
    with db_connect() as conn, conn.cursor() as cur:
        uid = user_id_by_name(cur, username)
        params: list[Any] = [uid]
        conv_sql = ""
        if conversation_id:
            conv_sql = " AND vm.conversation_id = %s"
            params.append(conversation_id)
        cur.execute(
            f"""
            SELECT vm.id, vm.conversation_id, vm.content, vm.open_question_id,
                   vm.related_user_message_id, vm.created_at, oq.question_text
            FROM valet_messages vm
            JOIN valet_conversations vc ON vc.id = vm.conversation_id
            LEFT JOIN valet_open_questions oq ON oq.id = vm.open_question_id
            WHERE vc.user_id = %s
              AND vm.message_kind = 'deferred_answer'
              AND vm.delivered_at IS NULL
              {conv_sql}
            ORDER BY vm.id
            """,
            params,
        )
        rows = cur.fetchall()
        items = []
        for row in rows:
            mid = int(row[0])
            cur.execute(
                "UPDATE valet_messages SET delivered_at = now() WHERE id = %s",
                (mid,),
            )
            if row[3]:
                cur.execute(
                    """
                    UPDATE valet_open_questions
                    SET status = 'delivered', delivered_at = now(), updated_at = now()
                    WHERE id = %s AND status IN ('ready', 'waiting')
                    """,
                    (int(row[3]),),
                )
            items.append(
                {
                    "message_id": mid,
                    "conversation_id": str(row[1]),
                    "content": row[2],
                    "open_question_id": row[3],
                    "related_user_message_id": row[4],
                    "created_at": row[5],
                    "question_text": row[6],
                    "needs_satisfaction": True,
                }
            )
    return {"username": username, "items": items}


@app.post("/api/v1/users/{username}/valet/messages/{message_id}/rating")
def valet_rate_message(username: str, message_id: int, body: ValetRatingIn):
    if body.rating not in (-1, 1):
        raise HTTPException(400, "rating must be 1 or -1")
    comment = (body.comment or "").strip()[:2000]
    with db_connect() as conn, conn.cursor() as cur:
        uid = user_id_by_name(cur, username)
        cur.execute(
            """
            SELECT vm.id, vm.open_question_id, vm.message_kind, vm.conversation_id
            FROM valet_messages vm
            JOIN valet_conversations vc ON vc.id = vm.conversation_id
            WHERE vm.id = %s AND vc.user_id = %s
            """,
            (message_id, uid),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "message not found")
        cur.execute(
            """
            UPDATE valet_messages
            SET rating = %s, rating_comment = %s, rated_at = now(),
                needs_attention = CASE WHEN %s = -1 THEN TRUE ELSE needs_attention END
            WHERE id = %s
            """,
            (body.rating, comment or None, body.rating, message_id),
        )
        follow_up = None
        open_qid = row[1]
        if body.rating == 1 and row[2] == "deferred_answer" and open_qid:
            cur.execute(
                """
                UPDATE valet_open_questions
                SET status = 'closed_ok', updated_at = now()
                WHERE id = %s
                """,
                (int(open_qid),),
            )
            thanks = satisfaction_thanks()
            follow_id = _log_valet_message(
                str(row[3]),
                "assistant",
                thanks,
                plan={},
                message_kind="normal",
                related_user_message_id=None,
                open_question_id=int(open_qid),
                delivered_at=datetime.now(timezone.utc),
            )
            follow_up = {"message_id": follow_id, "content": thanks}
        elif body.rating == -1 and open_qid:
            cur.execute(
                """
                UPDATE valet_open_questions
                SET status = 'closed_loop', updated_at = now()
                WHERE id = %s
                """,
                (int(open_qid),),
            )
            # reopen attention cycle: create waiting question from dissatisfaction note
            if comment:
                # logged by client as next user message usually; here just flag attention
                pass
    return {"ok": True, "message_id": message_id, "rating": body.rating, "follow_up": follow_up}


# --- Admin: logs / backlog / knowledge / open questions --------------------


@app.get("/api/v1/admin/valet/logs")
def admin_valet_logs(limit: int = 300, username: str | None = None):
    limit = max(1, min(int(limit or 300), 1000))
    with db_connect() as conn, conn.cursor() as cur:
        params: list[Any] = []
        where = ""
        if username:
            where = "WHERE u.username = %s"
            params.append(username)
        params.append(limit)
        cur.execute(
            f"""
            SELECT
                vm.id, vm.created_at, u.username, vm.conversation_id, vm.role, vm.content,
                vm.rating, vm.rating_comment, vm.is_dont_know, vm.needs_attention,
                vm.hallucination_flag, vm.message_kind, vm.open_question_id,
                vm.provider, vm.model, vm.status, vm.error, vm.meta,
                oq.status AS question_status, oq.created_at AS question_created_at
            FROM valet_messages vm
            JOIN valet_conversations vc ON vc.id = vm.conversation_id
            JOIN users u ON u.id = vc.user_id
            LEFT JOIN valet_open_questions oq ON oq.id = vm.open_question_id
            {where}
            ORDER BY vm.created_at DESC, vm.id DESC
            LIMIT %s
            """,
            params,
        )
        items = []
        for r in cur.fetchall():
            meta = r[17] if isinstance(r[17], dict) else {}
            crit = meta.get("critical_alert") if isinstance(meta, dict) else None
            user_error = crit.get("user_error") if isinstance(crit, dict) else None
            q_created = r[19]
            waiting = r[18] in ("waiting", "drafting", "ready", "delivered", "closed_loop")
            overdue = bool(r[8] or r[9]) and r[18] == "waiting" and is_overdue(q_created)
            items.append(
                {
                    "id": r[0],
                    "created_at": r[1],
                    "username": r[2],
                    "conversation_id": str(r[3]),
                    "role": r[4],
                    "content": r[5],
                    "rating": r[6],
                    "rating_comment": r[7],
                    "is_dont_know": r[8],
                    "needs_attention": r[9],
                    "hallucination_flag": r[10],
                    "message_kind": r[11],
                    "open_question_id": r[12],
                    "provider": r[13],
                    "model": r[14],
                    "status": r[15],
                    "error": r[16],
                    "meta": meta or None,
                    "user_error": user_error,
                    "critical_alert": crit,
                    "question_status": r[18],
                    "overdue_24h": overdue,
                    "attention": bool(r[9] or r[8] or r[10] or (r[6] == -1) or overdue or user_error),
                }
            )
    # group by username for UI convenience
    grouped: dict[str, list] = {}
    for it in items:
        grouped.setdefault(it["username"], []).append(it)
    attention_count = sum(1 for it in items if it["attention"])
    overdue_count = sum(1 for it in items if it["overdue_24h"])
    return {
        "items": items,
        "grouped": grouped,
        "stats": {"total": len(items), "attention": attention_count, "overdue_24h": overdue_count},
    }


@app.patch("/api/v1/admin/valet/messages/{message_id}")
def admin_patch_message(message_id: int, body: ValetMessagePatch):
    fields = []
    vals: list[Any] = []
    if body.needs_attention is not None:
        fields.append("needs_attention = %s")
        vals.append(body.needs_attention)
    if body.is_dont_know is not None:
        fields.append("is_dont_know = %s")
        vals.append(body.is_dont_know)
    if body.hallucination_flag is not None:
        fields.append("hallucination_flag = %s")
        vals.append(body.hallucination_flag)
    if not fields:
        raise HTTPException(400, "nothing to update")
    vals.append(message_id)
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE valet_messages SET {', '.join(fields)} WHERE id = %s RETURNING id", vals)
        if not cur.fetchone():
            raise HTTPException(404, "message not found")
    return {"ok": True, "id": message_id}


@app.get("/api/v1/admin/valet/questions")
def admin_list_questions(status: str | None = None):
    with db_connect() as conn, conn.cursor() as cur:
        params: list[Any] = []
        where = ""
        if status and status != "all":
            where = "WHERE oq.status = %s"
            params.append(status)
        cur.execute(
            f"""
            SELECT oq.id, u.username, oq.conversation_id, oq.question_text, oq.status,
                   oq.draft_answer, oq.final_answer, oq.created_at, oq.updated_at,
                   oq.answered_at, oq.delivered_at, oq.user_message_id, oq.deferred_message_id
            FROM valet_open_questions oq
            JOIN users u ON u.id = oq.user_id
            {where}
            ORDER BY oq.created_at DESC
            LIMIT 500
            """,
            params,
        )
        items = []
        for r in cur.fetchall():
            items.append(
                {
                    "id": r[0],
                    "username": r[1],
                    "conversation_id": str(r[2]),
                    "question_text": r[3],
                    "status": r[4],
                    "draft_answer": r[5],
                    "final_answer": r[6],
                    "created_at": r[7],
                    "updated_at": r[8],
                    "answered_at": r[9],
                    "delivered_at": r[10],
                    "user_message_id": r[11],
                    "deferred_message_id": r[12],
                    "overdue_24h": r[4] == "waiting" and is_overdue(r[7]),
                }
            )
    return {"items": items}


@app.post("/api/v1/admin/valet/questions/{question_id}/draft")
async def admin_draft_question(question_id: int):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT oq.question_text, u.username
            FROM valet_open_questions oq
            JOIN users u ON u.id = oq.user_id
            WHERE oq.id = %s
            """,
            (question_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "question not found")
        question_text, username = row[0], row[1]
        cur.execute(
            "UPDATE valet_open_questions SET status = 'drafting', updated_at = now() WHERE id = %s",
            (question_id,),
        )
    knowledge = _load_knowledge_text()
    try:
        plan = plan_summary(_load_payload(username))
    except Exception:  # noqa: BLE001
        plan = {}
    result = await valet_draft_deferred_answer(question=question_text, knowledge=knowledge, plan=plan)
    draft_raw = result.get("reply") or ""
    from valet_knowledge import detect_dont_know  # local import for draft path

    draft, still_unknown = detect_dont_know(draft_raw, user_message=question_text)
    article = None
    try:
        from valet_knowledge import match_knowledge_article, format_article_reply

        article = match_knowledge_article(question_text, _load_knowledge_rows())
        if article and still_unknown:
            draft = format_article_reply(article)
            still_unknown = False
    except Exception:  # noqa: BLE001
        pass

    if still_unknown or not draft.strip():
        draft = (
            "Пока в шпаргалке продукта нет точного ответа на этот вопрос. "
            "Нужно дополнить базу знаний и сформировать ответ вручную."
        )
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE valet_open_questions
            SET draft_answer = %s, status = 'waiting', updated_at = now()
            WHERE id = %s
            """,
            (draft, question_id),
        )
    return {
        "id": question_id,
        "draft_answer": draft,
        "ok": bool(result.get("ok")),
        "source": result.get("source"),
        "model": result.get("model"),
        "still_needs_kb": still_unknown,
    }


@app.post("/api/v1/admin/valet/questions/{question_id}/send")
def admin_send_question(question_id: int, body: QuestionSendIn):
    answer = (body.answer or "").strip()
    result = _send_open_question_answer(question_id, answer or None)
    return result


def _send_open_question_answer(question_id: int, answer: str | None = None) -> dict[str, Any]:
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT oq.question_text, oq.draft_answer, oq.final_answer, oq.conversation_id,
                   oq.user_message_id, oq.status
            FROM valet_open_questions oq
            WHERE oq.id = %s
            """,
            (question_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "question not found")
        question_text, draft, final, conv_id, user_msg_id, status = row
        text = (answer or final or draft or "").strip()
        if not text:
            raise HTTPException(400, "empty answer")
        # Avoid duplicate deferred answers if already ready/delivered with same final
        if status in ("ready", "delivered", "closed_ok") and (final or "").strip() == text:
            return {"ok": True, "message_id": None, "question_id": question_id, "status": status, "skipped": True}
        content = deferred_answer_text(question_text, text)
        cur.execute(
            """
            INSERT INTO valet_messages (
                conversation_id, role, content, status, message_kind,
                related_user_message_id, open_question_id, needs_attention, meta
            ) VALUES (%s,'assistant',%s,'ok','deferred_answer',%s,%s,FALSE,'{}'::jsonb)
            RETURNING id
            """,
            (str(conv_id), content, user_msg_id, question_id),
        )
        mid = int(cur.fetchone()[0])
        cur.execute(
            """
            UPDATE valet_open_questions
            SET final_answer = %s, deferred_message_id = %s, status = 'ready',
                answered_at = now(), updated_at = now()
            WHERE id = %s
            """,
            (text, mid, question_id),
        )
        cur.execute(
            """
            UPDATE valet_messages
            SET needs_attention = FALSE
            WHERE open_question_id = %s AND message_kind = 'deferred_promise'
            """,
            (question_id,),
        )
        # clear attention on related user message too
        if user_msg_id:
            cur.execute(
                "UPDATE valet_messages SET needs_attention = FALSE WHERE id = %s",
                (user_msg_id,),
            )
    return {"ok": True, "message_id": mid, "question_id": question_id, "status": "ready"}


def _find_related_open_question_ids(
    *,
    source_question_id: int | None,
    source_message_id: int | None,
    title: str,
) -> list[int]:
    title_norm = (title or "").strip().lower()
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, question_text, user_message_id
            FROM valet_open_questions
            WHERE status IN ('waiting', 'drafting', 'closed_loop')
            ORDER BY id
            """
        )
        rows = cur.fetchall()
    ids: list[int] = []
    for qid, qtext, user_msg_id in rows:
        if source_question_id and int(qid) == int(source_question_id):
            ids.append(int(qid))
            continue
        if source_message_id and user_msg_id and int(user_msg_id) == int(source_message_id):
            ids.append(int(qid))
            continue
        qt = (qtext or "").strip().lower()
        if title_norm and qt and (qt == title_norm or title_norm in qt or qt in title_norm):
            ids.append(int(qid))
    # unique preserve order
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _reply_from_knowledge_article(kid: int, answer: str, title: str) -> dict[str, Any]:
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_message_id, source_question_id, title, body
            FROM valet_knowledge WHERE id = %s
            """,
            (kid,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "not found")
        source_message_id, source_question_id, db_title, db_body = row
    text = (answer or db_body or "").strip()
    if not text:
        return {"sent": [], "cleared_actual": False, "reason": "empty"}
    qids = _find_related_open_question_ids(
        source_question_id=source_question_id,
        source_message_id=source_message_id,
        title=title or db_title or "",
    )
    sent = []
    for qid in qids:
        try:
            sent.append(_send_open_question_answer(qid, text))
        except HTTPException:
            continue
    cleared = False
    if sent:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE valet_knowledge
                SET is_actual = FALSE, updated_at = now()
                WHERE id = %s
                """,
                (kid,),
            )
            # backfill source_question_id if missing
            if not source_question_id and sent:
                cur.execute(
                    "UPDATE valet_knowledge SET source_question_id = %s WHERE id = %s AND source_question_id IS NULL",
                    (sent[0]["question_id"], kid),
                )
            cleared = True
    return {"sent": sent, "cleared_actual": cleared, "matched_question_ids": qids}


@app.get("/api/v1/admin/valet/knowledge")
def admin_list_knowledge(filter: str = "all"):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, slug, title, body, is_active, is_actual, sort_order, updated_at,
                   source_message_id, source_question_id
            FROM valet_knowledge
            ORDER BY is_actual DESC, sort_order, id
            """
        )
        items = [
            {
                "id": r[0],
                "slug": r[1],
                "title": r[2],
                "body": r[3],
                "is_active": r[4],
                "is_actual": r[5],
                "sort_order": r[6],
                "updated_at": r[7],
                "source_message_id": r[8],
                "source_question_id": r[9],
            }
            for r in cur.fetchall()
        ]
    if filter == "actual":
        items = [i for i in items if i["is_actual"]]
    elif filter == "normal":
        items = [i for i in items if not i["is_actual"]]
    return {
        "items": items,
        "stats": {
            "total": len(items) if filter == "all" else None,
            "actual": sum(1 for i in items if i["is_actual"]) if filter == "all" else None,
        },
    }


@app.post("/api/v1/admin/valet/knowledge")
def admin_create_knowledge(body: KnowledgeIn):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO valet_knowledge (
                slug, title, body, is_active, is_actual, sort_order,
                source_message_id, source_question_id
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                (body.slug or None),
                body.title.strip(),
                body.body.strip(),
                body.is_active,
                body.is_actual,
                body.sort_order,
                body.source_message_id,
                body.source_question_id,
            ),
        )
        kid = int(cur.fetchone()[0])
    return {"ok": True, "id": kid}


@app.post("/api/v1/admin/valet/knowledge/from-message")
def admin_knowledge_from_message(body: KnowledgeFromMessageIn):
    """One-click: put log message into knowledge as «актуально» stub."""
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, content, open_question_id FROM valet_messages WHERE id = %s",
            (body.message_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "message not found")
        content = (row[1] or "").strip()
        title = (body.title or content.split("\n", 1)[0])[:160].strip() or f"Сообщение #{body.message_id}"
        text = (body.body if body.body is not None else content)[:4000]
        # idempotent-ish: reuse existing stub for same source message
        cur.execute(
            "SELECT id FROM valet_knowledge WHERE source_message_id = %s LIMIT 1",
            (body.message_id,),
        )
        existing = cur.fetchone()
        if existing:
            kid = int(existing[0])
            cur.execute(
                """
                UPDATE valet_knowledge
                SET is_actual = TRUE, is_active = TRUE, updated_at = now()
                WHERE id = %s
                """,
                (kid,),
            )
            return {"ok": True, "id": kid, "existing": True}
        cur.execute(
            """
            INSERT INTO valet_knowledge (
                title, body, is_active, is_actual, sort_order, source_message_id, source_question_id
            ) VALUES (%s,%s,TRUE,TRUE,40,%s,%s)
            RETURNING id
            """,
            (title, text, body.message_id, row[2]),
        )
        kid = int(cur.fetchone()[0])
    return {"ok": True, "id": kid, "existing": False}


@app.patch("/api/v1/admin/valet/knowledge/{kid}")
def admin_patch_knowledge(kid: int, body: KnowledgePatch):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT slug, title, body, is_active, is_actual, sort_order,
                   source_message_id, source_question_id
            FROM valet_knowledge WHERE id = %s
            """,
            (kid,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "not found")
        slug = body.slug if body.slug is not None else row[0]
        title = body.title if body.title is not None else row[1]
        text = body.body if body.body is not None else row[2]
        is_active = body.is_active if body.is_active is not None else row[3]
        is_actual = body.is_actual if body.is_actual is not None else row[4]
        sort_order = body.sort_order if body.sort_order is not None else row[5]
        # If we are about to reply from this article, drop «актуально» after send
        cur.execute(
            """
            UPDATE valet_knowledge
            SET slug = %s, title = %s, body = %s, is_active = %s, is_actual = %s,
                sort_order = %s, updated_at = now()
            WHERE id = %s
            RETURNING id
            """,
            (slug, title, text, is_active, is_actual, sort_order, kid),
        )
    # Reply only when UI asks, or when body text itself was saved
    should_reply = body.reply_open_questions
    if should_reply is None:
        should_reply = body.body is not None and bool((text or "").strip())
    reply_info: dict[str, Any] = {"sent": [], "cleared_actual": False}
    if should_reply and (text or "").strip():
        reply_info = _reply_from_knowledge_article(kid, text, title)
    return {"ok": True, "id": kid, "reply": reply_info}


@app.delete("/api/v1/admin/valet/knowledge/{kid}")
def admin_delete_knowledge(kid: int):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM valet_knowledge WHERE id = %s RETURNING id", (kid,))
        if not cur.fetchone():
            raise HTTPException(404, "not found")
    return {"ok": True}


@app.get("/api/v1/admin/valet/backlog")
def admin_list_backlog(status: str = "open"):
    with db_connect() as conn, conn.cursor() as cur:
        params: list[Any] = []
        where = ""
        if status and status != "all":
            where = "WHERE status = %s"
            params.append(status)
        cur.execute(
            f"""
            SELECT id, title, body, status, source_question_id, source_message_id,
                   created_at, updated_at, done_at
            FROM valet_backlog
            {where}
            ORDER BY updated_at DESC, id DESC
            """,
            params,
        )
        items = [
            {
                "id": r[0],
                "title": r[1],
                "body": r[2],
                "status": r[3],
                "source_question_id": r[4],
                "source_message_id": r[5],
                "created_at": r[6],
                "updated_at": r[7],
                "done_at": r[8],
            }
            for r in cur.fetchall()
        ]
    return {"items": items}


@app.post("/api/v1/admin/valet/backlog")
def admin_create_backlog(body: BacklogIn):
    status = body.status if body.status in ("open", "done") else "open"
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO valet_backlog (title, body, status, source_question_id, source_message_id, done_at)
            VALUES (%s,%s,%s,%s,%s, CASE WHEN %s = 'done' THEN now() ELSE NULL END)
            RETURNING id
            """,
            (
                body.title.strip(),
                body.body or "",
                status,
                body.source_question_id,
                body.source_message_id,
                status,
            ),
        )
        bid = int(cur.fetchone()[0])
    return {"ok": True, "id": bid}


@app.patch("/api/v1/admin/valet/backlog/{bid}")
def admin_patch_backlog(bid: int, body: BacklogPatch):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT title, body, status FROM valet_backlog WHERE id = %s", (bid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "not found")
        title = body.title if body.title is not None else row[0]
        body_txt = body.body if body.body is not None else row[1]
        status = body.status if body.status is not None else row[2]
        if status not in ("open", "done"):
            raise HTTPException(400, "bad status")
        cur.execute(
            """
            UPDATE valet_backlog
            SET title = %s, body = %s, status = %s, updated_at = now(),
                done_at = CASE WHEN %s = 'done' THEN COALESCE(done_at, now()) ELSE NULL END
            WHERE id = %s
            """,
            (title, body_txt, status, status, bid),
        )
    return {"ok": True, "id": bid}


@app.delete("/api/v1/admin/valet/backlog/{bid}")
def admin_delete_backlog(bid: int):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM valet_backlog WHERE id = %s RETURNING id", (bid,))
        if not cur.fetchone():
            raise HTTPException(404, "not found")
    return {"ok": True}
