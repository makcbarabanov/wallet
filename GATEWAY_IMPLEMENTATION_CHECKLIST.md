# GATEWAY_IMPLEMENTATION_CHECKLIST

Финальная сверка проекта шлюза с **реальным** кодом восстановленного Wallet  
(`review_categorization.html` · UI_BUILD **46** · schema **9**).

Канон: [WALLET_GATEWAY_DESIGN.md](./WALLET_GATEWAY_DESIGN.md), AD-009.  
**Статус:** подтверждён и выполнен (UI_BUILD 49 / schema 11).  
**Порядок волны (утв.): Вариант А** — сначала восстановить Фазу 1 (funds/creditLimits/schema 10), затем Gateway.

**Сверка:** 2026-07-24.

---

## 0. Решение по Фазе 1 vs Gateway

| Вариант | Суть | Выбор |
|---------|------|-------|
| **А** | Сначала восстановить Фазу 1 (`settings`, `creditLimits`, `funds`, optional `fundId`, schema 10), затем шлюз | **принят** |
| Б | Шлюз на schema 9, потом перенос Allocation | отклонён |

**Почему А:** `fundId` уже часть модели расхода (AD-004/007). Если `createExpense()` собрать без него, придётся снова ломать сердце приложения.

**Факт сверки:** в текущем HTML **нет** `funds` / `creditLimits` / `fundId` / `settings.aiMode` / schema 10 — только legacy `cashReserves`. Фазу 1 нужно вернуть **до** кода шлюза.

---

## 1. Существующие функции — что меняется

### 1.1 `makeRow` (~стр. 7246)

| | |
|--|--|
| **Было** | Билдер полей: `opId, date, category, expense, store, cost/price, accountId, note, object, customer`. |
| **Станет** | Тот же билдер + опционально `sourceOperationId`, `fingerprint`, `fundId`. Без debit. |
| **Почему** | Единый shape расхода; шлюз передаёт связь с источником. |

### 1.2 `pushRow` (~стр. 7304)

| | |
|--|--|
| **Было** | Единственный путь записи в `personal`/`business` + `debitAccount` + блок через `findLedgerSoftDup`. Вызывается из **многих** мест напрямую. |
| **Станет** | **Низкоуровневый** writer. Вызывается **только** из `createExpense()`. Soft-dup внутри либо делегирует в `findDuplicateCandidates`, либо остаётся страховкой. |
| **Почему** | AD-009: ни один канал не создаёт расход мимо шлюза. |

**Прямые вызовы `pushRow` сейчас (все должны уйти в `createExpense`):**

| Место | Роль сегодня |
|-------|----------------|
| `applyImportPlan` (~5376) | **Авто-создание** расходов из `autoItems` без вопроса — **запрещено в Training** |
| Tutor / Valet confirm (~6363) | Подтверждение карточки тьютора → ledger |
| «Неучёл» (~7614) | Быстрый personal/Разовые |
| `confirmGroup` (~7669) | «Верно» / ОК группы в Разборе |
| Modal save одна оп. (~7904) | Модалка разметки |
| Modal save группа (~7921) | Модалка разметки группы |
| `manExpSave` (~10465) | Ручной расход из Разбора |

### 1.3 `confirmGroup` (~7656)

| | |
|--|--|
| **Было** | Читает wallet/category/account с карточки → цикл `pushRow(makeRow(...))`. |
| **Станет** | Собирает те же поля → для каждой op вызывает `createExpense({ sourceType, sourceOperationId: op.id, … })`. При `needsConfirm` — duplicateDialog. |
| **Почему** | «Верно» = подтверждение пользователя, но дедуп и связь source обязательны. |

### 1.4 `manualExpenseModal` / `manExpSave` (~10449)

| | |
|--|--|
| **Было** | Форма → `nextImportOpId` → `pushRow` напрямую. Source в `importedOps` не создаётся. |
| **Станет** | Создать/обновить source (`sourceType:'manual'`) → `createExpense`. Тот же duplicateDialog. Поле `fundId` после Фазы 1. |
| **Почему** | Ручной ввод — тот же вход в систему событий. |

### 1.5 `findLedgerSoftDup` (~4687)

| | |
|--|--|
| **Было** | Exact storeKey + день + сумма + account; короткий `NSP` ≠ `Сервисный центр NSP`. Блокирует `pushRow` toast’ом. |
| **Станет** | Поглощён / обёртнут `findDuplicateCandidates` (Exact/Strong/Weak + token). UI-диалог вместо тихого блока/тихого create. |
| **Почему** | Кейс NSP; единая политика дедупа. |

### 1.6 Import flow

| Функция | Было | Станет |
|---------|------|--------|
| `planImportStatementRows` | hard skip / soft skip / autoItems / reviewItems | кладёт **только** source в очередь (+ preview кандидатов); **не** планирует silent auto-ledger в Training |
| `applyImportPlan` | `pushRow` на каждый `autoItem` | **запрет auto pushRow**; все строки → `importedOps` (source); confirm через Разбор/`createExpense` |
| Preview badges | Hard/Soft/Авто | «в очередь» / «возможный дубль» (класс), без авто-списания |

### 1.7 Доп. call sites (не забыть)

