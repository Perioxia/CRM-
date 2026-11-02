'use client';

import { useState, useEffect, useRef } from 'react';
import { useSearchParams, usePathname } from 'next/navigation';
import Link from 'next/link';
import { formatDistanceToNow, parseISO, differenceInDays } from 'date-fns';
import { motion, AnimatePresence } from 'framer-motion';
import api from '../../services/api';
import { normalizeLeadStatus } from '../../lib/leadStatus';
import { leadsHomePath, unassignedLeadsPath } from '../../lib/leadsPaths';
import { announceLeadsChanged, useLiveRefresh } from '../../lib/useLiveRefresh';
import LeadModal from './LeadModal';
import LeadImportModal from './LeadImportModal';
import { useImportUndo } from '../shared/CsvImportModal';
import { useNotification } from '../../contexts/NotificationContext';
import { useAuth } from '../../contexts/AuthContext';
import { useT } from '../../contexts/LocaleContext';
import Skeleton, { TableRowSkeleton } from '../shared/Skeleton';
import {
  Plus, ChevronRight, Filter, Briefcase, Upload, Undo2
} from 'lucide-react';

const ASSIGNMENT_TABS = [
  { id: 'claimed', label: 'Claimed' },
  { id: 'open', label: 'Open' },
  { id: 'all', label: 'All Leads' },
];

const PAGE_SIZE = 50;

const STATUS_STYLES = {
  'Active': 'badge-info',
  'Converted': 'badge-success',
  'Lost': 'badge-neutral'
};

