'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Loader2 } from 'lucide-react';
import api from '../../services/api';
import { leadsHomePath } from '../../lib/leadsPaths';
import { announceLeadsChanged, useLiveRefresh } from '../../lib/useLiveRefresh';
import { useAuth } from '../../contexts/AuthContext';
import { useT } from '../../contexts/LocaleContext';
import KPICard from '../shared/KPICard';

function apiDetail(err, fallback) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  return fallback;
}

function teamLabel(lead) {
  if (!lead || typeof lead !== 'object') return null;
  if (Object.prototype.hasOwnProperty.call(lead, 'team_name') && lead.team_name) {
    return String(lead.team_name);
  }
  if (!Object.prototype.hasOwnProperty.call(lead, 'team') || lead.team == null || lead.team === '') {
    return null;
  }
  if (typeof lead.team === 'string') return lead.team;
  if (typeof lead.team === 'object' && lead.team.name) return String(lead.team.name);
  return null;
}

export default function UnassignedPoolPage() {
  const pathname = usePathname();
  const leadsHome = leadsHomePath(pathname);
  const { user } = useAuth();
  const t = useT();
  const role = user?.role;
  const canClaim = role === 'sales';
  const canAssign = role === 'manager' || role === 'md';

  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [members, setMembers] = useState([]);
  const [membersError, setMembersError] = useState(null);
  const [membersLoading, setMembersLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [noticeKind, setNoticeKind] = useState('success');
  const [busyId, setBusyId] = useState(null);
  const [assignees, setAssignees] = useState({});

  const load = useCallback(async ({ silent } = {}) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const res = await api.get('/leads', { params: { unassigned: true, limit: 100 } });
      const data = res.data || {};
      const rows = (Array.isArray(data.items) ? data.items : []).filter(
        (lead) => lead.assigned_to_id == null || lead.assigned_to_id === undefined,
      );
      setItems(rows);
      setTotal(rows.length);
    } catch (err) {
      setError(apiDetail(err, t('Could not load the pool.')));
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [t]);

  const loadMembers = useCallback(async () => {
    if (!canAssign) return;
    setMembersLoading(true);
    setMembersError(null);
    try {
      const res = await api.get('/leads/team-members');
      setMembers(Array.isArray(res.data?.members) ? res.data.members : []);
    } catch (err) {
      setMembers([]);
      setMembersError(apiDetail(err, t('Could not load teammates.')));
    } finally {
      setMembersLoading(false);
    }
  }, [canAssign, t]);

  useEffect(() => {
    load();
  }, [load]);

  useLiveRefresh(() => load({ silent: true }));

  useEffect(() => {
    if (canAssign) loadMembers();
  }, [canAssign, loadMembers]);

  const onClaim = async (id) => {
    setBusyId(id);
    setNotice(null);
    try {
      await api.post(`/leads/${id}/claim`);
      setNoticeKind('success');
      setNotice(t('Lead claimed.'));
    } catch (err) {
      setNoticeKind('error');
      setNotice(apiDetail(err, t('Could not claim lead.')));
    } finally {
      setBusyId(null);
      announceLeadsChanged();
    }
  };

  const onAssign = async (id) => {
    const assignedToId = Number(assignees[id]);
    if (!assignedToId) {
      setNoticeKind('error');
      setNotice(t('Select a teammate first.'));
      return;
    }
    setBusyId(id);
    setNotice(null);
    try {
      await api.put(`/leads/${id}`, { assigned_to_id: assignedToId, only_if_unassigned: true });
      setNoticeKind('success');
      setNotice(t('Lead assigned.'));
      setAssignees((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    } catch (err) {
      setNoticeKind('error');
      setNotice(apiDetail(err, t('Could not assign lead.')));
    } finally {
      setBusyId(null);
      announceLeadsChanged();
    }
  };

  return (
    <div className="min-h-[calc(100vh-56px)] bg-page">
      <div className="bg-surface border-b border-border px-6 py-4">
        <div className="max-w-[1400px] mx-auto">
          <h1 className="text-xl font-bold text-primary tracking-tight">{t('Unassigned Pool')}</h1>
          <p className="text-[12px] text-muted font-medium mt-0.5 opacity-80 uppercase tracking-wider">
            {t('Leads with no owner')}
          </p>
        </div>
      </div>

      <div className="max-w-[1400px] mx-auto px-6 py-6 space-y-4">
        <div className="max-w-xs">
          <KPICard label={t('Total')} value={loading ? '—' : total} />
        </div>

        {notice && (
          <p className={`text-[13px] font-medium ${noticeKind === 'error' ? 'text-error' : 'text-success'}`}>
            {notice}
          </p>
        )}

        {canAssign && membersError && (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-[13px] text-error font-medium">{membersError}</p>
            <button type="button" onClick={() => loadMembers()} className="btn btn-primary text-[12px] px-3 py-1.5" disabled={membersLoading}>
              {membersLoading ? <Loader2 size={14} className="animate-spin" /> : t('Retry')}
            </button>
          </div>
        )}

        {loading && (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-8 w-8 animate-spin text-accent" aria-label={t('Loading')} />
          </div>
        )}

        {!loading && error && (
          <div className="flex flex-col items-center gap-3 py-12">
            <p className="text-[13px] text-error font-bold uppercase tracking-widest">{error}</p>
            <button type="button" onClick={() => load()} className="btn btn-primary">
              {t('Retry')}
            </button>
          </div>
        )}

        {!loading && !error && items.length === 0 && (
          <p className="py-12 text-center text-muted text-[13px] font-medium">{t('Pool is clear')}</p>
        )}

        {!loading && !error && items.length > 0 && (
          <div className="bg-surface rounded border border-border overflow-hidden shadow-sm">
            <div className="hidden lg:flex items-center gap-4 px-5 py-2.5 bg-surface-elevated/50 border-b border-border">
              <div className="w-[24%] text-[10px] font-black text-muted uppercase tracking-widest">{t('Name')}</div>
              <div className="w-[16%] text-[10px] font-black text-muted uppercase tracking-widest">{t('Phone')}</div>
              <div className="w-[16%] text-[10px] font-black text-muted uppercase tracking-widest">{t('Source')}</div>
              <div className="w-[14%] text-[10px] font-black text-muted uppercase tracking-widest">{t('Created')}</div>
              <div className="flex-1 text-[10px] font-black text-muted uppercase tracking-widest">{t('Actions')}</div>
            </div>
            <ul className="divide-y divide-border/50">
              {items.map((lead) => {
                const team = teamLabel(lead);
                return (
                  <li key={lead.id} className="flex flex-col lg:flex-row lg:items-center gap-2 lg:gap-4 px-5 py-3">
                    <div className="w-full lg:w-[24%] min-w-0">
                      <Link href={`${leadsHome}/${lead.id}`} className="text-[13px] font-bold text-primary hover:text-accent truncate block">
                        {lead.name}
                      </Link>
                      {team && <p className="text-[11px] text-muted truncate">{team}</p>}
                    </div>
                    <div className="w-full lg:w-[16%] text-[12px] text-secondary">{lead.phone || '—'}</div>
                    <div className="w-full lg:w-[16%] text-[12px] text-secondary">{lead.source || '—'}</div>
                    <div className="w-full lg:w-[14%] text-[12px] text-muted">{lead.created_at || '—'}</div>
                    <div className="flex-1 flex items-center gap-2">
                      {canClaim && (
                        <button
                          type="button"
                          className="btn btn-primary text-[12px] px-3 py-1.5"
                          disabled={busyId === lead.id}
                          onClick={() => onClaim(lead.id)}
                        >
                          {busyId === lead.id ? <Loader2 size={14} className="animate-spin" /> : t('Claim')}
                        </button>
                      )}
                      {canAssign && !membersError && (
                        <>
                          <select
                            aria-label={t('Assign')}
                            value={assignees[lead.id] || ''}
                            onChange={(e) => setAssignees((prev) => ({ ...prev, [lead.id]: e.target.value }))}
                            className="h-8 text-[12px] border border-border rounded-md px-2 bg-surface text-primary"
                            disabled={membersLoading}
                          >
                            <option value="">{membersLoading ? t('Loading') : t('Select')}</option>
                            {members.map((member) => (
                              <option key={member.id} value={member.id}>
                                {member.full_name || member.email}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            className="btn btn-primary text-[12px] px-3 py-1.5"
                            disabled={busyId === lead.id || membersLoading || !assignees[lead.id]}
                            onClick={() => onAssign(lead.id)}
                          >
                            {busyId === lead.id ? <Loader2 size={14} className="animate-spin" /> : t('Assign')}
                          </button>
                        </>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
