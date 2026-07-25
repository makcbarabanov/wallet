#!/usr/bin/env python3
"""Fast offline regression checks for Wallet's expense gateway contract."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "review_categorization.html"

SERVICE_WORDS = {
    "сервисный",
    "сервис",
    "центр",
    "магазин",
    "торговый",
    "точка",
    "ооо",
    "ип",
    "зао",
    "пао",
    "ao",
    "llc",
    "shop",
    "store",
}


def normalize_store(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[«»\"'`()\[\]{}]", " ", value)
    value = re.sub(r"[^a-zа-яё0-9]+", " ", value, flags=re.IGNORECASE)
    return " ".join(value.split())


def tokens(value: str) -> list[str]:
    return [token for token in normalize_store(value).split() if token not in SERVICE_WORDS]


def store_signal(left: str, right: str, *, date: bool, amount: bool, account: bool) -> str:
    key_left, key_right = normalize_store(left), normalize_store(right)
    if key_left == key_right:
        return "exact"
    left_tokens, right_tokens = tokens(left), tokens(right)
    meaningful_left, meaningful_right = " ".join(left_tokens), " ".join(right_tokens)
    if (
        len(meaningful_left) >= 5
        and len(meaningful_right) >= 5
        and (meaningful_left in meaningful_right or meaningful_right in meaningful_left)
    ):
        return "related"
    token_hit = any(
        len(a) >= 3
        and len(b) >= 3
        and (a == b or a in b or b in a)
        for a in left_tokens
        for b in right_tokens
    )
    return "token" if token_hit and date and amount and account else "none"


def classify(*, bank_id: bool, date: bool, amount: bool, account: bool, store: str) -> str | None:
    if bank_id:
        return "exact"
    if date and amount and account and store != "none":
        return "strong"
    evidence = sum((date, amount, account, store != "none"))
    return "weak" if amount and evidence >= 2 else None


def assert_source_architecture(html: str) -> None:
    assert "const SCHEMA_VERSION = 11;" in html
    assert "function createExpense(params)" in html
    assert "function findDuplicateCandidates(input" in html
    assert "function runGatewayRegressionTests()" in html
    # Exactly one production call site: createExpense -> pushRow. Function declaration excluded.
    calls = [
        line
        for line in html.splitlines()
        if "pushRow(" in line and not line.lstrip().startswith("function pushRow")
    ]
    assert len(calls) == 1, f"direct pushRow call sites remain: {calls}"
    assert "const ok = pushRow(params.wallet, row, { allowSoftDup: true });" in calls[0]
    assert "for (const item of plan.autoItems)" in html
    import_block = html[html.index("function applyImportPlan"): html.index("function walletLabelShort")]
    assert "pushRow(" not in import_block, "Training mode must not auto-write ledger"


def assert_allocation_repair(html: str) -> None:
    """Phase 1 repair pass: v1 flag must not strand legacy reserves (see PROJECT_STATE bug)."""
    assert "'wallet_alloc_phase1_v1','wallet_alloc_phase1_v2'" in html
    assert "function needsAllocationRepair(st)" in html
    assert "function runAllocationRepair(st)" in html
    assert "function copyLegacyReservesToAllocation(st)" in html
    assert "if (migrationsMap(st).wallet_alloc_phase1_v1 != null) return runAllocationRepair(st);" in html
    copy_block = html[
        html.index("function copyLegacyReservesToAllocation(st)"): html.index("function statePayload(st)")
    ]
    assert "setMigrationDone" not in copy_block, "copy helper must not own migration flags"


def reserve_is_active(reserve: dict) -> bool:
    return not reserve.get("migratedToFundId") and not reserve.get("migratedToCreditLimitId")


def is_credit_candidate(reserve: dict) -> bool:
    title = str(reserve.get("title") or "").lower()
    amount = round(float(reserve.get("amount") or 0))
    return bool(re.search(r"кредит|лимит", title)) or amount == 150000


def run_allocation_migration(payload: dict) -> dict:
    """Mirror of runAllocationPhase1Migration + runAllocationRepair for offline checks."""
    flags = payload.setdefault("_migrations", {})
    funds = payload.setdefault("funds", [])
    limits = payload.setdefault("creditLimits", [])
    reserves = payload.setdefault("cashReserves", [])

    if flags.get("wallet_alloc_phase1_v1") is not None:
        if flags.get("wallet_alloc_phase1_v2") is not None:
            return {"skipped": True}
        has_money = any(reserve_is_active(r) and round(float(r.get("amount") or 0)) > 0 for r in reserves)
        migrated_exists = any(f.get("sourceReserveId") for f in funds) or any(
            limit.get("sourceReserveId") for limit in limits
        )
        if not has_money or migrated_exists:
            return {"skipped": True}
        flag = "wallet_alloc_phase1_v2"
    else:
        flag = "wallet_alloc_phase1_v1"

    moved_funds = moved_credit = 0
    for reserve in reserves:
        if not reserve_is_active(reserve):
            continue
        amount = round(float(reserve.get("amount") or 0))
        if amount <= 0:
            continue
        if is_credit_candidate(reserve):
            limits.append(
                {"id": f"cl-{reserve['id']}", "title": reserve["title"], "amount": amount,
                 "sourceReserveId": reserve["id"]}
            )
            reserve["migratedToCreditLimitId"] = f"cl-{reserve['id']}"
            moved_credit += 1
            continue
        funds.append(
            {"id": f"fund-{reserve['id']}", "title": reserve["title"], "amount": amount,
             "sourceReserveId": reserve["id"]}
        )
        reserve["migratedToFundId"] = f"fund-{reserve['id']}"
        moved_funds += 1

    flags[flag] = f'{{"movedCredit":{moved_credit},"movedFunds":{moved_funds}}}'
    return {"flag": flag, "movedCredit": moved_credit, "movedFunds": moved_funds}


def live_payload_shape() -> dict:
    """State observed in production payload revision 269 (funds lost, v1 flag kept)."""
    return {
        "schemaVersion": 11,
        "funds": [{"id": "fund19f95bf76e2", "title": "Тест", "amount": 10000}],
        "creditLimits": [],
        "cashReserves": [
            {"id": "cr19f889ec82a", "title": "Бизнес", "amount": 675000},
            {"id": "cr19f889ee6aa", "title": "Кредитка", "amount": 150000},
        ],
        "_migrations": {"wallet_alloc_phase1_v1": '{"movedCredit":1,"movedFunds":1}'},
    }


def assert_store_combobox(html: str) -> None:
    """Merchant Learning v1: searchable store field + prepared merchant structure."""
    assert "function smartMatchRank(item, query)" in html
    assert "function recordMerchantUsage(name)" in html
    assert "function learnMerchantAlias(canonName, rawName)" in html
    # New merchant fields are seeded on creation.
    assert "count: 0," in html and "aliases: []," in html
    # Alias capture on import correction (raw bank name -> chosen canonical).
    assert "learnMerchantAlias(store, item.storeRaw)" in html


def assert_smart_select(html: str) -> None:
    """SmartSelect: one configurable component behind every reference field."""
    assert "function attachSmartSelect(input, cfg = {})" in html
    # The documented config contract.
    for option in ("source", "searchFields", "displayField", "aliases",
                   "popularity", "allowCreate", "onCreate", "onSelect"):
        assert option in html, option
    # Popularity is derived from the Ledger, so old data ranks correctly.
    assert "function directoryUsage(field," in html
    assert "function categorySmartItems(wallet)" in html
    assert "function expenseSmartItems(category)" in html
    assert "function bizSmartItems(field, fallback)" in html
    # Long lists are capped instead of rendering thousands of rows.
    assert "const SMART_MAX_ROWS" in html
    # Reference fields are wired by config table, not by copied code.
    wiring = html[html.index("Reference fields → SmartSelect"):html.index("manIncCancel')?.addEvent")]
    assert "for (const form of ['manExp', 'editRow', 'modal', 'valetTeach'])" in wiring
    for suffix in ("Store", "Category", "Expense", "Object", "Customer"):
        assert f"byId('{suffix}')" in wiring, suffix
    # Category is a SmartSelect input now, not a <select>, in every form.
    for form in ("manExp", "editRow", "modal", "valetTeach"):
        assert f'<select id="{form}Category">' not in html, form
        assert f'<input id="{form}Category"' in html, form
    # Creating a category from an expense form registers it in the directory.
    assert "onCreate: (name) => ensureCategory(smartWalletOf(walletId), name)" in html
    # Picking a row must behave like a manual edit for dependent fields.
    assert "input.dispatchEvent(new Event('change', { bubbles: true }))" in html


def assert_phase2_wallet_entry(html: str) -> None:
    """Phase 2: expense entry from Personal/Business uses the same createExpense gateway."""
    assert 'id="addPersonalExpenseBtn"' in html
    assert 'id="addBusinessExpenseBtn"' in html
    assert "openManualExpenseModal({ wallet: 'personal' })" in html
    assert "openManualExpenseModal({ wallet: 'business' })" in html
    assert 'id="manExpBusinessFields"' in html
    assert 'id="manExpObject"' in html and 'id="manExpCustomer"' in html
    assert "function syncManExpWalletFields(wallet)" in html
    # Business fields must reach createExpense, not a side path.
    man_save = html[html.index("manExpSave')?.addEventListener"): html.index("let editRowCtx")]
    assert "createExpenseBatch" in man_save
    assert "object," in man_save and "customer," in man_save


def assert_settings_tab(html: str) -> None:
    """Settings tab: AI mode (Assistant locked), free cash, funds and credit limits."""
    assert 'data-view="settings"' in html
    assert 'id="view-settings"' in html
    assert "function renderSettings()" in html
    assert "if (view === 'settings') renderSettings();" in html
    assert "function availableToSpend()" in html
    # Assistant cannot be enabled from the UI alone (AD-009a).
    assert 'value="assistant" disabled' in html
    assert "state.settings.aiMode = 'training'" in html
    # Free-cash preference is writable and used for the «доступно» figure.
    assert 'name="settingsFreeCashMode"' in html
    assert "own_plus_credit" in html
    # Funds and credit limits are managed here.
    assert 'id="settingsFundsList"' in html
    assert 'id="settingsCreditList"' in html
    assert 'id="settingsFundAdd"' in html
    assert 'id="settingsCreditAdd"' in html
    assert "function deleteSettingsFund(id)" in html
    assert "function deleteSettingsCredit(id)" in html
    # Valet report section toggles (animated switches, not checkboxes).
    assert 'id="settingsValetReport"' in html
    assert "const VALET_REPORT_SECTIONS" in html
    assert "function normalizeValetReport(raw)" in html
    assert "function valetReportOn(id)" in html
    assert 'role="switch"' in html
    assert "settings.valetReport = normalizeValetReport(settings.valetReport)" in html
    assert "{ id: 'accountReconciliation', title: 'Журнал сверки'" in html
    assert "function latestAccountReconciliation()" in html
    assert "дней с последней сверки" in html
    assert "дата последней сверки" in html
    assert 'id="settingsValetLlmGreeting"' in html
    assert "valetLlmGreeting" in html
    assert "function valetWelcomeText()" in html
    # normalizeSettings still forces unknown aiMode back to training.
    assert "if (settings.aiMode !== 'training') settings.aiMode = 'training'" in html


def main() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert_source_architecture(html)
    assert_allocation_repair(html)
    assert_store_combobox(html)
    assert_smart_select(html)
    assert_phase2_wallet_entry(html)
    assert_settings_tab(html)

    # Test 5: repair restores the lost fund and credit limit exactly once.
    payload = live_payload_shape()
    result = run_allocation_migration(payload)
    assert result["flag"] == "wallet_alloc_phase1_v2", result
    assert (result["movedFunds"], result["movedCredit"]) == (1, 1), result
    titles = {fund["title"]: fund["amount"] for fund in payload["funds"]}
    assert titles == {"Тест": 10000, "Бизнес": 675000}, titles
    assert [(limit["title"], limit["amount"]) for limit in payload["creditLimits"]] == [("Кредитка", 150000)]
    assert all(not reserve_is_active(reserve) for reserve in payload["cashReserves"])

    # Repair is one-shot: a second pass changes nothing.
    before = str(payload)
    assert run_allocation_migration(payload) == {"skipped": True}
    assert str(payload) == before

    # A fund deleted on purpose stays deleted: its reserve keeps the marker.
    deliberate = live_payload_shape()
    deliberate["cashReserves"][0]["migratedToFundId"] = "fund-gone"
    deliberate["cashReserves"][1]["migratedToCreditLimitId"] = "cl-gone"
    assert run_allocation_migration(deliberate) == {"skipped": True}
    assert [fund["title"] for fund in deliberate["funds"]] == ["Тест"]

    # Fresh payload without the v1 flag still runs the original migration.
    fresh = live_payload_shape()
    fresh["_migrations"] = {}
    assert run_allocation_migration(fresh)["flag"] == "wallet_alloc_phase1_v1"

    # Test 1: unrelated store has no candidate from store evidence.
    signal = store_signal("Новый магазин", "NSP", date=True, amount=False, account=True)
    assert signal == "none"

    # Test 2: mandatory NSP regression.
    signal = store_signal(
        "Сервисный центр NSP",
        "NSP",
        date=True,
        amount=True,
        account=True,
    )
    assert signal == "token"
    assert classify(bank_id=False, date=True, amount=True, account=True, store=signal) == "strong"

    # A short token alone is not enough.
    assert store_signal(
        "Сервисный центр NSP",
        "NSP",
        date=False,
        amount=True,
        account=False,
    ) == "none"

    # Exact bank id remains available but is not required for Strong.
    assert classify(bank_id=True, date=False, amount=False, account=False, store="none") == "exact"

    # Test 3/4 architecture markers: explicit allowed duplicate and manual source.
    assert "duplicateResolution: 'allowed_duplicate'" in html
    assert "sourceType: 'manual'" in html
    assert "training_confirmation_required" in html

    print("Gateway regressions: OK")
    print("- one createExpense -> pushRow call site")
    print("- import has no silent ledger write")
    print("- NSP is Strong only with date+amount+account context")
    print("- allowed_duplicate and manual source paths present")
    print("- phase 1 repair restores lost fund/limit once, keeps deliberate deletions")
    print("- store combobox + merchant learning structure present and wired")
    print("- phase 2 personal/business expense entry uses createExpense")
    print("- SmartSelect wired by config to store/category/expense/object/customer")
    print("- Settings tab: AI Training only, free cash mode, funds and credit CRUD")


if __name__ == "__main__":
    main()
