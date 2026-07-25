# WALLET_GATEWAY_DESIGN — шлюз расходов (AD-009)

Детальный проект **до кода**.  
Канон: [ARCHITECTURE_DECISIONS.md](./ARCHITECTURE_DECISIONS.md) AD-009 / AD-009a, [WALLET_AI_LEARNING.md](./WALLET_AI_LEARNING.md).

**Статус:** реализовано · UI_BUILD 49 / schema 11 · 2026-07-24  
**Режим:** Training (`settings.aiMode = 'training'`) — всегда подтверждение.

---

## 0. Цель шлюза

Единый путь для всех источников расхода:

```text
source (pdf|excel|screenshot|manual|clipboard|api)
  → normalize + fingerprint
  → findDuplicateCandidates()
  → user confirmation (Training: всегда)
  → createExpense()
  → personal|business + debitAccount
  → sourceOperation связан через sourceOperationId
```

Ни один канал не вызывает `pushRow` / `debitAccount` мимо `createExpense()`.

---

## 1. Данные

### 1.1 `importedOps[]` → форма `sourceOperation` (in place)

Существующий массив остаётся. Добавляем поля (ленивая нормализация при чтении):

```text
{
  id,                          // = текущий importedOps[].id
  sourceType,                  // 'pdf'|'excel'|'screenshot'|'manual'|'clipboard'|'api'
  date,                        // как сейчас (iso / display)
  amount,                      // знак банка или −|cost| для manual
  accountId,                   // если известен / выбран
  storeRaw,                    // исходная строка банка
  storeNormalized,             // после normalizeStoreName()
  comment,
  fingerprint,                 // строка-хелпер поиска (НЕ уникальный ключ)
  bankId: null | string,       // если парсер дал; иначе null
  status: 'queued'|'linked'|'rejected'|'merged',
  duplicateResolution:
    'none'|'linked_existing'|'created_new'|'merged'|'allowed_duplicate',
  // legacy-совместимость:
  store, bank, softDup, kind, ...
}
```

Переименование массива в `sourceOperations` — **позже**, без миграции сейчас.

### 1.2 Расход `personal` / `business`

К текущим полям:

```text
{
  opId,
  sourceOperationId,           // id sourceOperation (обязателен для новых через шлюз)
  date, category, expense, store, cost, price, accountId, note, ...
  // опционально:
  fingerprint,                 // снимок на момент подтверждения
}
```

Исторические строки без `sourceOperationId` — валидны (бэкфилл не требуем).

### 1.3 Лог предложений (метрики на лету)

Лёгкий append-only лог (например `learnLog` / отдельный `proposalLog` в payload, урезать по длине):

```text
{
  at, sourceOperationId,
  proposed: { category, wallet, storeNormalized },
  confirmed: { category, wallet, store },   // после пользователя
  matchClass: null|'exact'|'strong'|'weak',
  duplicateResolution,
}
```

Считаем category / wallet / store accuracy и dedup на лету. Отдельный usage-слой — не сейчас.

---

## 2. `findDuplicateCandidates(input) → { candidates, best }`

### 2.1 Вход

```text
{
  date,              // день (время опционально)
  amount,            // число (знак не важен для сравнения |amount|)
  accountId,         // если есть
  storeRaw,
  storeNormalized,
  comment?,
  bankId?,
  fingerprint?,
}
```

Ищет в: `personal` + `business` (+ опционально другие `importedOps` со status linked).

### 2.2 Классы совпадения

| Класс | Условие | В Training |
|-------|---------|------------|
| **Exact** | есть `bankId` у обеих сторон и они равны | диалог (не auto) |
| **Strong** | день ✓ + \|сумма\| ✓ + счёт ✓ + магазин похож ✓ | диалог |
| **Weak** | часть признаков (напр. день+сумма без счёта/магазина) | диалог |

`bankId` в модели есть; **зависимости нет**. Основной режим — Strong / Weak.

### 2.3 Признаки и вес (для UI и статистики)

```text
signals: {
  date:     boolean,   // один календарный день
  amount:   boolean,   // |Δ| < 0.02 ₽
  account:  boolean,   // одинаковый accountId (если оба заданы)
  store:    'exact'|'related'|'token'|'none',
  bankId:   boolean,
}
```

Классификация:

```text
if bankId match                         → Exact
else if date && amount && account && store∈{exact,related,token}
                                        → Strong
else if (≥2 из {date, amount, account, store≠none})
                                        → Weak
else                                    → нет кандидата
```

### 2.4 Матчинг магазинов (v1)

