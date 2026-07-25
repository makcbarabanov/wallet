# PROJECT_STATE — текущее состояние Wallet

Живой короткий снимок. Канон: [ARCHITECTURE_DECISIONS.md](./ARCHITECTURE_DECISIONS.md).

**Карта развития (визуальная):** [wallet_roadmap.html](./wallet_roadmap.html) · онлайн: <https://wallet.islanddream.ru/roadmap>
(деплой-копия — `public/roadmap/index.html`; при правке roadmap обновлять обе).

**Машинный контекст для Ардена:** [project-state.json](./project-state.json) · онлайн: <https://wallet.islanddream.ru/project-state.json>
(деплой-копия — `public/project-state.json`; контракт `contextVersion: 1`; секретов и финансовых данных внутри нет).

**Контракт JSON:** `contextVersion`, `updatedAt`, `version`, `architectureRules[]` (жёсткие AD), `accepted` / `inProgress` / `nextActions`, `doNotDoNow`. Обновлять одновременно с этим файлом. Не копия roadmap — официальный машинный контекст.

**Снимок:** 2026-07-25 · UI_BUILD **52** / schema 11 · contextVersion **1**. Roadmap — компактный аккордеон. **Фокус: стабилизация, не новые фичи.** Ждёт приёмки: расход из Личный/Бизнес. Следом: SmartSelect справочников → Настройки.

---

## Контрольная точка

| Файл | Статус |
|------|--------|
| `review_categorization.html` / `public/index.html` | UI_BUILD **52** · schema **11** · идентичны |
| `public/sw.js` | `wallet-shell-v52` |
| `api/` | восстановлен из контейнера 2026-07-25 (`main.py`, `statement_parse.py`, `critical_alerts.py`, `valet_*.py`, `requirements.txt`) |
| дамп-страховка | `data/dumps/makc/250726/` · revision 269 · reason `pre_repair_wave` |
| `WALLET_GATEWAY_DESIGN.md` | основа реализации — подтверждена |
| `GATEWAY_IMPLEMENTATION_CHECKLIST.md` | подтверждён и выполнен |
| Git | ✅ init + push `main` · commit `aae4e1e` · [makcbarabanov/wallet](https://github.com/makcbarabanov/wallet) |
| `.gitignore` | исключает `.env`, dumps, `wallet_review_v3*`, выписки, `backup/`, zip |
| `wallet_roadmap.html` / `public/roadmap/index.html` | живая карта развития · идентичны · `/roadmap` = 200 |
| `project-state.json` / `public/project-state.json` | официальный машинный контекст · `contextVersion: 1` · `/project-state.json` = 200 |

**Вариант А выполнен:** сначала восстановлена Фаза 1, затем Gateway.

**Перед будущим Git:** `.gitignore`, исключить `.env`, ключи, дампы.

---

## Реализовано

- Allocation: `settings`, `creditLimits`, `funds`, optional `fundId`; legacy copy+markers; свободные по AD-008.
- schema **9 → 10**: Фаза 1; schema **10 → 11**: `proposalLog`.
- Gateway: sourceOperation-поля на `importedOps`, matcher Exact/Strong/Weak, `createExpense`, диалог дубля.
- Все пути создания расхода идут через `createExpense`; единственный production-call `pushRow` — внутри шлюза.
- Training only: `applyImportPlan` больше не создаёт Ledger; все строки идут в Разбор.
- `duplicateResolution`, `sourceOperationId`, proposalLog; старые расходы работают без новых полей.
- NSP fixture: `data/fixtures/tbank/05_nsp_duplicate_candidate.csv`.
- Store combobox (v51): поле «Магазин» — searchable dropdown (популярные + алфавит, поиск по началу/части слова, кросс-алфавит translit, «Создать новый»). Подключено к `manExpStore` / `editRowStore` / `modalStore`.
- Merchant Learning (структура, v51): `count` / `lastUsed` / `aliases[]` на merchant; `recordMerchantUsage` в `recordOutcome`; `learnMerchantAlias` при импортной коррекции. Дедуп пока не меняется (алиасы — только поиск/подсказка).

---

## Проверено

- Миграция Фазы 1: повторный запуск не меняет payload; нет дублей funds/creditLimits; legacy сохранён; personal/business не изменены.
- `scripts/test_gateway_regressions.py`: PASS.
- JavaScript parse (tree-sitter) для source/public: 0 ошибок.
- IDE lints: 0.

## Известные проблемы

Полный список с деталями — раздел «Мои текущие проблемы / баги» в [roadmap](./wallet_roadmap.html).

- **Потеря файлов:** риск снят Git-ом (25.07.2026). Дампы/секреты по-прежнему локально. Репозиторий сейчас Public → лучше Private.
- Модалка добавления расхода: **принято 25.07.2026 (v50)** — пользователь подтвердил на смартфоне.
- Фонды: **принято 25.07.2026 (v50)** — «Тест», «Бизнес» 675 000, лимит 150 000 на смартфоне.
- Поле «Магазин»: **принято 25.07.2026 (v51)**.
- Yandex AI Studio: в локальном `.env` подготовлены `YANDEX_AI_STUDIO_API_KEY` / `FOLDER_ID` / `BASE_URL` / `MODEL` (учебный ключ; в Git не коммитится). Подключение в код API — ещё план.
- `/logs`: nginx ищет `logs.html`, страница лежит как `public/logs/index.html` → 404.

## Следующий шаг

1. **Стабилизация:** приёмка v52 (расход из Личный / Бизнес).
2. Универсальный SmartSelect для справочников (как у магазинов).
3. Вкладка «Настройки» (Assistant выключен).
4. Мелкий баг `/logs`.
