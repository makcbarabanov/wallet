/** Formatting helpers for RUB amounts and dates. */

const rubFormatter = new Intl.NumberFormat('ru-RU', {
  style: 'currency',
  currency: 'RUB',
  maximumFractionDigits: 0,
})

const rubPrecise = new Intl.NumberFormat('ru-RU', {
  style: 'currency',
  currency: 'RUB',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

export function formatMoney(value, { precise = false } = {}) {
  const n = Number(value) || 0
  return (precise ? rubPrecise : rubFormatter).format(n)
}

export function formatMoneyAbs(value, { precise = false } = {}) {
  return formatMoney(Math.abs(Number(value) || 0), { precise })
}

export function formatDate(iso) {
  if (!iso) return '—'
  const [y, m, d] = iso.split('-')
  return `${d}.${m}.${y}`
}

export function currentPeriod() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

export function statusLabel(status) {
  switch (status) {
    case 'pending':
      return 'К разметке'
    case 'review':
      return 'На согласование'
    case 'labeled':
      return 'Размечено'
    default:
      return status
  }
}
