'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { dealsHomePath } from '../../lib/leadsPaths';
import api from '../../services/api';
import { useNotification } from '../../contexts/NotificationContext';
import { useAuth } from '../../contexts/AuthContext';
import { useT } from '../../contexts/LocaleContext';
import Skeleton from '../shared/Skeleton';
import { Plus, TrendingUp, Trophy, Upload, Undo2 } from 'lucide-react';
import CsvImportModal, { useImportUndo } from '../shared/CsvImportModal';

function formatMoney(value) {
  const n = Number(value);
  if (Number.isNaN(n)) return value ?? '—';
  return n.toLocaleString('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 });
}

function roleOf(user) {
  return String(user?.role || '').toLowerCase();
}

const ALLOWED_REQUIRED_FIELDS = ['title', 'amount', 'expected_close', 'client_id', 'probability'];

function parseMoveErrorDetail(detail, fallback = 'Request failed') {
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const msgs = detail.map((e) => e?.msg).filter(Boolean);
    return msgs.length ? msgs.join('; ') : fallback;
  }
  if (typeof detail === 'object' && detail.message) {
    const missing = detail.missing_fields;
    if (missing?.length) return `${detail.message}: ${missing.join(', ')}`;
    return detail.message;
  }
  return fallback;
}

export default function DealsBoard() {
  const [board, setBoard] = useState(null);
  const [stages, setStages] = useState([]);
  const [pipelines, setPipelines] = useState([]);
  const [pipelineId, setPipelineId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [creating, setCreating] = useState(false);
  const [movingId, setMovingId] = useState(null);
  const [togglingBlueprint, setTogglingBlueprint] = useState(false);
  const [view, setView] = useState('');
  const [savedFilters, setSavedFilters] = useState([]);
  const [savingFilter, setSavingFilter] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  const basePath = dealsHomePath(usePathname());
  const { showToast } = useNotification();
  const { user } = useAuth();
  const t = useT();
  const canConfigure = ['admin', 'md'].includes(roleOf(user));

  useEffect(() => {
    fetchPipelines();
    api.get('/saved-filters')
      .then((res) => setSavedFilters(res.data?.items ?? []))
      .catch(() => setSavedFilters([]));
  }, []);

  useEffect(() => {
    if (pipelineId == null) return;
    fetchBoard(pipelineId, view);
  }, [pipelineId, view]);

  const fetchPipelines = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get('/deals/pipelines');
      const items = res.data.items || [];
      setPipelines(items);
      const current = items.find((p) => p.is_default) || items[0];
      setPipelineId(current ? current.id : null);
      if (!current) setLoading(false);
    } catch (err) {
      console.error('Failed to fetch pipelines', err);
      setError('Unable to load deals. Please try again.');
      setLoading(false);
    }
  };

  const fetchBoard = async (pid, boardView = view) => {
    setLoading(true);
    setError(null);
    try {
      const params = { pipeline_id: pid };
      if (boardView) params.view = boardView;
      const [boardRes, stagesRes] = await Promise.all([
        api.get('/deals/board', { params }),
        api.get('/deals/stages', { params: { pipeline_id: pid } }),
      ]);
      setBoard(boardRes.data);
      const rawStages = stagesRes.data?.items ?? stagesRes.data;
      setStages(Array.isArray(rawStages) ? rawStages : []);
    } catch (err) {
      console.error('Failed to fetch deals board', err);
      setError('Unable to load deals. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const refreshBoard = () => {
    if (pipelineId != null) fetchBoard(pipelineId, view);
  };

  const { canUndo, undo, undoing, refreshBatch } = useImportUndo('deal', refreshBoard);

  const handleCreateDeal = async () => {
    const title = window.prompt('Deal title:');
    if (!title) return;
    const amountStr = window.prompt('Amount:', '0');
    if (amountStr === null) return;
    const amount = Number(amountStr);
    if (Number.isNaN(amount)) {
      showToast('Amount must be a number', 'error');
      return;
    }
    setCreating(true);
    try {
      const payload = { title, amount };
      if (pipelineId != null) payload.pipeline_id = pipelineId;
      await api.post('/deals', payload);
      showToast('Deal created', 'success');
      await fetchBoard(pipelineId, view);
    } catch (err) {
      console.error('Failed to create deal', err);
      showToast(err.response?.data?.detail || 'Failed to create deal', 'error');
    } finally {
      setCreating(false);
    }
  };

  const handleSaveFilter = async () => {
    const name = window.prompt('Name this view:');
    if (!name || !name.trim()) return;
    setSavingFilter(true);
    try {
      const filters = {};
      if (view) filters.view = view;
      if (pipelineId != null) filters.pipeline_id = pipelineId;
      const res = await api.post('/saved-filters', {
        name: name.trim(),
        object_type: 'deal',
        filters,
      });
      setSavedFilters((prev) => [...prev, res.data].sort((a, b) => a.name.localeCompare(b.name)));
      showToast('View saved', 'success');
    } catch (err) {
      showToast(err.response?.data?.detail || 'Could not save view', 'error');
    } finally {
      setSavingFilter(false);
    }
  };

  const applySavedFilter = (id) => {
    const row = savedFilters.find((f) => String(f.id) === String(id));
    if (!row) return;
    const filters = row.filters || {};
    setView(filters.view || '');
    if (filters.pipeline_id) setPipelineId(filters.pipeline_id);
  };

  const handleNewPipeline = async () => {
    const name = window.prompt('Pipeline name:');
    if (!name) return;
    try {
      const res = await api.post('/deals/pipelines', { name });
      showToast('Pipeline created', 'success');
      const itemsRes = await api.get('/deals/pipelines');
      const items = itemsRes.data.items || [];
      setPipelines(items);
      setPipelineId(res.data.id);
    } catch (err) {
      showToast(err.response?.data?.detail || 'Failed to create pipeline', 'error');
    }
  };

  const handleBlueprintToggle = async (enabled) => {
    if (pipelineId == null) return;
    setTogglingBlueprint(true);
    try {
      await api.patch(`/deals/pipelines/${pipelineId}`, { blueprint_enabled: enabled });
      const itemsRes = await api.get('/deals/pipelines');
      setPipelines(itemsRes.data.items || []);
      await fetchBoard(pipelineId, view);
    } catch (err) {
      console.error('Failed to toggle blueprint', err);
      showToast(parseMoveErrorDetail(err.response?.data?.detail, 'Failed to update blueprint'), 'error');
    } finally {
      setTogglingBlueprint(false);
    }
  };

  const handleRequiredFieldsChange = async (stageId, keys) => {
    try {
      await api.patch(`/deals/stages/${stageId}`, { required_fields: keys });
      await fetchBoard(pipelineId, view);
    } catch (err) {
      console.error('Failed to update required fields', err);
      showToast(parseMoveErrorDetail(err.response?.data?.detail, 'Failed to update required fields'), 'error');
    }
  };

  const handleMoveStage = async (dealId, newStageId) => {
    setMovingId(dealId);
    try {
      await api.patch(`/deals/${dealId}/stage`, { stage_id: newStageId });
      await fetchBoard(pipelineId, view);
    } catch (err) {
      console.error('Failed to move deal', err);
      showToast(parseMoveErrorDetail(err.response?.data?.detail), 'error');
      await fetchBoard(pipelineId, view);
    } finally {
      setMovingId(null);
    }
  };

  const totalDeals = (board?.stages || []).reduce((sum, s) => sum + (s.deals?.length || 0), 0);
  const selected = pipelines.find((p) => p.id === pipelineId);

  if (loading && !board) {
    return (
      <div className="min-h-[calc(100vh-56px)] bg-page">
        <div className="bg-surface border-b border-border px-6 py-4">
          <div className="max-w-[1400px] mx-auto flex items-center justify-between">
            <div className="space-y-2">
              <Skeleton className="h-6 w-48" />
              <Skeleton className="h-4 w-32 opacity-60" />
            </div>
          </div>
        </div>
        <div className="max-w-[1400px] mx-auto px-6 py-8 flex gap-4 overflow-x-auto">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="w-72 shrink-0 space-y-3">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-56px)] bg-page">
        <div className="flex flex-col items-center gap-3">
          <div className="text-[13px] text-error font-bold uppercase tracking-widest">{error}</div>
          <button onClick={() => (pipelineId != null ? fetchBoard(pipelineId, view) : fetchPipelines())} className="px-4 py-2 bg-accent hover:bg-accent-hover text-white rounded-md text-[11px] font-black uppercase tracking-tight">Retry</button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-[calc(100vh-56px)] bg-page flex flex-col">
      <div className="bg-surface border-b border-border px-6 py-4">
        <div className="max-w-[1400px] mx-auto flex items-center justify-between gap-4">
          <div>
                            <h1 className="page-title">{t('Deals')}</h1>
            <p className="page-subtitle">
              {selected?.name || board?.pipeline_name || 'Opportunities by stage'}
            </p>
          </div>
          <div className="flex items-center gap-3 flex-wrap justify-end">
            <div className="flex rounded-md border border-border overflow-hidden" role="group" aria-label="Deal view">
              {[
                { id: '', label: 'All' },
                { id: 'due_today', label: 'Due today' },
                { id: 'rotting', label: 'Rotting' },
              ].map((opt) => (
                <button
                  key={opt.id || 'all'}
                  type="button"
                  onClick={() => setView(opt.id)}
                  className={`px-2.5 py-1.5 text-[13px] font-medium ${
                    view === opt.id ? 'bg-primary text-surface' : 'bg-surface text-secondary hover:bg-surface-elevated'
                  }`}
                >
                  {t(opt.label)}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2">
              <span className="sr-only">Saved views</span>
              <select
                value=""
                onChange={(e) => {
                  if (e.target.value) applySavedFilter(e.target.value);
                  e.target.value = '';
                }}
                className="text-[11px] font-bold uppercase tracking-tight bg-surface border border-border rounded px-2 py-1.5 text-primary focus:outline-none focus:border-accent"
              >
                <option value="">{t('Saved views')}</option>
                {savedFilters.map((f) => (
                  <option key={f.id} value={f.id}>{f.name}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={handleSaveFilter}
              disabled={savingFilter}
              className="px-2.5 py-1.5 border border-border rounded-md text-[11px] font-bold uppercase tracking-tight text-primary disabled:opacity-50"
            >
              {t('Save view')}
            </button>
            <label className="flex items-center gap-2">
              <span className="sr-only">Pipeline</span>
              <select
                value={pipelineId ?? ''}
                onChange={(e) => setPipelineId(Number(e.target.value))}
                className="text-[11px] font-bold uppercase tracking-tight bg-surface border border-border rounded px-2 py-1.5 text-primary focus:outline-none focus:border-accent"
              >
                {pipelines.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}{p.is_default ? ' (default)' : ''}
                  </option>
                ))}
              </select>
            </label>
            {canConfigure && pipelineId != null && selected && (
              <label className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-tight text-primary">
                <input
                  type="checkbox"
                  checked={Boolean(selected.blueprint_enabled)}
                  onChange={(e) => handleBlueprintToggle(e.target.checked)}
                  disabled={togglingBlueprint}
                  className="rounded border-border"
                />
                {t('Enforce blueprint')}
              </label>
            )}
            {canConfigure && (
              <button
                type="button"
                onClick={handleNewPipeline}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-border rounded-md text-[12px] font-bold uppercase tracking-tight text-primary hover:border-accent"
              >
                {t('New pipeline')}
              </button>
            )}
            <div className="hidden sm:flex items-center gap-4 px-4 py-1.5 bg-surface-elevated/50 border border-border rounded-md shadow-inner">
              <div className="flex items-center gap-1.5">
                <TrendingUp size={14} strokeWidth={2.5} className="text-accent" />
                <span className="text-[10px] font-black text-muted uppercase tracking-widest">{t('Open Forecast')}</span>
                <span className="text-[12px] font-bold text-primary tabular-nums">{formatMoney(board?.open_forecast)}</span>
              </div>
              <div className="w-px h-4 bg-border" />
              <div className="flex items-center gap-1.5">
                <Trophy size={14} strokeWidth={2.5} className="text-success" />
                <span className="text-[10px] font-black text-muted uppercase tracking-widest">{t('Won')}</span>
                <span className="text-[12px] font-bold text-primary tabular-nums">{formatMoney(board?.won_value)}</span>
              </div>
            </div>
            {canUndo && (
              <button
                type="button"
                onClick={undo}
                disabled={undoing}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-border rounded-md text-[11px] font-bold uppercase tracking-tight text-primary hover:bg-surface-elevated disabled:opacity-50"
              >
                <Undo2 size={14} strokeWidth={2.5} /> {t('Undo last import')}
              </button>
            )}
            <button
              type="button"
              onClick={() => setImportOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-border rounded-md text-[11px] font-bold uppercase tracking-tight text-primary hover:bg-surface-elevated"
            >
              <Upload size={14} strokeWidth={2.5} /> {t('Import CSV')}
            </button>
            <button
              onClick={handleCreateDeal}
              disabled={creating}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-accent hover:bg-accent-hover text-white rounded-md text-[12px] font-bold uppercase tracking-tight transition-all shadow-sm shadow-accent/10 disabled:opacity-50"
            >
              <Plus size={14} strokeWidth={2.5} /> {t('New Deal')}
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-[1400px] mx-auto px-6 py-8 flex-1 w-full">
        {totalDeals === 0 && (
          <p className="text-[13px] text-muted font-medium italic mb-4">
            {view === 'due_today'
              ? t('No deals due today.')
              : view === 'rotting'
                ? t('No rotting deals.')
                : t('No deals in this pipeline yet.')}
          </p>
        )}
        <div className="flex gap-4 overflow-x-auto pb-4">
          {(board?.stages || []).map((stage) => (
            <div key={stage.stage_id} className="w-72 shrink-0 flex flex-col bg-surface rounded border border-border shadow-sm">
              <div className="px-4 py-3 border-b border-border bg-surface-elevated/50">
                <div className="flex items-center justify-between">
                  <h2 className="text-[12px] font-black text-primary uppercase tracking-tight truncate">{stage.name}</h2>
                  <span className="text-[10px] font-bold text-muted bg-surface px-1.5 py-0.5 rounded border border-border">{stage.deals?.length || 0}</span>
                </div>
                <div className="mt-1 flex items-center justify-between text-[10px] font-bold text-muted uppercase tracking-wide">
                  <span>Total {formatMoney(stage.stage_total)}</span>
                  <span>Wtd {formatMoney(stage.weighted_value)}</span>
                </div>
                {stage.required_fields?.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {stage.required_fields.map((field) => (
                      <span
                        key={field}
                        className="text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 bg-accent/10 text-accent border border-accent/20 rounded"
                      >
                        {field}
                      </span>
                    ))}
                  </div>
                )}
                {canConfigure && selected?.blueprint_enabled && (
                  <label className="mt-2 block">
                    <span className="text-[9px] font-bold text-muted uppercase tracking-wide">Required to leave</span>
                    <select
                      multiple
                      value={stage.required_fields || []}
                      onChange={(e) => {
                        const keys = Array.from(e.target.selectedOptions, (o) => o.value);
                        handleRequiredFieldsChange(stage.stage_id, keys);
                      }}
                      className="mt-1 w-full text-[10px] font-bold uppercase tracking-tight bg-surface border border-border rounded px-1.5 py-1 text-primary focus:outline-none focus:border-accent"
                      size={3}
                    >
                      {ALLOWED_REQUIRED_FIELDS.map((field) => (
                        <option key={field} value={field}>{field}</option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
              <div className="p-2 space-y-2 flex-1 min-h-[80px]">
                {(stage.deals || []).length === 0 ? (
                  <p className="text-[11px] text-muted italic text-center py-4">No deals in this stage</p>
                ) : (
                  stage.deals.map((deal) => (
                    <div key={deal.id} className="bg-surface-elevated/40 border border-border rounded-md p-3 hover:border-accent/40 transition-colors">
                      <Link href={`${basePath}/${deal.id}`} className="block">
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-[12px] font-bold text-primary truncate">{deal.title}</p>
                          {deal.missing_next_activity && (
                            <span className="shrink-0 text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30 rounded">
                              {t('No next step')}
                            </span>
                          )}
                        </div>
                        <p className="text-[12px] font-bold text-accent tabular-nums mt-1">{formatMoney(deal.amount)}</p>
                        <p className="text-[10px] text-muted font-medium uppercase tracking-wide mt-0.5">
                          {deal.effective_probability != null ? `${deal.effective_probability}% probability` : ''}
                        </p>
                      </Link>
                      <select
                        value={stage.stage_id}
                        disabled={movingId === deal.id}
                        onChange={(e) => handleMoveStage(deal.id, Number(e.target.value))}
                        className="mt-2 w-full text-[10px] font-bold uppercase tracking-tight bg-surface border border-border rounded px-1.5 py-1 text-muted focus:outline-none focus:border-accent disabled:opacity-50"
                      >
                        {stages.map((s) => (
                          <option key={s.id} value={s.id}>{s.name}</option>
                        ))}
                      </select>
                    </div>
                  ))
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
      <CsvImportModal
        entity="deals"
        isOpen={importOpen}
        onClose={() => setImportOpen(false)}
        onRefresh={() => { refreshBoard(); refreshBatch(); }}
      />
    </div>
  );
}
