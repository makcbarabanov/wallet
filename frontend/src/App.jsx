import { NavLink, Route, Routes } from 'react-router-dom'
import {
  LayoutDashboard,
  Tags,
  Upload,
  BarChart3,
  ClipboardCheck,
  Wallet,
} from 'lucide-react'
import { useCatalog } from './hooks/useCatalog'
import UploadPage from './pages/UploadPage'
import GroupsPage from './pages/GroupsPage'
import ReviewPage from './pages/ReviewPage'
import ReportsPage from './pages/ReportsPage'
import CategoriesPage from './pages/CategoriesPage'

const nav = [
  { to: '/', label: 'Загрузка', icon: Upload, end: true },
  { to: '/groups', label: 'Группы', icon: Tags },
  { to: '/review', label: 'На согласование', icon: ClipboardCheck },
  { to: '/reports', label: 'Отчёты', icon: BarChart3 },
  { to: '/categories', label: 'Категории', icon: LayoutDashboard },
]

export default function App() {
  const catalog = useCatalog()

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-ink-200/70 bg-ink-50/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-pine-600 text-white shadow-soft">
              <Wallet size={18} />
            </div>
            <div>
              <p className="font-display text-lg font-semibold leading-none text-ink-800">
                Wallet
              </p>
              <p className="mt-0.5 text-xs text-ink-400">личное · бизнес · одна карта</p>
            </div>
          </div>

          {catalog.stats && (
            <div className="hidden items-center gap-3 text-xs text-ink-500 sm:flex">
              <span>{catalog.stats.total_operations} операций</span>
              <span className="h-1 w-1 rounded-full bg-ink-300" />
              <span>{catalog.stats.pending_groups} групп к разметке</span>
              {catalog.stats.review_groups > 0 && (
                <>
                  <span className="h-1 w-1 rounded-full bg-ink-300" />
                  <span className="text-clay-500">
                    {catalog.stats.review_groups} на согласование
                  </span>
                </>
              )}
            </div>
          )}
        </div>

        <nav className="mx-auto flex max-w-6xl gap-1 overflow-x-auto px-4 pb-3 sm:px-6">
          {nav.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                [
                  'flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition',
                  isActive
                    ? 'bg-pine-600 text-white shadow-soft'
                    : 'text-ink-500 hover:bg-ink-100 hover:text-ink-700',
                ].join(' ')
              }
            >
              <Icon size={16} />
              {label}
              {to === '/review' && catalog.stats?.review_groups > 0 && (
                <span className="ml-1 rounded-md bg-white/20 px-1.5 text-[10px]">
                  {catalog.stats.review_groups}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <Routes>
          <Route path="/" element={<UploadPage catalog={catalog} />} />
          <Route
            path="/groups"
            element={<GroupsPage catalog={catalog} status="pending" />}
          />
          <Route path="/review" element={<ReviewPage catalog={catalog} />} />
          <Route path="/reports" element={<ReportsPage catalog={catalog} />} />
          <Route path="/categories" element={<CategoriesPage catalog={catalog} />} />
        </Routes>
      </main>
    </div>
  )
}
