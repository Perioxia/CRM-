'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter, useParams, usePathname } from 'next/navigation';
import {
  ArrowLeft,
  Mail,
  Phone,
  Clock,
  Building2,
  MapPin,
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
  PlusCircle,
  Briefcase,
  AlertTriangle,
  Loader2
} from 'lucide-react';
import { formatDistanceToNow, parseISO } from 'date-fns';
import { formatDistanceToTaskDue } from '../../lib/taskDue';
import { clientsHomePath, leadsHomePath } from '../../lib/leadsPaths';
import { announceLeadsChanged } from '../../lib/useLiveRefresh';
import api from '../../services/api';
import { useAuth } from '../../contexts/AuthContext';
import TaskModal from './TaskModal';
import NoteModal from './NoteModal';
import ReassignModal from './ReassignModal';
import LeadEditModal from './LeadEditModal';
import DocumentsList from '../documents/DocumentsList';
import LeadEmailPanel from './LeadEmailPanel';
import LeadWhatsAppPanel from './LeadWhatsAppPanel';
import LeadTagsPanel from './LeadTagsPanel';
import LeadDuplicatesPanel from './LeadDuplicatesPanel';
import MeetingCallPanel from '../activity/MeetingCallPanel';
import ActivityFeed from '../activity/ActivityFeed';
import ScoreBadge from '../ScoreBadge';

