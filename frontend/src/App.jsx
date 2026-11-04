import { useState, useEffect, useCallback, useRef } from 'react'

const AUTO_RUN_MS = 3 * 60 * 60 * 1000 // 3 hours

const ATS_META = {
  ashby:      { label: 'Ashby',      badge: 'bg-purple-100 text-purple-700', tab: 'border-purple-500 text-purple-600' },
  lever:      { label: 'Lever',      badge: 'bg-blue-100 text-blue-700',     tab: 'border-blue-500 text-blue-600' },
  greenhouse: { label: 'Greenhouse', badge: 'bg-green-100 text-green-700',   tab: 'border-green-500 text-green-600' },
}

function formatRunTime(isoString) {
  const d = new Date(isoString)
  const today = new Date()
  const isToday = d.toDateString() === today.toDateString()
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return isToday ? `Today · ${time}` : `Yesterday · ${time}`
}

function formatCountdown(ms) {
  if (ms <= 0) return '0:00:00'
  const h = Math.floor(ms / 3_600_000)
  const m = Math.floor((ms % 3_600_000) / 60_000)
  const s = Math.floor((ms % 60_000) / 1_000)
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function Spinner({ size = 4 }) {
  return (
    <svg
      className={`animate-spin h-${size} w-${size} text-current`}
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  )
}

export default function App() {
  const [runs, setRuns] = useState([])
  const [selectedRunId, setSelectedRunId] = useState(null)
  const [jobs, setJobs] = useState([])
  const [jobsLoading, setJobsLoading] = useState(false)
  const [isRunning, setIsRunning] = useState(false)
  const [atsFilter, setAtsFilter] = useState('all')
  const [countdown, setCountdown] = useState(AUTO_RUN_MS)
  const [error, setError] = useState(null)

  const nextRunAt = useRef(Date.now() + AUTO_RUN_MS)
  // prevent the auto-run effect from capturing a stale triggerRun
  const triggerRunRef = useRef(null)

  // ── data fetchers ────────────────────────────────────────────────────────

  const fetchRuns = useCallback(async () => {
    const res = await fetch('/api/runs')
    if (!res.ok) throw new Error('Failed to load runs')
    return res.json()
  }, [])

  const fetchJobs = useCallback(async (runId) => {
    setJobsLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/runs/${runId}/jobs`)
      if (!res.ok) throw new Error('Failed to load jobs')
      const data = await res.json()
      setJobs(data.jobs ?? [])
    } catch (e) {
      setError(e.message)
    } finally {
      setJobsLoading(false)
    }
  }, [])

  // ── poll until a run finishes, then refresh everything ──────────────────

  const pollUntilDone = useCallback(async (runId) => {
    for (;;) {
      await new Promise(r => setTimeout(r, 2_000))
      const res = await fetch(`/api/runs/${runId}/jobs`)
      const data = await res.json()
      if (!data.in_progress) {
        const freshRuns = await fetchRuns()
        setRuns(freshRuns)
        setJobs(data.jobs ?? [])
        setSelectedRunId(runId)
        setAtsFilter('all')
        setIsRunning(false)
        return
      }
    }
  }, [fetchRuns])

  // ── trigger a crawl run ──────────────────────────────────────────────────

  const triggerRun = useCallback(async () => {
    if (isRunning) return
    setIsRunning(true)
    setError(null)
    try {
      const res = await fetch('/api/run', { method: 'POST' })
      const data = await res.json()
      // Reset the countdown regardless of whether it was already running
      nextRunAt.current = Date.now() + AUTO_RUN_MS
      setCountdown(AUTO_RUN_MS)
      await pollUntilDone(data.run_id)
    } catch (e) {
      setError('Run failed: ' + e.message)
      setIsRunning(false)
    }
  }, [isRunning, pollUntilDone])

  // keep ref in sync so the ticker can call the latest version
  triggerRunRef.current = triggerRun

  // ── initial load ─────────────────────────────────────────────────────────

  useEffect(() => {
    ;(async () => {
      try {
        const runsData = await fetchRuns()
        setRuns(runsData)
        const first = runsData.find(r => !r.in_progress)
        if (first) {
          setSelectedRunId(first.id)
          await fetchJobs(first.id)
        }
        // if a run was in progress when we opened, poll it
        const inProg = runsData.find(r => r.in_progress)
        if (inProg) {
          setIsRunning(true)
          pollUntilDone(inProg.id)
        }
      } catch (e) {
        setError(e.message)
      }
    })()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── countdown + auto-run ─────────────────────────────────────────────────

  useEffect(() => {
    const id = setInterval(() => {
      const remaining = nextRunAt.current - Date.now()
      if (remaining <= 0) {
        nextRunAt.current = Date.now() + AUTO_RUN_MS
        triggerRunRef.current?.()
      } else {
        setCountdown(remaining)
      }
    }, 1_000)
    return () => clearInterval(id)
  }, [])

  // ── derived data ─────────────────────────────────────────────────────────

  const filteredJobs = atsFilter === 'all' ? jobs : jobs.filter(j => j.ats === atsFilter)
  const selectedRun  = runs.find(r => r.id === selectedRunId)

  const countFor = (ats) => selectedRun?.[`${ats}_new`] ?? 0

  // ── handlers ─────────────────────────────────────────────────────────────

  const handleSelectRun = useCallback(async (run) => {
    if (run.in_progress) return
    setSelectedRunId(run.id)
    setAtsFilter('all')
    await fetchJobs(run.id)
  }, [fetchJobs])

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-screen bg-gray-50 text-gray-900">

      {/* ── Header ── */}
      <header className="flex items-center justify-between px-6 py-3 bg-white border-b border-gray-200 shrink-0">
        <h1 className="text-base font-semibold tracking-tight">Job Crawler</h1>
        <div className="flex items-center gap-4">
          {error && (
            <span className="text-sm text-red-500">{error}</span>
          )}
          <span className="text-sm text-gray-400">
            Next run in{' '}
            <span className="font-mono text-gray-600">{formatCountdown(countdown)}</span>
          </span>
          <button
            onClick={triggerRun}
            disabled={isRunning}
            className="flex items-center gap-2 px-4 py-1.5 bg-blue-600 text-white text-sm
                       font-medium rounded-md hover:bg-blue-700 transition-colors
                       disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isRunning && <Spinner />}
            {isRunning ? 'Running…' : 'Run Now'}
          </button>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">

        {/* ── Left panel: run history ── */}
        <aside className="w-60 shrink-0 bg-white border-r border-gray-200 flex flex-col overflow-hidden">
          <div className="px-4 py-2.5 border-b border-gray-100">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Past 24 hours</p>
          </div>
          <ul className="flex-1 overflow-y-auto">
            {runs.length === 0 && !isRunning && (
              <li className="px-4 py-6 text-sm text-gray-400 text-center">No runs yet.</li>
            )}
            {runs.map(run => {
              const isSelected = run.id === selectedRunId
              return (
                <li key={run.id}>
                  <button
                    onClick={() => handleSelectRun(run)}
                    disabled={run.in_progress}
                    className={[
                      'w-full text-left px-4 py-3 border-b border-gray-100 transition-colors',
                      run.in_progress
                        ? 'cursor-default opacity-60'
                        : 'hover:bg-gray-50 cursor-pointer',
                      isSelected
                        ? 'bg-blue-50 border-l-[3px] border-l-blue-500 pl-[13px]'
                        : '',
                    ].join(' ')}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-gray-700 truncate">
                        {formatRunTime(run.run_at)}
                      </span>
                      {run.in_progress
                        ? <span className="text-xs text-orange-500 font-medium shrink-0">Running</span>
                        : <span className="text-sm font-semibold text-gray-800 shrink-0">{run.total_new}</span>
                      }
                    </div>
                    {!run.in_progress && (
                      <div className="flex gap-2.5 text-xs text-gray-400 flex-wrap">
                        {run.ashby_new > 0      && <span>A·{run.ashby_new}</span>}
                        {run.lever_new > 0      && <span>L·{run.lever_new}</span>}
                        {run.greenhouse_new > 0 && <span>G·{run.greenhouse_new}</span>}
                        {run.total_new === 0    && <span>No new jobs</span>}
                      </div>
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        </aside>

        {/* ── Right panel: jobs ── */}
        <main className="flex-1 flex flex-col overflow-hidden">

          {/* ATS filter tabs */}
          <div className="flex items-center gap-0 bg-white border-b border-gray-200 px-4 shrink-0">
            {['all', 'ashby', 'lever', 'greenhouse'].map(tab => {
              const count = tab === 'all' ? (selectedRun?.total_new ?? 0) : countFor(tab)
              const isActive = atsFilter === tab
              const meta = ATS_META[tab]
              return (
                <button
                  key={tab}
                  onClick={() => setAtsFilter(tab)}
                  className={[
                    'px-4 py-3 text-sm font-medium border-b-2 transition-colors',
                    isActive
                      ? (meta ? meta.tab : 'border-blue-500 text-blue-600')
                      : 'border-transparent text-gray-500 hover:text-gray-700',
                  ].join(' ')}
                >
                  {tab === 'all' ? 'All' : meta.label}
                  {selectedRun && !selectedRun.in_progress && (
                    <span className="ml-1.5 text-xs text-gray-400">({count})</span>
                  )}
                </button>
              )
            })}
          </div>

          {/* Job list */}
          <div className="flex-1 overflow-y-auto p-5">
            {!selectedRunId && !isRunning ? (
              <EmptyState title="No run selected" sub='Click "Run Now" to fetch new jobs' />
            ) : isRunning && !selectedRunId ? (
              <EmptyState title="Running crawl…" sub="Jobs will appear when complete" loading />
            ) : jobsLoading ? (
              <EmptyState title="Loading…" loading />
            ) : filteredJobs.length === 0 ? (
              <EmptyState
                title="No new jobs"
                sub="No new matching roles were found in this run"
              />
            ) : (
              <div className="grid gap-2.5 max-w-3xl">
                {filteredJobs.map((job, i) => (
                  <JobCard key={i} job={job} />
                ))}
              </div>
            )}
          </div>

        </main>
      </div>
    </div>
  )
}

function JobCard({ job }) {
  const meta = ATS_META[job.ats] ?? { label: job.ats, badge: 'bg-gray-100 text-gray-600' }
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 hover:border-gray-300 transition-colors">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${meta.badge}`}>
              {meta.label}
            </span>
            <span className="text-sm text-gray-500 truncate">{job.company}</span>
          </div>
          <p className="text-sm font-semibold text-gray-900 leading-snug">{job.title}</p>
          {job.location && (
            <p className="text-xs text-gray-500 mt-0.5">{job.location}</p>
          )}
        </div>
        {job.url ? (
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 px-3 py-1.5 text-xs font-medium text-blue-600
                       border border-blue-200 rounded-md hover:bg-blue-50 transition-colors"
          >
            Apply →
          </a>
        ) : null}
      </div>
    </div>
  )
}

function EmptyState({ title, sub, loading = false }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center py-24 text-gray-400">
      {loading && <Spinner size={6} />}
      <p className={`text-base font-medium mt-3 ${loading ? 'text-gray-500' : ''}`}>{title}</p>
      {sub && <p className="text-sm mt-1">{sub}</p>}
    </div>
  )
}
