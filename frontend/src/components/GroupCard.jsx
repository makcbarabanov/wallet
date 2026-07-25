import { useState } from 'react'
import { ChevronDown, Send, Sparkles } from 'lucide-react'
import { formatDate, formatMoney } from '../utils/format'

/**
 * Accordion card for a merchant group.
 * Primary action: pick category + wallet → «Разметить группу».
 */
export default function GroupCard({
  group,
  categories,
  wallets,
  onLabel,
  onReview,
  busyId,
}) {
  const [open, setOpen] = useState(false)
  const [categoryId, setCategoryId] = useState(
    group.suggestedCategory?.id || group.category?.id || ''
  )
  const [walletId, setWalletId] = useState(
    group.suggestedWallet?.id || group.wallet?.id || wallets[0]?.id || ''
  )
  const [saveRule, setSaveRule] = useState(true)
  const [error, setError] = useState(null)

  const busy = busyId === group.id
  const hasSuggestion = Boolean(group.suggestedCategory)

  async function handleLabel() {
    setError(null)
    if (!categoryId || !walletId) {
      setError('Выберите категорию и кошелёк')
      return
    }
    try {
      await onLabel(group.id, {
        category_id: Number(categoryId),
        wallet_id: Number(walletId),
        save_rule: saveRule,
      })
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <article className="panel overflow-hidden transition hover:border-ink-300">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-4 px-5 py-4 text-left"
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate font-display text-lg font-semibold text-ink-800">
              {group.groupName}
            </h3>
            {hasSuggestion && group.status !== 'labeled' && (
              <span className="badge bg-pine-600/10 text-pine-700">
                <Sparkles size={12} className="mr-1" />
                совет: {group.suggestedCategory.name}
              </span>
            )}
            {group.status === 'review' && (
              <span className="badge bg-clay-500/10 text-clay-600">сомнительная</span>
            )}
            {group.status === 'labeled' && (
              <span className="badge bg-pine-600/10 text-pine-700">размечено</span>
            )}
          </div>
          <p className="mt-1 text-sm text-ink-400">
            {group.count}{' '}
            {group.count === 1 ? 'операция' : group.count < 5 ? 'операции' : 'операций'}
            {' · '}
            <span
              className={
                group.totalSum < 0
                  ? 'font-medium text-ink-700'
                  : 'font-medium text-pine-600'
              }
            >
              {formatMoney(group.totalSum)}
            </span>
          </p>
        </div>
        <ChevronDown
          size={18}
          className={`mt-1 shrink-0 text-ink-400 transition ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="border-t border-ink-100 px-5 py-4">
          {group.status !== 'labeled' && (
            <div className="mb-4 grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-ink-500">
                  Категория
                </span>
                <select
                  className="select"
                  value={categoryId}
                  onChange={(e) => setCategoryId(e.target.value)}
                >
                  <option value="">Выберите…</option>
                  {categories.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="mb-1 block text-xs font-medium text-ink-500">
                  Кошелёк
                </span>
                <select
                  className="select"
                  value={walletId}
                  onChange={(e) => setWalletId(e.target.value)}
                >
                  {wallets.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
              </label>

              <div className="flex items-end gap-2">
                <button
                  type="button"
                  className="btn-primary w-full sm:w-auto"
                  disabled={busy}
                  onClick={handleLabel}
                >
                  Разметить группу
                </button>
              </div>
            </div>
          )}

          {group.status !== 'labeled' && (
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <label className="flex items-center gap-2 text-xs text-ink-500">
                <input
                  type="checkbox"
                  checked={saveRule}
                  onChange={(e) => setSaveRule(e.target.checked)}
                  className="rounded border-ink-300 text-pine-600 focus:ring-pine-500"
                />
                Запомнить как правило для следующих выписок
              </label>

              {onReview && group.status === 'pending' && (
                <button
                  type="button"
                  className="btn-ghost text-xs"
                  disabled={busy}
                  onClick={() => onReview(group.id)}
                >
                  <Send size={14} />
                  На согласование
                </button>
              )}
            </div>
          )}

          {group.status === 'labeled' && (
            <p className="mb-3 text-sm text-ink-500">
              Категория:{' '}
              <span className="font-medium text-ink-700">
                {group.category?.name || '—'}
              </span>
              {' · '}
              Кошелёк:{' '}
              <span className="font-medium text-ink-700">
                {group.wallet?.name || '—'}
              </span>
            </p>
          )}

          {error && (
            <p className="mb-3 rounded-lg bg-clay-500/10 px-3 py-2 text-sm text-clay-600">
              {error}
            </p>
          )}

          <div className="overflow-x-auto rounded-xl border border-ink-100">
            <table className="w-full min-w-[480px] text-left text-sm">
              <thead className="bg-ink-50 text-xs uppercase tracking-wide text-ink-400">
                <tr>
                  <th className="px-3 py-2 font-medium">Дата</th>
                  <th className="px-3 py-2 font-medium">Сумма</th>
                  <th className="px-3 py-2 font-medium">Комментарий</th>
                </tr>
              </thead>
              <tbody>
                {group.operations.map((op) => (
                  <tr key={op.id} className="border-t border-ink-50">
                    <td className="px-3 py-2 text-ink-500">{formatDate(op.date)}</td>
                    <td className="px-3 py-2 font-medium tabular-nums">
                      {formatMoney(op.amount, { precise: true })}
                    </td>
                    <td className="px-3 py-2 text-ink-400">
                      {op.comment || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </article>
  )
}