1. Нормализация: lower, кавычки, пробелы, `canonicalStoreName` / alias.  
2. Токены: разбиение по пробелам/пунктуации.  
3. Удаление служебных слов: `сервисный`, `центр`, `ооо`, `ип`, `магазин`, … (список расширяемый).  
4. Безопасное сравнение:
   - exact `storeKey`;
   - related: подстрока, если **обе** стороны ≥ 5 **после** удаления служебных **или** токенный hit;
   - **короткий канон сам по себе (напр. `NSP`) ≠ совпадение**;
   - но если **дата + сумма + счёт** уже ✓ и короткий токен входит в длинное имя банка → `store: 'token'` → **Strong**.

Кейс NSP:

```text
Ledger:  NSP · 21.07.26 · 10511 · Т кредитка
Вход:    Сервисный центр NSP · 21.07.26 · 10511 · Т кредитка
→ Strong · signals: date✓ amount✓ account✓ store=token
```

### 2.5 Формат ответа для UI

```text
{
  candidates: [
    {
      class: 'exact'|'strong'|'weak',
      expense: { wallet, opId, date, store, cost, accountId, category, expense },
      signals: { date, amount, account, store, bankId },
      explanation: [
        'дата ✓', 'сумма ✓', 'счёт ✓', 'магазин похож ✓'
      ],
    },
    ...
  ],
  best: candidates[0] | null,   // сортировка: exact > strong > weak, затем сумма сигналов
}
```

Текст диалога строится из `explanation` / `signals`, не из «магии» ИИ.  
ИИ может только переупорядочить / добавить комментарий — не менять класс.

---

## 3. `createExpense(params) → { ok, opId?, reason?, needsConfirm? }`

### 3.1 Входные параметры

```text
{
  // источник
  sourceType,                 // обязателен
  sourceOperationId?,         // если source уже в importedOps; иначе создаём
  raw?: { date, amount, store, comment, bank, bankId, accountId },

  // решение пользователя / черновик формы
  wallet: 'personal'|'business',
  date, amount, accountId, category, expense, store, note?,
  fundId?,                    // AD-004 optional

  // дедуп
  skipDuplicateCheck?: false, // только после явного выбора в диалоге
  duplicateResolution?: 'none'|'linked_existing'|'created_new'|'merged'|'allowed_duplicate',
  linkToOpId?,                // при linked_existing / merged

  // предложения ИИ (для метрик)
  proposed?: { category, wallet, storeNormalized },
}
```

### 3.2 Проверки (порядок)

1. Валидация: сумма > 0, есть `accountId`, есть `wallet`, дата.  
2. Режим: если `aiMode !== 'training'` и нет AD на Assistant → всё равно вести себя как Training.  
3. Если нет `sourceOperationId` — создать/нормализовать запись в `importedOps` (status `queued`).  
4. Если `!skipDuplicateCheck` → `findDuplicateCandidates`.  
   - есть кандидаты → **не писать ledger**; вернуть `{ ok:false, needsConfirm:true, candidates }` → UI диалог.  
5. Если `duplicateResolution === 'linked_existing'`:  
   - привязать `sourceOperationId` к существующему expense (`linkToOpId`);  
   - status `linked`; **не** debit; **не** новый row.  
6. Если `merged`:  
   - связать source с существующим; status `merged`; **не** второй debit.  
7. Если `created_new` / `allowed_duplicate` / нет кандидатов после подтверждения формы:  
   - `makeRow` + `pushRow` (единственный debit);  
   - проставить `sourceOperationId` на row;  
   - source.status = `linked`;  
   - записать `duplicateResolution`;  
   - записать proposalLog (proposed vs confirmed).  
8. `saveState()`.

### 3.3 Что пишет `sourceOperation` / `importedOps`

| Сценарий | status | duplicateResolution |
|----------|--------|---------------------|
| только попал в очередь | `queued` | `none` |
| связали с существующим | `linked` | `linked_existing` |
| создали новый расход | `linked` | `created_new` или `allowed_duplicate` |
| объединили | `merged` | `merged` |
| отвергли (ящик/reject) | `rejected` | `none` |

### 3.4 Что пишет `personal` / `business`

Только через внутренний вызов `pushRow(makeRow(...))` внутри `createExpense`:

- обычные ledger-поля;
- `sourceOperationId`;
- при расходе с `fundId` — хук фонда (когда Allocation снова в коде).

**Запрещено** снаружи: прямой `pushRow` из UI/импорта после внедрения шлюза (кроме временного адаптера на переходный период).

---

## 4. Диалог подтверждения дубля

Заголовок: **«Возможно, эта операция уже существует»**

Блок совпадений (из `best.explanation`):

```text
Совпадение:
  дата ✓
  сумма ✓
  счёт ✓
  магазин похож ✓
```