export default function Leads() {
  const [activeTab, setActiveTab] = useState('claimed');
  const [leads, setLeads] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [searchText, setSearchText] = useState('');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isImportOpen, setIsImportOpen] = useState(false);
  const [error, setError] = useState(null);
  const [now, setNow] = useState(null);
  const latestRequest = useRef(0);
  
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const basePath = leadsHomePath(pathname);
  const { showToast } = useNotification();
  const { user } = useAuth();
  const t = useT();
  const [formMeta, setFormMeta] = useState(null);
  const [copied, setCopied] = useState(false);
  const [copiedWidget, setCopiedWidget] = useState(false);

  useEffect(() => {
    setNow(new Date());
    if (searchParams.get('action') === 'new') {
      setIsModalOpen(true);
    }
  }, [searchParams]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchText.trim());
      setPage(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchText]);

  useEffect(() => {
    fetchLeads();
  }, [activeTab, page, search, user?.id, user?.role]);

  useEffect(() => {
    api.get('/lead-forms')
      .then((res) => setFormMeta(res.data))
      .catch(() => setFormMeta(null));
  }, []);

  useEffect(() => {
    const role = user?.role;
    if (role !== 'admin' && role !== 'md') return undefined;
    let cancelled = false;
    (async () => {
      try {
        const status = await api.get('/import/sheets');
        if (!status.data?.url) return;
        const last = status.data?.last_synced_at;
        const stale = !last || (Date.now() - new Date(last).getTime()) > 2 * 60 * 1000;
        if (!stale) return;
        const synced = await api.post('/import/sheets/sync');
        if (!cancelled && (synced.data?.created || 0) > 0) fetchLeads();
      } catch {
        // A private sheet or a missing link does not block the leads list.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user?.id, user?.role]);

  const fetchLeads = async ({ silent = false } = {}) => {
    const request = ++latestRequest.current;
    if (!silent) {
      setLoading(true);
      setError(null);
    }
    try {
      const response = await api.get('/leads', {
        params: {
          limit: PAGE_SIZE,
          skip: page * PAGE_SIZE,
          ...(activeTab === 'claimed' ? { claimed: true } : {}),
          ...(activeTab === 'open' ? { unassigned: true } : {}),
          ...(search ? { search } : {}),
        },
      });
      if (request !== latestRequest.current) return;
      const data = (response.data?.items ?? []).map((l) => ({ ...l, status: normalizeLeadStatus(l.status) }));
      const count = Number(response.data?.total) || 0;
      if (data.length === 0 && page > 0) {
        setPage(Math.max(0, Math.ceil(count / PAGE_SIZE) - 1));
        return;
      }
      setLeads(data);
      setTotal(count);
    } catch (err) {
      console.error("Failed to fetch leads", err);
      // A failed background refresh keeps the list on screen; the next one will try again.
      if (!silent) setError('Unable to load leads. Please try again.');
    } finally {
      if (request === latestRequest.current) setLoading(false);
    }
  };

  useLiveRefresh(() => fetchLeads({ silent: true }));

  const handleLeadCreated = (lead) => {
    const isOpen = lead?.assigned_to_id == null;
    setActiveTab((tab) => {
      if (tab === 'all') return tab;
      return isOpen ? 'open' : 'claimed';
    });
    setPage(0);
    showToast(t(isOpen ? 'Lead created. It is open for your team to claim.' : 'Lead created'), 'success');
    announceLeadsChanged();
  };

  const { canUndo, undo, undoing, refreshBatch } = useImportUndo('lead', fetchLeads);

  const formUrl = formMeta?.public_path
    ? `${typeof window !== 'undefined' ? window.location.origin : ''}${formMeta.public_path}`
    : '';
  const canEditForm = user?.role === 'admin' || user?.role === 'md';

  const copyFormLink = async () => {
    if (!formUrl) return;
    try {
      await navigator.clipboard.writeText(formUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      showToast('Could not copy link', 'error');
    }
  };

  const copyWidgetSnippet = async () => {
    if (!formMeta?.embed_script_path) return;
    const origin = typeof window !== 'undefined' ? window.location.origin : '';
    const snippet = `<script src="${origin}${formMeta.embed_script_path}" async></script>`;
    try {
      await navigator.clipboard.writeText(snippet);
      setCopiedWidget(true);
      setTimeout(() => setCopiedWidget(false), 1500);
    } catch {
      showToast('Could not copy widget snippet', 'error');
    }
  };

  const onDefaultTeamChange = async (e) => {
    const value = e.target.value ? Number(e.target.value) : null;
    try {
      const res = await api.patch('/lead-forms', { default_team_id: value });
      setFormMeta(res.data);
    } catch {
      showToast('Could not update form team', 'error');
    }
  };

  const filteredLeads = leads;

  const safeParseISO = (s) => {
    if (!s || typeof s !== 'string') return null;
    try {
      const d = parseISO(s);
      return isNaN(d.getTime()) ? null : d;
    } catch {
      return null;
    }
  };

  const getEngagementSignal = (lead) => {
    if (!now) return { text: '', color: '' };
    const nextTaskDate = safeParseISO(lead.next_task);
    if (nextTaskDate) {
      const daysDiff = differenceInDays(nextTaskDate, now);
      if (daysDiff < 0) return { text: 'Follow-up overdue', color: 'text-red-600 font-semibold' };
      if (daysDiff === 0) return { text: 'Follow-up today', color: 'text-emerald-600 font-semibold' };
    }
    const lastResp = safeParseISO(lead.last_response_at);
    if (lastResp) return { text: `Responded ${formatDistanceToNow(lastResp, { addSuffix: true })}`, color: 'text-blue-600 font-medium' };
    const lastContact = safeParseISO(lead.last_contacted_at);
    if (lastContact) {
      const daysSinceContact = differenceInDays(now, lastContact);
      if (daysSinceContact > 2) return { text: 'Awaiting response', color: 'text-amber-600 font-medium' };
      return { text: `Contacted ${daysSinceContact === 0 ? 'today' : daysSinceContact + ' days ago'}`, color: 'text-slate-500' };
    }
    return { text: '', color: '' };
  };

  if (loading && leads.length === 0) {
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
        <div className="max-w-[1400px] mx-auto px-6 py-8">
            <div className="bg-surface rounded border border-border overflow-hidden">
                {[...Array(6)].map((_, i) => <TableRowSkeleton key={i} />)}
            </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-56px)] bg-page">
        <div className="flex flex-col items-center gap-3">
          <div className="text-[13px] text-error">{error}</div>
          <button onClick={fetchLeads} className="btn btn-primary">{t('Retry')}</button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-[calc(100vh-56px)] bg-page flex flex-col">
      <div className="page-header">
        <div>
          <h1 className="page-title">{t('Leads')}</h1>
          <p className="page-subtitle">{t('Track and assign conversations')}</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
            {formMeta && (
              <div className="hidden md:flex items-center gap-2">
                {canEditForm && Array.isArray(formMeta.teams) && formMeta.teams.length > 0 && (
                  <select
                    value={formMeta.default_team_id ?? ''}
                    onChange={onDefaultTeamChange}
                    className="h-8 text-[13px] border border-border rounded-md px-2 bg-surface text-primary"
                    aria-label="Website form default team"
                  >
                    <option value="">No team</option>
                    {formMeta.teams.map((t) => (
                      <option key={t.id} value={t.id}>{t.name}</option>
                    ))}
                  </select>
                )}
                <button
                  type="button"
                  onClick={copyFormLink}
                  className="btn btn-secondary h-8"
                >
                  {copied ? t('Copied') : t('Website form')}
                </button>
                <button
                  type="button"
                  onClick={copyWidgetSnippet}
                  className="btn btn-secondary h-8"
                >
                  {copiedWidget ? t('Copied') : t('Chat widget')}
                </button>
              </div>
            )}
            <Link
              href={unassignedLeadsPath(pathname)}
              className="h-8 px-3 border border-border rounded-md text-[11px] font-bold uppercase tracking-tight text-primary hover:bg-surface-elevated inline-flex items-center"
            >
              {t('Unassigned Pool')}
            </Link>
            <Link
              href={`${basePath}/trash`}
              className="btn btn-secondary h-8"
            >
              {t('Trash')}
            </Link>
            {canUndo && (
              <button
                type="button"
                onClick={undo}
                disabled={undoing}
                className="btn btn-secondary h-8 disabled:opacity-50"
              >
                <Undo2 size={14} /> {t('Undo last import')}
              </button>
            )}
            <button
              type="button"
              onClick={() => setIsImportOpen(true)}
              className="btn btn-secondary h-8"
            >
              <Upload size={14} /> {t('Import CSV')}
            </button>
            <button
              onClick={() => setIsModalOpen(true)}
              className="btn btn-primary h-8"
            >
              <Plus size={14} strokeWidth={2.25} /> {t('Add Lead')}
            </button>
        </div>
      </div>

      <div className="bg-surface border-b border-border sticky top-0 z-10">
        <div className="max-w-[72rem] mx-auto px-6 py-2 flex items-center gap-1.5 overflow-x-auto">
            <Filter size={13} strokeWidth={2} className="text-muted mr-1 shrink-0" />
            {ASSIGNMENT_TABS.map(tab => (
              <button
                key={tab.id}
                onClick={() => {
                  setActiveTab(tab.id);
                  setPage(0);
                }}
                className={`px-2.5 py-1 text-[13px] font-medium rounded-md transition-colors whitespace-nowrap ${activeTab === tab.id
                  ? 'bg-primary text-surface'
                  : 'text-muted hover:bg-surface-elevated hover:text-primary'
                  }`}
              >
                {t(tab.label)}
              </button>
            ))}
            <input
              type="search"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder={t('Search leads...')}
              aria-label={t('Search leads...')}
              className="ml-auto h-8 w-56 shrink-0 text-[13px] border border-border rounded-md px-2 bg-surface text-primary"
            />
        </div>
      </div>

      <div className="page-body flex-1 w-full">
        <div className="panel">
          <div className="hidden lg:flex items-center gap-4 px-4 py-2 border-b border-border">
            <div className="w-[30%] text-[12px] font-medium text-muted">{t('Lead')}</div>
            <div className="w-[20%] text-[12px] font-medium text-muted text-center">{t('Status')}</div>
            <div className="flex-1 text-[12px] font-medium text-muted">{t('Next step')}</div>
            <div className="w-8"></div>
          </div>
          <div className="divide-y divide-border/50">
            <AnimatePresence mode="popLayout">
              {filteredLeads.length === 0 ? (
                <motion.div 
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="p-12 text-center text-muted text-[13px] font-medium italic"
                >
                  {t('No leads found in this view.')}
                </motion.div>
              ) : (
                filteredLeads.map((lead) => {
                  const signal = getEngagementSignal(lead);
                  const isMuted = ['Converted', 'Lost', 'Lost Client'].includes(lead.status);

                  return (
                    <motion.div
                      key={lead.id}
                      layout
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={{ opacity: 0, x: 10 }}
                      transition={{ duration: 0.2 }}
                    >
                      <Link href={`${basePath}/${lead.id}`} className={`flex items-center gap-4 px-4 py-2.5 hover:bg-surface-elevated transition-colors group ${isMuted ? 'opacity-50' : ''}`}>
                        <div className="w-[30%] min-w-[180px]">
                          <div className="flex items-center gap-2">
                            <p className={`text-[13px] font-medium truncate ${isMuted ? 'text-muted' : 'text-primary'}`}>{lead.name}</p>
                            {!lead.assigned_to_id && (
                              <span className="badge badge-warning shrink-0">Open</span>
                            )}
                          </div>
                          {lead.company && (
                            <p className="text-[12px] text-muted truncate flex items-center gap-1 mt-0.5">
                              <Briefcase size={11} strokeWidth={2} /> {lead.company}
                            </p>
                          )}
                          {user?.role !== 'sales' && lead.assigned_to_name && (
                            <p className="text-[12px] text-muted truncate mt-0.5">
                              {t('Owner')}: {lead.assigned_to_name}
                            </p>
                          )}
                        </div>
                        <div className="w-[20%] flex justify-center">
                          <span className={`badge ${STATUS_STYLES[lead.status] || STATUS_STYLES['Active']}`}>
                            {t(lead.status === 'Converted' ? 'Client' : lead.status)}
                          </span>
                        </div>
                        <div className="flex-1 min-w-0">
                          {signal.text && (
                            <span className={`text-[12px] font-bold ${isMuted ? 'text-muted' : signal.color.replace('font-semibold', '').replace('font-medium', '')} truncate block`}>
                              {signal.text}
                            </span>
                          )}
                        </div>
                        <div className="text-muted group-hover:text-accent transition-all translate-x-0 group-hover:translate-x-1">
                          <ChevronRight size={14} strokeWidth={2.5} />
                        </div>
                      </Link>
                    </motion.div>
                  )
                })
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>

      <div className="max-w-[72rem] mx-auto px-6 mt-3 flex items-center justify-between pb-6 w-full">
        <span className="text-[12px] text-muted tabular-nums">
          {total > PAGE_SIZE ? `${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, total)} / ` : ''}{total} {t('Leads')}
        </span>
        {total > PAGE_SIZE && (
          <div className="flex items-center gap-2">
            <button type="button" className="btn btn-secondary h-8 disabled:opacity-50" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
              {t('Previous')}
            </button>
            <button type="button" className="btn btn-secondary h-8 disabled:opacity-50" disabled={(page + 1) * PAGE_SIZE >= total} onClick={() => setPage((p) => p + 1)}>
              {t('Next')}
            </button>
          </div>
        )}
      </div>

      <LeadModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onRefresh={fetchLeads}
        onCreated={handleLeadCreated}
      />
      <LeadImportModal
        isOpen={isImportOpen}
        onClose={() => setIsImportOpen(false)}
        onRefresh={() => { fetchLeads(); refreshBatch(); }}
      />
    </div>
  );
}
