import { useEffect, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '../api/client'

export default function CategoriesPage({ catalog }) {
  const [name, setName] = useState('')
  const [color, setColor] = useState('#2F6B54')
  const [rules, setRules] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function loadRules() {
    try {
      setRules(await api.getRules())
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    loadRules()
  }, [])

  async function createCategory(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.createCategory({ name: name.trim(), color })
      setName('')
      await catalog.refresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function removeRule(id) {
    await api.deleteRule(id)
    setRules((prev) => prev.filter((r) => r.id !== id))
  }

  return (
    <div className="space-y-8">
      <section>
        <h1 className="font-display text-3xl font-semibold text-ink-800">
          Категории и правила
        </h1>
        <p className="mt-2 max-w-2xl text-ink-500">
          Категории создаёте вы. Правила появляются после разметки групп и
          подсказывают категорию при следующих загрузках — без внешних API.
        </p>
      </section>

      {error && (
        <p className="rounded-xl bg-clay-500/10 px-4 py-3 text-sm text-clay-600">
          {error}
        </p>
      )}

      <form onSubmit={createCategory} className="panel flex flex-wrap items-end gap-3 p-4">
        <label className="min-w-[200px] flex-1">
          <span className="mb-1 block text-xs font-medium text-ink-500">
            Новая категория
          </span>
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Например: Софт для команды"
            required
          />
        </label>
        <label>
          <span className="mb-1 block text-xs font-medium text-ink-500">Цвет</span>
          <input
            type="color"
            value={color}
            onChange={(e) => setColor(e.target.value)}
            className="h-10 w-14 cursor-pointer rounded-lg border border-ink-200 bg-white p-1"
          />
        </label>
        <button type="submit" className="btn-primary" disabled={busy}>
          <Plus size={16} />
          Создать
        </button>
      </form>

      <div>
        <h2 className="font-display text-xl font-semibold text-ink-800">
          Список категорий
        </h2>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {catalog.categories.map((c) => (
            <div
              key={c.id}
              className="flex items-center gap-3 rounded-xl border border-ink-100 bg-white/70 px-4 py-3"
            >
              <span
                className="h-3 w-3 rounded-full"
                style={{ background: c.color }}
              />
              <span className="font-medium text-ink-700">{c.name}</span>
              {c.is_system && (
                <span className="ml-auto text-[10px] uppercase tracking-wide text-ink-300">
                  система
                </span>
              )}
            </div>
          ))}
        </div>
      </div>

      <div>
        <h2 className="font-display text-xl font-semibold text-ink-800">
          Локальные правила
        </h2>
        <p className="mt-1 text-sm text-ink-400">
          Магазин → категория + кошелёк. Применяются только как подсказки.
        </p>
        <div className="panel mt-3 overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="bg-ink-50 text-xs uppercase tracking-wide text-ink-400">
              <tr>
                <th className="px-4 py-3 font-medium">Магазин</th>
                <th className="px-4 py-3 font-medium">Категория</th>
                <th className="px-4 py-3 font-medium">Кошелёк</th>
                <th className="px-4 py-3 font-medium">Срабатываний</th>
                <th className="px-4 py-3 font-medium" />
              </tr>
            </thead>
            <tbody>
              {rules.map((r) => (
                <tr key={r.id} className="border-t border-ink-50">
                  <td className="px-4 py-2.5 font-medium text-ink-700">{r.pattern}</td>
                  <td className="px-4 py-2.5 text-ink-500">{r.category_name}</td>
                  <td className="px-4 py-2.5 text-ink-500">{r.wallet_name}</td>
                  <td className="px-4 py-2.5 tabular-nums text-ink-500">
                    {r.hit_count}
                  </td>
                  <td className="px-4 py-2.5">
                    <button
                      type="button"
                      className="btn-ghost p-1.5"
                      onClick={() => removeRule(r.id)}
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rules.length === 0 && (
            <p className="px-4 py-8 text-center text-sm text-ink-400">
              Правил пока нет — разметьте первую группу
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