Показать карточку существующего расхода (дата, магазин, сумма, счёт, категория).

Кнопки:

| Действие | `duplicateResolution` | Ledger |
|----------|----------------------|--------|
| **Использовать существующий** | `linked_existing` | без нового row / без debit |
| **Создать новый** | `created_new` (или `allowed_duplicate`, если class exact/strong) | новый row + debit |
| **Объединить** | `merged` | без второго debit; source → существующий opId |
| Отмена | — | source остаётся `queued` |

В Training даже Exact идёт через этот диалог.

---

## 5. Как встраиваются текущие функции

```text
                    ┌─────────────────────────────┐
  CSV/PDF/скрин/… → │ ingest → importedOps        │
  буфер / manual  → │   (sourceOperation shape)   │
                    └──────────────┬──────────────┘
                                   ▼
                    findDuplicateCandidates()
                                   ▼
              ┌──── needsConfirm? ────┐
              │ да                    │ нет / после выбора
              ▼                       ▼
     duplicateDialog UI        createExpense()
     (использовать /               │
      создать / объединить)        ├─ makeRow(...)     ← оставить как билдер полей
              │                    ├─ pushRow(...)     ← единственный debit
              └────────────────────┘
                                   ▼
                        personal | business
```

| Текущее | Роль после шлюза |
|---------|------------------|
| **`makeRow`** | Чистый билдер объекта расхода. Без изменений смысла. Вызывается **только** из `createExpense`. |
| **`pushRow`** | Низкоуровневая запись + debit + soft-dup block. Вызывается **только** из `createExpense` (адаптер на переход: старые call sites постепенно заменить). |
| **`confirmGroup`** | UI Разбора: собирает wallet/category/account → вызывает `createExpense({ sourceType:'excel'|…, sourceOperationId: op.id, … })` вместо прямого `pushRow`. |
| **`manualExpenseModal`** | Сначала создаёт/обновляет source (`sourceType:'manual'`), затем `createExpense`. При кандидатах — тот же duplicateDialog. |
| **`planImportStatementRows` / `applyImportPlan`** | Перестают auto-`pushRow`. Hard-skip можно оставить как «кандидат Exact/Strong уже в ledger → всё равно показать в preview как possible dup» в Training; в очередь кладут source, confirm — через шлюз. |
| **`findLedgerSoftDup`** | Поглощается / вызывается изнутри `findDuplicateCandidates` (не отдельная политика). |

Переходный период: пока не все call sites переведены — `pushRow` логирует warning в console / `ledgerLessons`, если вызван без `sourceOperationId` (опционально).

---

## 6. Метрики (на лету)

Из `proposalLog` + `duplicateResolution`:

- category / wallet / store accuracy = доля, где `proposed.* === confirmed.*`;
- dedup recall / precision по классам Exact/Strong/Weak и решениям пользователя.

Отдельный usage-слой — не создаём.

---

## 7. Тест приёмки (обязательный)

Фикстура + проверка матчера (класс Strong, не тихий create):

```text
Уже есть: 21.07.26 · NSP · 10511 · Т кредитка
Вход:     21.07.26 · Сервисный центр NSP · 10511 · Т кредитка
→ Strong, explanation с date/amount/account/store
→ диалог; createExpense без выбора пользователя не пишет второй расход
```

---

## 8. Порядок реализации (когда скажете «код»)

1. Нормализация source-полей на `importedOps` + fingerprint helper.  
2. `findDuplicateCandidates` + тест NSP.  
3. `createExpense` + перевод `confirmGroup` / `manualExpenseModal`.  
4. Диалог дубля.  
5. proposalLog / метрики на лету.  
6. Отключить авто-`pushRow` в `applyImportPlan`.  
7. (Отдельно по волне) Allocation / настройки AI mode UI.

---

## 9. Контрольная точка файлов (2026-07-24)

| Файл | Состояние |
|------|-----------|
| `review_categorization.html` | восстановлен из `review-pack/…20260723.zip` · **UI_BUILD 46 · schema 9** |
| `public/index.html` | копия того же |
| `public/sw.js` | `wallet-shell-v46` |
| Документы AD / learning / этот файл | актуальны |

**Важно:** код Фазы 1 (funds / creditLimits / schema 10) был в пропавшем дереве и **не** входит в этот zip. При кодовой волне Allocation нужно **восстановить заново** или вытащить из transcript — не считать schema 10 уже в HTML.

---

*Документ подтверждён; реализация проверяется по `GATEWAY_IMPLEMENTATION_CHECKLIST.md` и `scripts/test_gateway_regressions.py`.*