| Место | Действие |
|-------|----------|
| Tutor confirm (~6351) | → `createExpense` |
| «Неучёл» (~7614) | → `createExpense` (явное решение пользователя = confirmation) |
| Modal save (~7904/7921) | → `createExpense` |

---

## 2. Новые сущности

| Сущность | Где хранится | Кто пишет | Кто читает |
|----------|--------------|-----------|------------|
| **Поля sourceOperation** на `importedOps[]` (`sourceType`, `storeRaw`, `storeNormalized`, `bankId`, `status`, `duplicateResolution`, …) | payload `importedOps` | ingest / `createExpense` / диалог дубля | матчер, Разбор, метрики |
| **`duplicateCandidate`** | **не** в payload; runtime результат `findDuplicateCandidates` | матчер | UI диалога |
| **`proposalLog[]`** (или расширение `learnLog`) | payload, урезать по длине | `createExpense` после подтверждения | метрики на лету (accuracy / dedup) |
| **`duplicateResolution`** | на source + в proposalLog | пользователь через диалог / `createExpense` | аналитика |
| **`sourceOperationId` на expense** | `personal[]` / `business[]` | только `createExpense` | связь «откуда расход», метрики |
| **`settings.aiMode`** | `settings` (Фаза 1+) | Настройки UI (позже) | шлюз (сейчас всегда `training`) |
| **`fundId` на expense** | personal/business | `createExpense` / форма | Allocation (Фаза 1) |

Fingerprint — **инструмент поиска**, не уникальный ключ БД.

---

## 3. Обратная совместимость

| Данные | Требование |
|--------|------------|
| Старые `personal` / `business` **без** `sourceOperationId` / `fingerprint` | Работают как сейчас: списки, план, сверка, удаление. Бэкфилл **не** обязателен. Новые расходы через шлюз — с `sourceOperationId`. |
| Старые `importedOps` (только `id, date, store, amount, comment, bank, fingerprint`) | Ленивая нормализация: `storeRaw=store`, `storeNormalized=normalize(store)`, `sourceType` default `'excel'`/`'unknown'`, `status='queued'`, `duplicateResolution='none'`, `bankId=null`. |
| `confirmedIds` / `deletedIds` | Без изменений контракта. |
| Сверка счетов (AD-003) | Не трогаем. |
| `findLedgerSoftDup` на старых строках | Пока живёт как страховка внутри `pushRow`; поведение не ломает историю. |

Миграция schema: после Фазы 1 → **10** (allocation); поля gateway — в той же или следующей schema step (**11**), лениво, без wipe.

---

## 4. Минимальные тестовые сценарии

### Тест 1 — новый расход
- **Вход:** операция без кандидатов.  
- **Ожидание:** после подтверждения пользователя — **один** row в personal/business, один debit, source `status=linked`, `duplicateResolution=created_new` (или `none`→`created_new`).

### Тест 2 — NSP (Strong, без silent create)
- **Есть:** `21.07.26 · NSP · 10511 · Т кредитка`.  
- **Приходит:** `21.07.26 · Сервисный центр NSP · 10511 · Т кредитка`.  
- **Ожидание:** Strong candidate; диалог «Возможно, уже существует» (дата✓ сумма✓ счёт✓ магазин похож✓); **без** подтверждения второй расход **не** создаётся и счёт **не** списывается повторно.

### Тест 3 — разрешённый дубль
- Пользователь в диалоге: **«Создать новый»**.  
- **Ожидание:** новый row + debit; source `duplicateResolution = allowed_duplicate` (для Strong/Exact) или `created_new`; оба расхода видны; два списания осознанны.

### Тест 4 — ручной ввод
- Расход из `manualExpenseModal`.  
- **Ожидание:** путь через `createExpense` (есть source `sourceType=manual`); при кандидате — тот же диалог; не прямой `pushRow`.

### Тест 5 — import auto (регрессия Training)
- Импорт с merchant `auto`+`userAuto`.  
- **Ожидание:** **нет** silent `pushRow` из `applyImportPlan`; строки в Разборе / очереди; списание только после подтверждения.

---

## 5. Порядок внедрения (после подтверждения чеклиста)

```text
1. Восстановить Фазу 1 (Вариант А)
     settings, creditLimits, funds, fundId?, freeCash AD-008, schema 10
2. Gateway
     normalize + fingerprint helpers
     findDuplicateCandidates + тест NSP
     createExpense (+ fundId в makeRow)
     duplicateDialog UI
     перевести все call sites с §1.2
     запрет auto pushRow в applyImportPlan
     proposalLog / метрики на лету
3. Приёмка по тестам 1–5
```

Assistant mode — **не** реализуется.  
ИИ — только предлагает / ранжирует / объясняет.

---

## 6. Риски, найденные сверкой (важно)

1. **`applyImportPlan` сегодня создаёт ledger без вопроса** — главный конфликт с Training; убрать первым при Gateway.  
2. **Семь прямых call sites `pushRow`** — легко забыть tutor / «Неучёл» / modal; чеклист call sites обязателен в PR.  
3. **Фаза 1 отсутствует в HTML** — без Варианта А `createExpense` придётся переписывать под `fundId`.  
4. Документация ≠ код: soft-dup в design уже «диалог», в коде — exact storeKey + toast block. Шлюз это выравнивает.

---

*Выполнено в порядке: backup schema 9 → Фаза 1 → Gateway → регрессии.*