export default function LeadDetailPage() {
  const router = useRouter();
  const pathname = usePathname();
  const { id } = useParams();
  const [lead, setLead] = useState(null);
  const [loading, setLoading] = useState(true);
  const [isDetailsOpen, setIsDetailsOpen] = useState(false);
  const [isTaskModalOpen, setIsTaskModalOpen] = useState(false);
  const [isNoteModalOpen, setIsNoteModalOpen] = useState(false);
  const [isReassignModalOpen, setIsReassignModalOpen] = useState(false);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [syncingClient, setSyncingClient] = useState(false);
  const [claiming, setClaiming] = useState(false);
  const [enriching, setEnriching] = useState(false);
  const [fieldDefs, setFieldDefs] = useState([]);
  const [customDraft, setCustomDraft] = useState({});
  const [savingFields, setSavingFields] = useState(false);
  const { user } = useAuth();
  const canPrivacy = user?.role === 'admin' || user?.role === 'md';
  const [activityTick, setActivityTick] = useState(0);
  const [loadError, setLoadError] = useState(null);

  useEffect(() => {
    fetchLeadData({ silent: false });
  }, [id]);

  const applyLeadPayload = (data) => {
    const isManager = pathname.startsWith('/manager');
    const isMd = pathname.startsWith('/md');
    const salesCreated = data.created_by_role === 'sales';
    const ownerHasLeft = data.assignee_active === false;
    const isConvertedStatus = ['Converted'].includes(data.status);
    const canReassign = !isConvertedStatus && (isMd || (isManager && (!salesCreated || ownerHasLeft)));
    data.permissions = {
      canEdit: !['Converted', 'Lost', 'Lost Client'].includes(data.status),
      canConvert: !['Converted', 'Lost', 'Lost Client'].includes(data.status),
      canAddTask: true,
      canAddNote: true,
      canReassign,
      canSendToPool: canReassign && !!data.assigned_to_id
    };
    setLead(data);
    setCustomDraft(data.custom_fields || {});
    setActivityTick((n) => n + 1);
  };

  const fetchLeadData = async ({ silent = true } = {}) => {
    if (!silent) {
      setLoading(true);
      setLoadError(null);
    }
    try {
      const res = await api.get(`/leads/${id}`);
      applyLeadPayload(res.data);
      api.get('/custom-fields', { params: { entity_type: 'lead' } })
        .then((defsRes) => setFieldDefs(defsRes.data.items || []))
        .catch(() => setFieldDefs([]));
    } catch (err) {
      console.error("Failed to fetch lead", err);
      const detail = err.response?.data?.detail;
      setLoadError(typeof detail === 'string' ? detail : 'Could not load this lead.');
      setLead(null);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  const handleUpdateStatus = async (newStatus) => {
    try {
      await api.put(`/leads/${id}`, { status: newStatus });
      fetchLeadData();
    } catch (err) {
      alert(err.response?.data?.detail || "Could not update the status");
    }
  };

  const handleDelete = async () => {
    if (!window.confirm('Move this lead to the trash? You can restore it later.')) return;
    try {
      await api.delete(`/leads/${id}`);
      announceLeadsChanged();
      router.push(leadsHomePath(pathname));
    } catch (err) {
      alert(err.response?.data?.detail || 'Could not delete this lead');
    }
  };

  const handleReassign = async (newOwnerId) => {
    try {
      await api.put(`/leads/${id}`, { assigned_to_id: newOwnerId });
      setIsReassignModalOpen(false);
      fetchLeadData();
      announceLeadsChanged();
    } catch (err) {
      alert(err.response?.data?.detail || "Failed to reassign lead");
    }
  };

  const handleSendToPool = async () => {
    if (!window.confirm('Send this lead back to the pool? Anyone on the team can then claim it.')) return;
    try {
      await api.post(`/leads/${id}/release`);
      fetchLeadData();
      announceLeadsChanged();
    } catch (err) {
      alert(err.response?.data?.detail || 'Could not send this lead back to the pool');
    }
  };

  const handleConvert = async () => {
    if (!window.confirm("Convert this lead to a formal client?")) return;
    try {
      await api.post(`/leads/${id}/convert`);
      alert("Converted successfully!");
      router.push(`${clientsHomePath(pathname)}`);
    } catch (err) {
      alert(err.response?.data?.detail || "Conversion failed");
      console.error("Conversion failed", err);
    }
  };

  const handleEnsureClientRecord = async () => {
    try {
      setSyncingClient(true);
      await api.post(`/leads/${id}/convert`);
      await fetchLeadData();
    } catch (err) {
      alert(err.response?.data?.detail || "Could not create client record");
    } finally {
      setSyncingClient(false);
    }
  };

  const handleEraseLead = async () => {
    if (!window.confirm('Erase personal data on this lead? This cannot be undone.')) return;
    try {
      await api.post(`/privacy/erase/leads/${id}`);
      fetchLeadData();
    } catch (err) {
      alert(err.response?.data?.detail || 'Could not erase lead');
    }
  };

  const handleExportLead = async () => {
    try {
      const res = await api.get(`/privacy/export/leads/${id}`);
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `lead-${id}-export.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      alert(err.response?.data?.detail || 'Could not export lead');
    }
  };

  const handleClaimLead = async () => {
    try {
      setClaiming(true);
      await api.post(`/leads/${id}/claim`);
      fetchLeadData();
    } catch (err) {
      alert(err.response?.data?.detail || "Failed to claim lead");
      fetchLeadData();
    } finally {
      announceLeadsChanged();
      setClaiming(false);
    }
  };

  const handleEnrichLead = async () => {
    try {
      setEnriching(true);
      const res = await api.post(`/leads/${id}/enrich`);
      setLead((prev) => (prev ? { ...prev, ...res.data } : prev));
      await fetchLeadData({ silent: true });
    } catch (err) {
      alert(err.response?.data?.detail || 'Could not fill company details from the domain');
    } finally {
      setEnriching(false);
    }
  };

  const handleAddNote = () => {
    setIsNoteModalOpen(true);
  };

  const handleCreateTask = () => {
    setIsTaskModalOpen(true);
  };

  if (loading) {
    return (
      <div className="p-6 flex items-center gap-2 text-slate-500">
        <Loader2 size={18} className="animate-spin" /> Loading…
      </div>
    );
  }
  if (loadError) {
    return (
      <div className="p-6 max-w-lg space-y-3">
        <p className="text-red-600 text-sm">{loadError}</p>
        <button
          type="button"
          onClick={() => router.push(leadsHomePath(pathname))}
          className="text-sm text-blue-600 hover:underline"
        >
          Back to leads
        </button>
      </div>
    );
  }
  if (!lead) return null;

  const clientsBase = clientsHomePath(pathname);
  const statusStr = typeof lead.status === 'string' ? lead.status : lead.status?.value ?? String(lead.status);
  const isConverted = statusStr === 'Converted';
  const missingClientRecord = isConverted && lead.converted_client_id == null;
  const isSalesPage = pathname?.startsWith('/sales');
  const isOpenLead = !lead.assigned_to_id;
  const isLost = ['Lost', 'Lost Client'].includes(statusStr);
  // A rep works only on leads they own; the API refuses the rest, so don't offer those buttons.
  const canWork = !isSalesPage || Number(lead.assigned_to_id) === Number(user?.id);

  return ( <>
    <div className="bg-slate-50 dark:bg-slate-900 min-h-full font-sans text-slate-900 dark:text-slate-100 pb-12">

      {/* --- PAGE HEADER --- */}
      <div className="bg-white dark:bg-slate-800 border-b border-slate-200 dark:border-slate-700 sticky top-0 z-20 px-6 py-4 shadow-sm">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => router.push(leadsHomePath(pathname))}
              className="p-2 -ml-2 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 rounded-full hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              <ArrowLeft size={18} />
            </button>
            <div>
              <div className="flex items-center gap-3">
                <h1 className="text-xl font-bold text-slate-900 dark:text-white leading-tight">
                  {lead.name}
                </h1>
                <span className="text-sm font-semibold text-slate-400 tabular-nums">#{lead.id}</span>
                <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300 uppercase tracking-wide border border-slate-200 dark:border-slate-600">
                  Lead
                </span>
                <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 uppercase tracking-wide border border-blue-100 dark:border-blue-800/50">
                  {lead.status === 'Converted' ? 'Client' : lead.status}
                </span>
                <ScoreBadge entity="leads" id={id} score={lead.score} />
              </div>
              <div className="flex items-center gap-3 mt-1 text-xs text-slate-500 dark:text-slate-400">
                {lead.company && (
                  <span className="flex items-center gap-1">
                    <Building2 size={12} /> {lead.company}
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {!canWork ? null : lead.enriched_at ? (
              <span
                className="px-3 py-2 text-xs font-medium text-slate-500 dark:text-slate-400"
                title="Blank company, website, industry and LinkedIn were filled from the email or website domain"
              >
                Enriched
              </span>
            ) : (
              <button
                type="button"
                onClick={handleEnrichLead}
                disabled={enriching}
                title="Fill blank company, website, industry and LinkedIn from the work email or website domain"
                className="px-3 py-2 border border-slate-200 dark:border-slate-600 text-slate-700 dark:text-slate-200 text-sm font-medium rounded-lg disabled:opacity-50"
              >
                {enriching ? 'Filling…' : 'Fill from domain'}
              </button>
            )}
            {canPrivacy && (
              <>
                <button type="button" onClick={handleExportLead}
                  className="px-3 py-2 border border-slate-200 dark:border-slate-600 text-slate-700 dark:text-slate-200 text-sm font-medium rounded-lg">
                  Export data
                </button>
                <button type="button" onClick={handleEraseLead}
                  className="px-3 py-2 border border-red-200 text-red-700 text-sm font-medium rounded-lg">
                  Erase PII
                </button>
              </>
            )}
            {isSalesPage && isOpenLead && (
              <button
                onClick={handleClaimLead}
                disabled={claiming}
                className="px-4 py-2 bg-amber-500 hover:bg-amber-600 text-white text-sm font-semibold rounded-lg shadow-sm transition-colors disabled:opacity-50 flex items-center gap-2"
              >
                {claiming && <Loader2 size={14} className="animate-spin" />}
                Claim this Lead
              </button>
            )}
            {lead.permissions?.canReassign && (
              <button 
                onClick={() => setIsReassignModalOpen(true)}
                className="px-4 py-2 bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 text-sm font-medium rounded-lg hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors"
               >
                Reassign Owner
              </button>
            )}
            {lead.permissions?.canSendToPool && (
              <button
                onClick={handleSendToPool}
                className="px-4 py-2 bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 text-sm font-medium rounded-lg hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors"
              >
                Back to Pool
              </button>
            )}
            {canWork && (
              <>
                <button
                  type="button"
                  onClick={() => setIsEditModalOpen(true)}
                  className="px-4 py-2 bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 text-slate-700 dark:text-slate-200 text-sm font-medium rounded-lg hover:bg-slate-50 dark:hover:bg-slate-600 transition-colors"
                >
                  Edit
                </button>
                <button
                  type="button"
                  onClick={handleDelete}
                  className="px-4 py-2 border border-red-200 text-red-700 text-sm font-medium rounded-lg hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors"
                >
                  Delete
                </button>
              </>
            )}
            {canWork && isLost && (
              <button
                type="button"
                onClick={() => handleUpdateStatus('Active')}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold rounded-lg shadow-sm transition-colors"
              >
                Reopen Lead
              </button>
            )}
            {canWork && lead.permissions?.canEdit && (
              <div className="relative group">
                <button className="px-4 py-2 bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 text-slate-700 dark:text-slate-200 text-sm font-medium rounded-lg hover:bg-slate-50 dark:hover:bg-slate-600 transition-colors flex items-center gap-2">
                  Update Status <ChevronDown size={14} />
                </button>
                <div className="absolute right-0 mt-2 w-48 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-xl hidden group-hover:block z-30 overflow-hidden">
                  {['Active', 'Lost', 'Convert to Client'].map(status => (
                    <button
                      key={status}
                      onClick={() => status === 'Convert to Client' ? handleConvert() : handleUpdateStatus(status)}
                      className="w-full text-left px-4 py-2 text-sm text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
                    >
                      {status}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {canWork && lead.permissions?.canConvert && (
              <button
                onClick={handleConvert}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold rounded-lg shadow-sm transition-colors"
              >
                Convert to Client
              </button>
            )}
            {isConverted && lead.converted_client_id != null && (
              <Link
                href={`${clientsBase}/${lead.converted_client_id}`}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold rounded-lg shadow-sm transition-colors"
              >
                Open client profile
              </Link>
            )}
          </div>
        </div>
      </div>

      {missingClientRecord && (
        <div className="bg-amber-50 dark:bg-amber-950/40 border-b border-amber-200 dark:border-amber-800/60">
          <div className="max-w-6xl mx-auto px-6 py-3 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div className="flex items-start gap-2 text-sm text-amber-900 dark:text-amber-100">
              <AlertTriangle className="w-5 h-5 flex-shrink-0 text-amber-600 dark:text-amber-400 mt-0.5" />
              <p>
                <span className="font-semibold">No client record linked.</span>{" "}
                This lead is marked converted but has no entry in Clients (often from a status-only change). Create the record to use the Clients area and invoices.
              </p>
            </div>
            <button
              type="button"
              onClick={handleEnsureClientRecord}
              disabled={syncingClient}
              className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold bg-amber-600 hover:bg-amber-700 text-white disabled:opacity-60 shrink-0"
            >
              {syncingClient ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              {syncingClient ? "Creating…" : "Create client record"}
            </button>
          </div>
        </div>
      )}

      <div className="max-w-6xl mx-auto px-6 py-6 grid grid-cols-1 lg:grid-cols-12 gap-6">

        {/* --- LEFT COLUMN: CORE ACTIVITY (7 cols) --- */}
        <div className="lg:col-span-7 space-y-6">

          <ActivityFeed entityType="lead" entityId={id} reloadKey={activityTick} />

        </div>

        {/* --- RIGHT COLUMN: CONTEXT & TASKS (5 cols) --- */}
        <div className="lg:col-span-5 space-y-6">

          {/* Section 1: Lead Overview (Compact) */}
          <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm p-5">
            <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wide mb-4">Lead Overview</h2>
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-500">Lead ID</span>
                <span className="font-semibold text-slate-900 dark:text-white tabular-nums">#{lead.id}</span>
              </div>
              <div className="flex items-center gap-3 text-sm">
                <Mail size={14} className="text-slate-400 w-4" />
                <span className="text-blue-600 hover:underline cursor-pointer truncate">{lead.email}</span>
              </div>
              <div className="flex items-center gap-3 text-sm">
                <Phone size={14} className="text-slate-400 w-4" />
                <span className="text-slate-700 dark:text-slate-300">{lead.phone}</span>
              </div>
              <div className="flex items-center gap-3 text-sm">
                <Briefcase size={14} className="text-slate-400 w-4" />
                <span className="text-slate-700 dark:text-slate-300">{lead.source}</span>
              </div>
              {lead.website && (
                <p className="text-sm text-slate-700 dark:text-slate-300 truncate">{lead.website}</p>
              )}
              {lead.industry && (
                <p className="text-sm text-slate-700 dark:text-slate-300">{lead.industry}</p>
              )}
              {lead.linkedin_url && (
                <a href={lead.linkedin_url} className="text-sm text-blue-600 truncate block" target="_blank" rel="noreferrer">
                  {lead.linkedin_url}
                </a>
              )}
              <div className="pt-3 mt-3 border-t border-slate-100 dark:border-slate-700/50 flex items-center justify-between text-xs">
                <span className="text-slate-500">Assignee</span>
                {lead.assigned_to_id ? (
                  <span className="font-medium text-slate-900 dark:text-white">{lead.assignee}</span>
                ) : (
                  <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide bg-amber-50 text-amber-700 border border-amber-200">Open to Anyone</span>
                )}
              </div>
            </div>
          </div>

          {/* Section 3: Tasks */}
          <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wide">Pending Tasks</h2>
              <span className="text-[10px] bg-slate-100 px-1.5 py-0.5 rounded text-slate-500">
                {(lead.tasks || []).filter(t => t.status !== 'Completed').length} Open
              </span>
            </div>
            <div className="space-y-1">
              {(lead.tasks || []).filter(t => t.status !== 'Completed').map((task) => (
                <div key={task.id} className="group p-2 rounded hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors cursor-pointer">
                  <div className="flex items-start gap-3">
                    <button
                      disabled={!canWork}
                      onClick={async () => {
                        try {
                          await api.post(`/tasks/${task.id}/complete`);
                          fetchLeadData();
                        } catch (err) { console.error(err); }
                      }}
                      title="Mark as completed"
                      className="mt-0.5 w-3.5 h-3.5 rounded border border-slate-300 dark:border-slate-500 hover:border-emerald-500 hover:bg-emerald-50 flex items-center justify-center transition-colors"
                    >
                    </button>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium truncate text-slate-700 dark:text-slate-200">
                        {task.title}
                      </p>
                      {(task.status === 'Open' || task.status === 'Pending') && (
                        <div className="flex items-center gap-2 mt-1">
                          <span className={`text-[10px] ${task.due_date ? 'text-amber-600' : 'text-slate-400'} flex items-center gap-1`}>
                            <Clock size={10} /> {task.due_date ? (formatDistanceToTaskDue(task.due_date, { addSuffix: true }) || task.due_date) : 'No date'}
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
              {(lead.tasks || []).filter(t => t.status !== 'Completed').length === 0 && (
                <p className="text-xs text-slate-400 text-center py-3 italic">No pending tasks</p>
              )}
              {canWork && lead.permissions?.canAddTask && (
                <button onClick={async () => {
                  const title = window.prompt('Task title:');
                  if (!title) return;
                  const dueDate = window.prompt('Due date (YYYY-MM-DD):', new Date(Date.now() + 86400000).toISOString().split('T')[0]);
                  if (!dueDate) return;
                  try {
                    await api.post('/tasks', { title, lead_id: parseInt(id), due_date: dueDate, priority: 'medium' });
                    fetchLeadData();
                  } catch (err) { alert(err.response?.data?.detail || 'Failed to add task'); }
                }} className="w-full py-2 mt-2 text-xs font-medium text-slate-500 hover:text-blue-600 dashed border border-slate-200 rounded hover:border-blue-200 transition-all flex items-center justify-center gap-2">
                  <PlusCircle size={12} /> Add Task
                </button>
              )}
            </div>
          </div>

          {canWork && (
            <>
              <LeadDuplicatesPanel leadId={id} onMerged={fetchLeadData} />
              <MeetingCallPanel parentType="lead" parentId={id} onChanged={fetchLeadData} hideHistory />
              <LeadTagsPanel leadId={id} tags={lead.tags} onChanged={fetchLeadData} />
              <LeadEmailPanel leadId={id} leadEmail={lead.email} hideHistory onChanged={fetchLeadData} />
              <LeadWhatsAppPanel leadId={id} leadPhone={lead.phone} hideHistory onChanged={fetchLeadData} />
            </>
          )}

          {/* Section 4: Notes */}
          <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wide">Recent Notes</h2>
              {canWork && lead.permissions?.canAddNote && (
                <button onClick={handleAddNote} className="text-xs text-blue-600 hover:underline">Add Note</button>
              )}
            </div>
            <div className="space-y-4">
              {lead.notes && (
                <div className="text-xs">
                  <p className="text-slate-600 dark:text-slate-300 leading-relaxed bg-amber-50 dark:bg-amber-900/20 p-3 rounded-lg border border-amber-100 dark:border-amber-800/40 whitespace-pre-wrap">
                    {lead.notes}
                  </p>
                  <div className="mt-1 px-1 text-slate-400">Added when the lead came in</div>
                </div>
              )}
              {lead.notes_list?.map((note) => (
                <div key={note.id} className="text-xs">
                  <p className="text-slate-600 dark:text-slate-300 leading-relaxed bg-slate-50 dark:bg-slate-700/30 p-3 rounded-lg border border-slate-100 dark:border-slate-700/50">
                    {note.content}
                  </p>
                  <div className="flex items-center justify-between mt-1 px-1">
                    <span className="text-slate-400">{note.created_by_name || 'System'}</span>
                    <span className="text-slate-400">{note.created_at ? (() => { try { return formatDistanceToNow(parseISO(note.created_at), { addSuffix: true }); } catch { return note.created_at; } })() : '—'}</span>
                  </div>
                </div>
              ))}
              {!lead.notes && (!lead.notes_list || lead.notes_list.length === 0) && (
                <p className="text-xs text-slate-400 text-center py-4">No notes yet.</p>
              )}
            </div>
          </div>

          {/* Section: Documents */}
          {canWork && (
            <DocumentsList 
              entityType="lead" 
              entityId={id} 
              canDelete={true} 
              canUpload={true} 
            />
          )}

          {/* Section 5: Lead Details (Collapsible) */}
          <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm overflow-hidden">
            <button
              onClick={() => setIsDetailsOpen(!isDetailsOpen)}
              className="w-full flex items-center justify-between px-5 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors"
            >
              <span className="text-xs font-bold text-slate-500 uppercase tracking-wide">Full Details</span>
              <ChevronDown size={14} className={`text-slate-400 transition-transform ${isDetailsOpen ? 'rotate-180' : ''}`} />
            </button>

            {isDetailsOpen && (
              <div className="px-5 pb-5 pt-0 border-t border-slate-100 dark:border-slate-700/50 animate-in slide-in-from-top-1">
                <div className="space-y-3 pt-3">
                  <div>
                    <span className="block text-[10px] text-slate-400 uppercase">Lead ID</span>
                    <span className="text-sm text-slate-700 dark:text-slate-300">#{lead.id}</span>
                  </div>
                  <div>
                    <span className="block text-[10px] text-slate-400 uppercase">Lead Source</span>
                    <span className="text-sm text-slate-700 dark:text-slate-300">{lead.source || 'Direct'}</span>
                  </div>
                  <div>
                    <span className="block text-[10px] text-slate-400 uppercase">Service Interested In</span>
                    <span className="text-sm text-slate-700 dark:text-slate-300">{lead.service_type || 'General Inquiry'}</span>
                  </div>
                  <div>
                    <span className="block text-[10px] text-slate-400 uppercase">Created On</span>
                    <span className="text-sm text-slate-700 dark:text-slate-300">
                      {lead.created_at ? new Date(lead.created_at).toLocaleDateString() : '—'}
                    </span>
                  </div>
                  {fieldDefs.length > 0 && (
                    <div className="pt-2 space-y-2">
                      <span className="block text-[10px] text-slate-400 uppercase">Custom fields</span>
                      {fieldDefs.map((f) => (
                        <label key={f.id} className="block">
                          <span className="block text-xs text-slate-500 mb-1">{f.name}</span>
                          {f.field_type === 'picklist' ? (
                            <select
                              value={customDraft[f.field_key] || ''}
                              onChange={(e) => setCustomDraft({ ...customDraft, [f.field_key]: e.target.value })}
                              className="w-full px-2 py-1.5 border border-slate-200 dark:border-slate-600 rounded text-sm bg-white dark:bg-slate-900"
                            >
                              <option value="">Select</option>
                              {(f.options || []).map((opt) => (
                                <option key={opt} value={opt}>{opt}</option>
                              ))}
                            </select>
                          ) : (
                            <input
                              type={f.field_type === 'number' ? 'number' : f.field_type === 'date' ? 'date' : 'text'}
                              value={customDraft[f.field_key] || ''}
                              onChange={(e) => setCustomDraft({ ...customDraft, [f.field_key]: e.target.value })}
                              className="w-full px-2 py-1.5 border border-slate-200 dark:border-slate-600 rounded text-sm bg-white dark:bg-slate-900"
                            />
                          )}
                        </label>
                      ))}
                      {canWork && lead.permissions?.canEdit && (
                        <button
                          type="button"
                          disabled={savingFields}
                          onClick={async () => {
                            setSavingFields(true);
                            try {
                              await api.patch(`/leads/${id}`, { custom_fields: customDraft });
                              await fetchLeadData();
                            } catch (err) {
                              alert(err.response?.data?.detail || 'Could not save fields');
                            } finally {
                              setSavingFields(false);
                            }
                          }}
                          className="px-3 py-1.5 bg-slate-900 text-white text-xs rounded-lg disabled:opacity-50"
                        >
                          {savingFields ? 'Saving…' : 'Save fields'}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

        </div>

      </div>
    </div>
  <TaskModal
    isOpen={isTaskModalOpen}
    onClose={() => setIsTaskModalOpen(false)}
    onRefresh={fetchLeadData}
    currentLeadId={id}
  />
  <NoteModal
    isOpen={isNoteModalOpen}
    onClose={() => setIsNoteModalOpen(false)}
    onRefresh={fetchLeadData}
    endpoint={`/leads/${id}/notes`}
  />
  <LeadEditModal
    isOpen={isEditModalOpen}
    onClose={() => setIsEditModalOpen(false)}
    onSaved={fetchLeadData}
    lead={lead}
  />
  <ReassignModal
    isOpen={isReassignModalOpen}
    onClose={() => setIsReassignModalOpen(false)}
    onReassign={handleReassign}
    currentAssigneeId={lead.assigned_to_id}
  />
  </> );
}
