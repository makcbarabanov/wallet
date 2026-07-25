import { useEffect, useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import { currentPeriod, formatDate, formatMoney, formatMoneyAbs } from '../utils/format'

const PIE_FALLBACK = [
  '#2F6B54',
  '#A85D42',
  '#3B82F6',
  '#F59E0B',
  '#8B5CF6',
  '#06B6D4',
  '#EF4444',
  '#84CC16',
]

export default function ReportsPage({ catalog }) {
  const [tab, setTab] = useState('overview')
  const [report, setReport] = useState(null)
  const [transactions, setTransactions] = useState([])
  const [planFact, setPlanFact] = useState([])
  const [budgets, setBudgets] = useState([])
  const [period, setPeriod] = useState(currentPeriod())
  const [walletFilter, setWalletFilter] = useState('')
  const [labeledOnly, setLabeledOnly] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [budgetForm, setBudgetForm] = useState({
    category_id: '',
    wallet_id: '',
    planned_amount: '',
  })

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const params = {
          labeled_only: labeledOnly,
          wallet_id: walletFilter || undefined,
        }
        const [rep, txs, pf, buds] = await Promise.all([
          api.getReport(params),
          api.getTransactions({
            wallet_id: walletFilter || undefined,
            status: labeledOnly ? 'labeled' : undefined,
            limit: 1000,
          }),
          api.getPlanFact(period, walletFilter || undefined),
          api.getBudgets(period),
        ])
        if (!cancelled) {
          setReport(rep)
          setTransactions(txs)
          setPlanFact(pf)
          setBudgets(buds)
        }
      } catch (err) {
        if (!cancelled) setError(err.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [walletFilter, labeledOnly, period, catalog.stats])

  const pieData = useMemo(() => {
    if (!report) return []
    return report.by_category
      .filter((c) => c.category_id != null)
      .map((c) => ({
        name: c.category_name,
        value: Math.abs(c.total),
        color: c.color || PIE_FALLBACK[0],
      }))
  }, [report])

  const barData = useMemo(() => {
    if (!report) return []
    return report.by_month.map((m) => ({
      period: m.period,
      Личный: Math.abs(m.personal),
      Бизнес: Math.abs(m.business),
    }))
  }, [report])

  async function addBudget(e) {
    e.preventDefault()
    if (!budgetForm.category_id || !budgetForm.planned_amount) return
    await api.createBudget({
      category_id: Number(budgetForm.category_id),
      wallet_id: budgetForm.wallet_id ? Number(budgetForm.wallet_id) : null,
      period,
      planned_amount: Number(budgetForm.planned_amount),
    })
    setBudgetForm({ category_id: '', wallet_id: '', planned_amount: '' })
    const [pf, buds] = await Promise.all([
      api.getPlanFact(period, walletFilter || undefined),
      api.getBudgets(period),
    ])
    setPlanFact(pf)
    setBudgets(buds)
  }

  async function removeBudget(id) {
    await api.deleteBudget(id)
    setBudgets((prev) => prev.filter((b) => b.id !== id))
    setPlanFact(await api.getPlanFact(period, walletFilter || undefined))
  }

  const tabs = [
    { id: 'overview', label: 'Обзор' },
    { id: 'table', label: 'Таблица' },
    { id: 'planfact', label: 'План-факт' },
  ]

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="font-display text-3xl font-semibold text-ink-800">Отчёты</h1>
          <p className="mt-2 text-ink-500">
            Таблицы, диаграммы и план-факт по двум кошелькам
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <select
            className="select w-auto"
            value={walletFilter}
            onChange={(e) => setWalletFilter(e.target.value)}
          >
            <option value="">Все кошельки</option>
            {catalog.wallets.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
          <label className="btn-secondary cursor-pointer">
            <input
              type="checkbox"
              className="mr-2"
              checked={labeledOnly}
              onChange={(e) => setLabeledOnly(e.target.checked)}
            />
            Только размеченные
          </label>
        </div>
      </section>

      <div className="flex gap-1 rounded-xl bg-ink-100/80 p-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={[
              'flex-1 rounded-lg px-3 py-2 text-sm font-medium transition',
              tab === t.id
                ? 'bg-white text-ink-800 shadow-soft'
                : 'text-ink-500 hover:text-ink-700',
            ].join(' ')}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <p className="rounded-xl bg-clay-500/10 px-4 py-3 text-sm text-clay-600">
          {error}
        </p>
      )}

      {loading && <p className="text-sm text-ink-400">Считаем отчёт…</p>}

      {!loading && report && tab === 'overview' && (
        <div className="space-y-6">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ['Всего', formatMoney(report.total_amount), report.total_operations],
              ['Личный', formatMoney(report.personal_amount), null],
              ['Бизнес', formatMoney(report.business_amount), null],
              ['Без разметки', formatMoney(report.unlabeled_amount), report.pending_operations + report.review_operations],
            ].map(([label, value, count]) => (
              <div key={label} className="panel px-4 py-4">
                <p className="text-xs text-ink-400">{label}</p>
                <p className="mt-1 font-display text-2xl font-semibold text-ink-800">
                  {value}
                </p>
                {count != null && (
                  <p className="mt-1 text-xs text-ink-400">{count} операций</p>
                )}
              </div>
            ))}
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <div className="panel p-5">
              <h2 className="font-display text-lg font-semibold text-ink-800">
                По категориям
              </h2>
              {pieData.length === 0 ? (
                <p className="mt-8 text-center text-sm text-ink-400">
                  Нет размеченных операций
                </p>
              ) : (
                <div className="mt-4 h-72">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={pieData}
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        innerRadius={55}
                        outerRadius={95}
                        paddingAngle={2}
                      >
                        {pieData.map((entry, i) => (
                          <Cell
                            key={entry.name}
                            fill={entry.color || PIE_FALLBACK[i % PIE_FALLBACK.length]}
                          />
                        ))}
                      </Pie>
                      <Tooltip
                        formatter={(v) => formatMoneyAbs(v)}
                        contentStyle={{
                          borderRadius: 12,
                          border: '1px solid #EDE9E3',
                        }}
                      />
                      <Legend />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>

            <div className="panel p-5">
              <h2 className="font-display text-lg font-semibold text-ink-800">
                По месяцам
              </h2>
              {barData.length === 0 ? (
                <p className="mt-8 text-center text-sm text-ink-400">Нет данных</p>
              ) : (
                <div className="mt-4 h-72">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={barData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#EDE9E3" />
                      <XAxis dataKey="period" tick={{ fontSize: 12 }} />
                      <YAxis tick={{ fontSize: 12 }} />
                      <Tooltip
                        formatter={(v) => formatMoneyAbs(v)}
                        contentStyle={{
                          borderRadius: 12,
                          border: '1px solid #EDE9E3',
                        }}
                      />
                      <Legend />
                      <Bar dataKey="Личный" fill="#2F6B54" radius={[4, 4, 0, 0]} />
                      <Bar dataKey="Бизнес" fill="#A85D42" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {!loading && tab === 'table' && (
        <div className="panel overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="bg-ink-50 text-xs uppercase tracking-wide text-ink-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Дата</th>
                  <th className="px-4 py-3 font-medium">Магазин</th>
                  <th className="px-4 py-3 font-medium">Сумма</th>
                  <th className="px-4 py-3 font-medium">Категория</th>
                  <th className="px-4 py-3 font-medium">Кошелёк</th>
                  <th className="px-4 py-3 font-medium">Комментарий</th>
                </tr>
              </thead>
              <tbody>
                {transactions.map((t) => (
                  <tr key={t.id} className="border-t border-ink-50 hover:bg-ink-50/50">
                    <td className="px-4 py-2.5 text-ink-500">{formatDate(t.date)}</td>
                    <td className="px-4 py-2.5 font-medium text-ink-700">{t.store}</td>
                    <td className="px-4 py-2.5 tabular-nums">
                      {formatMoney(t.amount, { precise: true })}
                    </td>
                    <td className="px-4 py-2.5 text-ink-500">
                      {t.category_name || '—'}
                    </td>
                    <td className="px-4 py-2.5 text-ink-500">{t.wallet_name || '—'}</td>
                    <td className="max-w-[200px] truncate px-4 py-2.5 text-ink-400">
                      {t.comment || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {transactions.length === 0 && (
            <p className="px-4 py-10 text-center text-sm text-ink-400">
              Операций нет — загрузите выписку и разметьте группы
            </p>
          )}
        </div>
      )}

      {!loading && tab === 'planfact' && (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-sm text-ink-500">
              Период{' '}
              <input
                type="month"
                className="input ml-2 w-auto"
                value={period}
                onChange={(e) => setPeriod(e.target.value)}
              />
            </label>
          </div>

          <form
            onSubmit={addBudget}
            className="panel grid gap-3 p-4 sm:grid-cols-4"
          >
            <select
              className="select"
              value={budgetForm.category_id}
              onChange={(e) =>
                setBudgetForm((f) => ({ ...f, category_id: e.target.value }))
              }
              required
            >
              <option value="">Категория…</option>
              {catalog.categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <select
              className="select"
              value={budgetForm.wallet_id}
              onChange={(e) =>
                setBudgetForm((f) => ({ ...f, wallet_id: e.target.value }))
              }
            >
              <option value="">Любой кошелёк</option>
              {catalog.wallets.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
            <input
              type="number"
              min="1"
              step="100"
              className="input"
              placeholder="План, ₽"
              value={budgetForm.planned_amount}
              onChange={(e) =>
                setBudgetForm((f) => ({ ...f, planned_amount: e.target.value }))
              }
              required
            />
            <button type="submit" className="btn-primary">
              <Plus size={16} />
              Добавить бюджет
            </button>
          </form>

          <div className="panel overflow-hidden">
            <table className="w-full text-left text-sm">
              <thead className="bg-ink-50 text-xs uppercase tracking-wide text-ink-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Категория</th>
                  <th className="px-4 py-3 font-medium">Кошелёк</th>
                  <th className="px-4 py-3 font-medium">План</th>
                  <th className="px-4 py-3 font-medium">Факт</th>
                  <th className="px-4 py-3 font-medium">Отклонение</th>
                  <th className="px-4 py-3 font-medium">%</th>
                  <th className="px-4 py-3 font-medium" />
                </tr>
              </thead>
              <tbody>
                {planFact.map((row) => {
                  const over = (row.pct_used || 0) > 100
                  const budget = budgets.find(
                    (b) =>
                      b.category_id === row.category_id &&
                      b.wallet_id === row.wallet_id &&
                      b.period === row.period
                  )
                  return (
                    <tr key={`${row.category_id}-${row.wallet_id}`} className="border-t border-ink-50">
                      <td className="px-4 py-3">
                        <span
                          className="mr-2 inline-block h-2.5 w-2.5 rounded-full"
                          style={{ background: row.category_color }}
                        />
                        {row.category_name}
                      </td>
                      <td className="px-4 py-3 text-ink-500">
                        {row.wallet_name || '—'}
                      </td>
                      <td className="px-4 py-3 tabular-nums">
                        {formatMoneyAbs(row.planned)}
                      </td>
                      <td className="px-4 py-3 tabular-nums">
                        {formatMoneyAbs(row.actual)}
                      </td>
                      <td
                        className={[
                          'px-4 py-3 tabular-nums',
                          row.variance < 0 ? 'text-clay-600' : 'text-pine-700',
                        ].join(' ')}
                      >
                        {formatMoney(row.variance)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-16 overflow-hidden rounded-full bg-ink-100">
                            <div
                              className={`h-full rounded-full ${over ? 'bg-clay-500' : 'bg-pine-600'}`}
                              style={{
                                width: `${Math.min(row.pct_used || 0, 100)}%`,
                              }}
                            />
                          </div>
                          <span className="text-xs text-ink-500">
                            {row.pct_used ?? 0}%
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        {budget && (
                          <button
                            type="button"
                            className="btn-ghost p-1.5"
                            onClick={() => removeBudget(budget.id)}
                            title="Удалить бюджет"
                          >
                            <Trash2 size={14} />
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            {planFact.length === 0 && (
              <p className="px-4 py-10 text-center text-sm text-ink-400">
                Задайте бюджеты на месяц — появится сравнение план/факт
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
