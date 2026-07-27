# PROJECT_STATE — текущее состояние Wallet

Живой короткий снимок. Канон: [ARCHITECTURE_DECISIONS.md](./ARCHITECTURE_DECISIONS.md).

**Карта развития (визуальная):** [wallet_roadmap.html](./wallet_roadmap.html) · онлайн: <https://wallet.islanddream.ru/roadmap>
(деплой-копия — `public/roadmap/index.html`; при правке roadmap обновлять обе).

**Машинный контекст для Ардена:** [project-state.json](./project-state.json) · онлайн: <https://wallet.islanddream.ru/project-state.json>
(деплой-копия — `public/project-state.json`; контракт `contextVersion: 1`; секретов и финансовых данных внутри нет).

**Контракт JSON:** `contextVersion`, `updatedAt`, `version`, `architectureRules[]` (жёсткие AD), `accepted` / `inProgress` / `nextActions`, `doNotDoNow`. Обновлять одновременно с этим файлом. Не копия roadmap — официальный машинный контекст.

**Снимок:** 2026-07-27 · UI_BUILD **78** / schema **12** · contextVersion **1**. **AD-011** + **Этап 1 reconciliation** (банк ≠ товар; `bank.comment` не productName). Разбор: **1 карточка = 1 расход**; ящик: «Вернуть назад». Purchase↔Bank — волна 2 после приёмки Этапа 1.

---

## Контрольная точка

| Файл | Статус |
|------|--------|
| `review_categorization.html` / `public/index.html` | UI_BUILD **78** · schema **12** · идентичны |
| `public/sw.js` | `wallet-shell-v78` |
| `README.md` | создан: запуск, тесты, карта документации, раздел «UI Patterns → SmartSelect» |
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
- SmartSelect (v53): комбобокс магазина выделен в универсальный `attachSmartSelect(input, cfg)` с настройками `source / searchFields / displayField / aliases / popularity / allowCreate / onCreate / onSelect`. Подключены Магазин, Категория, Расход, Объект, Заказчик во всех четырёх формах (`manExp*`, `editRow*`, `modal*`, `valetTeach*`). Категория была `<select>` → стала поиск с подсказками; создание новой идёт через `ensureCategory()`. Популярность считается по строкам Ledger (`directoryUsage`), поэтому старые данные ранжируются без миграции. Выбор строки шлёт `change`, зависимая логика форм не тронута. Не переведены `.as-cat` / `.as-exp` в карточках разбора.
- **AD-010 (2026-07-26):** сущность «Покупка» обязательна; банк = деньги; чек = состав.
- **AD-010.1 (2026-07-26):** единый поток обучения — выписка / скрин / чек; пакетный экран чека отменён.
- **Чеки волна 1 (v61):** кнопка «Чек»; `/receipt/parse`; позиции → `runImportPlanFromRows` / tutor; `purchases[]` (schema 12). Дружба с банком — не в этой волне.
- **Ошибки импорта (v74):** `reportUserError()` — модалка + безопасная запись в тот же `/logs` (без фото/сумм/ключей); частичное распознавание → Продолжить/Отмена.
- **Позиция чека (v75):** `normalize_receipt_line_item` / `normalizeReceiptLineItem` — unit/qty/price/amount; нарушение математики → doubt → вопрос в Разборе; поля доходят до `createExpense`/`makeRow`.

---

## Проверено

- Миграция Фазы 1: повторный запуск не меняет payload; нет дублей funds/creditLimits; legacy сохранён; personal/business не изменены.
- `scripts/test_gateway_regressions.py`: PASS.
- `scripts/test_smart_select.js`: PASS (поиск, популярность из Ledger, алиасы и кросс-алфавит, 20 000 строк — сборка и поиск ~0,4 с). Запуск: `docker run --rm -v "$PWD":/w -w /w node:20-alpine node scripts/test_smart_select.js` (node на хосте не установлен).
- `scripts/test_user_error_handler.js`: PASS (sanitize метаданных, классификация, лог не блокирует модалку).
- `scripts/test_ad011_source_dedup.js`: PASS.
- `scripts/test_receipt_parse.py` + `scripts/test_receipt_line_math.js`: PASS (Магнит перец ok/doubt без автопочинки, штучный, conflict).
- IDE lints: 0.

## Известные проблемы

Полный список с деталями — раздел «Мои текущие проблемы / баги» в [roadmap](./wallet_roadmap.html).

- **Потеря файлов:** риск снят Git-ом (25.07.2026). Дампы/секреты по-прежнему локально. Репозиторий сейчас Public → лучше Private.
- Модалка добавления расхода: **принято 25.07.2026 (v50)** — пользователь подтвердил на смартфоне.
- Фонды: **принято 25.07.2026 (v50)** — «Тест», «Бизнес» 675 000, лимит 150 000 на смартфоне.
- Поле «Магазин»: **принято 25.07.2026 (v51)**.
- Расход из Личного / Бизнеса: **принято 25.07.2026 (v52)**.
- SmartSelect: **Проверил 25.07.2026 (v54)** — пустая категория, «з» ≠ «Жура», «оз»→Ozon.
- Настройки: **Проверил 26.07.2026 (v58)** — AI Training, отчёт Валета (переключатели + журнал сверки), свободные, фонды, лимиты.
- Yandex AI Studio: **основной** на разборе чека (OCR → YandexGPT). Цепочка: **Яндекс → Timeweb → OpenRouter** (Qwen / Llama Scout / Nova Lite).
- **AD-011 (2026-07-26):** единый конвейер источников (банк/чек/…) → Разбор → `createExpense()`; без отдельных путей записи.
- **v76:** инвариант AD-010.1 — одна карточка Валета = один расход; убрана агрегация `groupValetTutorItems` и кнопки «Верно · все N».
- **v77:** в ящике кнопка «Вернуть назад» — отмена случайного «В ящик», расход снова в Разбор.
- **PDF-справка Т‑Банк:** парсер больше не берёт «последнее число» (хвост карты 0507≠7); блоки + сумма с ₽ → тот же `normalize_row`, что CSV.
- v68: при входе в Разбор, если очередь не пуста — модалка «Незавершённый разбор… Продолжить? Да/Нет» (оба ведут в обычный Разбор; уже записанное остаётся).
- Новые идеи 26.07.2026: настраиваемый дневной лимит расходов на ИИ со счётчиком и жёсткой остановкой; нижнее мобильное меню с распределением пунктов между верхом и низом.
- `/logs`: **Проверил 26.07.2026** — админка обучения Валета (логи / вопросы / KB).

## Следующий шаг

1. Приёмка v76: одна карточка = один расход (нет «Группа» / «Верно · все N»; resume с середины).
2. Приёмка волны 1 + AD-011 (v67) — «Проверил»: повтор источника → «N новых»; похожие → вопрос.
3. Добавить дневной финансовый лимит ИИ, счётчик и остановку при достижении порога.
4. Волна 2: дружба с выпиской на уровне Покупки.
5. Потом: Receipt Learning / merchantId / Assistant.
