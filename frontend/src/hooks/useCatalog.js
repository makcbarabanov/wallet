import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'

/**
 * Shared catalog + stats used across pages.
 * Refetch after labeling so reports stay in sync.
 */
export function useCatalog() {
  const [categories, setCategories] = useState([])
  const [wallets, setWallets] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [cats, wals, st] = await Promise.all([
        api.getCategories(),
        api.getWallets(),
        api.stats(),
      ])
      setCategories(cats)
      setWallets(wals)
      setStats(st)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { categories, wallets, stats, loading, error, refresh, setCategories }
}
