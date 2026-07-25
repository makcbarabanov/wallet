/** Thin fetch wrapper around the FastAPI backend. */

const BASE = import.meta.env.VITE_API_URL || ''

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options)
  if (res.status === 204) return null

  let data = null
  const text = await res.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = { detail: text }
    }
  }

  if (!res.ok) {
    const detail = data?.detail
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
          : `Ошибка ${res.status}`
    throw new Error(message)
  }
  return data
}

export const api = {
  health: () => request('/api/health'),
  stats: () => request('/api/reports/stats'),

  uploadCsv: async (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/api/upload', { method: 'POST', body: form })
  },

  getGroups: (params = {}) => {
    const qs = new URLSearchParams()
    if (params.status) qs.set('status', params.status)
    if (params.q) qs.set('q', params.q)
    const suffix = qs.toString() ? `?${qs}` : ''
    return request(`/api/groups${suffix}`)
  },

  labelGroup: (groupId, { category_id, wallet_id, save_rule = true }) =>
    request(`/api/groups/${groupId}/label`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category_id, wallet_id, save_rule }),
    }),

  sendToReview: (groupId) =>
    request(`/api/groups/${groupId}/review`, { method: 'POST' }),

  getCategories: () => request('/api/categories'),
  createCategory: (body) =>
    request('/api/categories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  getWallets: () => request('/api/wallets'),

  getTransactions: (params = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.set(k, v)
    })
    const suffix = qs.toString() ? `?${qs}` : ''
    return request(`/api/transactions${suffix}`)
  },

  labelTransaction: (id, body) =>
    request(`/api/transactions/${id}/label`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  getReport: (params = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
    })
    const suffix = qs.toString() ? `?${qs}` : ''
    return request(`/api/reports/summary${suffix}`)
  },

  getPlanFact: (period, wallet_id) => {
    const qs = new URLSearchParams({ period })
    if (wallet_id) qs.set('wallet_id', wallet_id)
    return request(`/api/reports/plan-fact?${qs}`)
  },

  getBudgets: (period) => {
    const qs = period ? `?period=${period}` : ''
    return request(`/api/budgets${qs}`)
  },

  createBudget: (body) =>
    request('/api/budgets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  deleteBudget: (id) => request(`/api/budgets/${id}`, { method: 'DELETE' }),

  getRules: () => request('/api/rules'),
  deleteRule: (id) => request(`/api/rules/${id}`, { method: 'DELETE' }),
}
