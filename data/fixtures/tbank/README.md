# Test CSV fixtures for Wallet (Т‑Банк style)

These files exercise import/dedup without touching production money.

| File | Purpose |
|------|---------|
| `01_basic_ok.csv` | Happy path: 3 distinct expenses |
| `02_hard_duplicate.csv` | Exact twin row → must be **hard-skipped** (2nd «Яндекс Такси») |
| `03_soft_duplicate.csv` | Same date+amount+normalized store, different spelling/category → **soft-dup → Разбор**, not silent skip |
| `04_xss_store_names.csv` | Malicious/HTML chars in Описание → UI must use `escapeHtml` |
| `05_nsp_duplicate_candidate.csv` | Existing `NSP` + same day/amount/account → **Strong candidate**, dialog, no silent debit |

## How to use

1. Open https://wallet.islanddream.ru
2. Prefer a dump first (or use a throwaway browser profile)
3. **Разбор → Загрузить выписку** → pick a fixture
4. Check preview badges: Hard-дубли / Soft-дубли

## Why fixtures exist (product reason)

Bank CSVs silently change: encoding, quotes around merchant names, MCC, card masks.
Without fixtures you only discover regressions when a real import double-books or drops a payment.
Fixtures are the cheapest “accounting insurance” for `planImportStatementRows` / fingerprints.
