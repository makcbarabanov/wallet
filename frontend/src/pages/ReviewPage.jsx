import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import GroupCard from '../components/GroupCard'

/**
 * «На согласование» — groups with 1–2 unknown-store operations
 * (or manually parked). Same labeling UX as the main groups page.
 */
export default function ReviewPage({ catalog }) {
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [busyId, setBusyId] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setGroups(await api.getGroups({ status: 'review' }))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

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

  return (
    <div className="space-y-6">
      <section>
        <h1 className="font-display text-3xl font-semibold text-ink-800">
          На согласование
        </h1>
        <p className="mt-2 max-w-2xl text-ink-500">
          Редкие магазины (1–2 операции) без известных правил. Просмотрите и
          разметьте вручную — решение сохранится как локальное правило.
        </p>
      </section>

      {error && (
        <p className="rounded-xl bg-clay-500/10 px-4 py-3 text-sm text-clay-600">
          {error}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-ink-400">Загрузка…</p>
      ) : groups.length === 0 ? (
        <div className="panel px-6 py-12 text-center">
          <p className="font-display text-xl text-ink-700">Очередь пуста</p>
          <p className="mt-2 text-sm text-ink-400">
            Все сомнительные группы уже размечены
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
              busyId={busyId}
            />
          ))}
        </div>
      )}
    </div>
  )
}
