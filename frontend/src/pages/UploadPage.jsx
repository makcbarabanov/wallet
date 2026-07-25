import { useNavigate } from 'react-router-dom'
import { useRef, useState } from 'react'
import { FileUp, CheckCircle2, AlertCircle } from 'lucide-react'
import { api } from '../api/client'

export default function UploadPage({ catalog }) {
  const inputRef = useRef(null)
  const navigate = useNavigate()
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  async function handleFile(file) {
    if (!file) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const res = await api.uploadCsv(file)
      setResult(res)
      await catalog.refresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  function onDrop(e) {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files?.[0]
    handleFile(file)
  }

  return (
    <div className="space-y-8">
      <section>
        <h1 className="font-display text-3xl font-semibold text-ink-800 sm:text-4xl">
          Загрузка выписки
        </h1>
        <p className="mt-2 max-w-2xl text-ink-500">
          CSV из Т-Банка с колонками <strong>Дата</strong>, <strong>Магазин</strong>,{' '}
          <strong>Сумма</strong>, <strong>Комментарий</strong>. Операции сгруппируются
          только по магазину — без авторазметки.
        </p>
      </section>

      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={[
          'panel flex flex-col items-center justify-center gap-4 px-6 py-16 text-center transition',
          dragging ? 'border-pine-500 bg-pine-600/5' : '',
        ].join(' ')}
      >
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-pine-600/10 text-pine-700">
          <FileUp size={28} />
        </div>
        <div>
          <p className="font-medium text-ink-700">
            Перетащите CSV сюда или выберите файл
          </p>
          <p className="mt-1 text-sm text-ink-400">
            Повторная загрузка безопасна — дубликаты пропускаются
          </p>
        </div>
        <button
          type="button"
          className="btn-primary"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          {busy ? 'Загрузка…' : 'Выбрать файл'}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.txt,text/csv"
          className="hidden"
          onChange={(e) => handleFile(e.target.files?.[0])}
        />
      </div>

      {error && (
        <div className="flex items-start gap-3 rounded-2xl border border-clay-500/20 bg-clay-500/5 px-4 py-3 text-clay-600">
          <AlertCircle size={18} className="mt-0.5 shrink-0" />
          <p className="text-sm">{error}</p>
        </div>
      )}

      {result && (
        <div className="panel space-y-4 p-6">
          <div className="flex items-center gap-2 text-pine-700">
            <CheckCircle2 size={20} />
            <h2 className="font-display text-xl font-semibold">Выписка загружена</h2>
          </div>
          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ['Импортировано', result.imported],
              ['Дубликатов пропущено', result.skipped_duplicates],
              ['Групп к разметке', result.pending_groups],
              ['На согласование', result.review_groups],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl bg-ink-50 px-4 py-3">
                <dt className="text-xs text-ink-400">{label}</dt>
                <dd className="mt-1 font-display text-2xl font-semibold text-ink-800">
                  {value}
                </dd>
              </div>
            ))}
          </dl>
          <div className="flex flex-wrap gap-2 pt-2">
            <button
              type="button"
              className="btn-primary"
              onClick={() => navigate('/groups')}
            >
              Перейти к группам
            </button>
            {result.review_groups > 0 && (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => navigate('/review')}
              >
                Открыть «На согласование»
              </button>
            )}
          </div>
        </div>
      )}

      {catalog.stats && catalog.stats.total_operations > 0 && !result && (
        <p className="text-sm text-ink-400">
          В базе уже {catalog.stats.total_operations} операций ·{' '}
          {catalog.stats.pending_groups} групп ждут разметки
        </p>
      )}
    </div>
  )
}
