import { useCallback, useEffect, useState } from 'react'
import { Search } from 'lucide-react'
import { api } from '../api/client'
import GroupCard from '../components/GroupCard'
import { formatMoneyAbs } from '../utils/format'

export default function GroupsPage({ catalog, status = 'pending' }) {
  const [groups, setGroups] = useState([])
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [busyId, setBusyId] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.getGroups({ status, q: q || undefined })
      setGroups(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [status, q])

  useEffect(() => {
    const t = setTimeout(load, q ? 250 : 0)
    return () => clearTimeout(t)
  }, [load, q])

  async function handleLabel(groupId, payload) {
    setBusyId(groupId)
    try {
      await api.labelGroup(groupId, payload)
      setGroups((prev) => prev.filter((g) => g.id !== groupId))
      await catalog.refresh()
    } finally {
      setBusyId(null)
    }
  }

  async function handleReview(groupId) {
    setBusyId(groupId)
    try {
      await api.sendToReview(groupId)
      setGroups((prev) => prev.filter((g) => g.id !== groupId))
      await catalog.refresh()
    } finally {
      setBusyId(null)
    }
  }

  const totalSum = groups.reduce((s, g) => s + Math.abs(g.totalSum), 0)

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-display text-3xl font-semibold text-ink-800">
            Группы магазинов
          </h1>
          <p className="mt-2 max-w-xl text-ink-500">
            Размечайте сразу всю группу одной категорией и кошельком. Сомнительные
            (1–2 операции без известных правил) уходят в «На согласование».
          </p>
        </div>
        <div className="relative w-full sm:w-72">
          <Search
            size={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-400"
          />
          <input
            className="input pl-9"
            placeholder="Поиск магазина…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
      </section>

      <div className="flex flex-wrap gap-4 text-sm text-ink-500">
        <span>
          Групп: <strong className="text-ink-700">{groups.length}</strong>
        </span>
        <span>
          Сумма: <strong className="text-ink-700">{formatMoneyAbs(totalSum)}</strong>
        </span>
      </div>

      {error && (
        <p className="rounded-xl bg-clay-500/10 px-4 py-3 text-sm text-clay-600">
          {error}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-ink-400">Загрузка групп…</p>
      ) : groups.length === 0 ? (
        <div className="panel px-6 py-12 text-center">
          <p className="font-display text-xl text-ink-700">Нечего размечать</p>
          <p className="mt-2 text-sm text-ink-400">
            Загрузите выписку или проверьте раздел «На согласование»
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {groups.map((g) => (
            <GroupCard
              key={g.id}
              group={g}
              categories={catalog.categories}
              wallets={catalog.wallets}
              onLabel={handleLabel}
              onReview={handleReview}
              busyId={busyId}
            />
          ))}
        </div>
      )}
    </div>
  )
}
