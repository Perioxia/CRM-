'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowUpRight, RotateCw } from 'lucide-react';

const PLATFORM_API = '/api/platform';

const PLAN_NAMES = { 1: 'Starter', 2: 'Growth', 3: 'Enterprise' };

const TONE = {
    accent: 'var(--color-accent)',
    success: 'var(--color-success)',
    warning: 'var(--color-warning)',
    error: 'var(--color-error)',
};

const pct = (part, whole) => (whole ? Math.round((part / whole) * 100) : 0);

function SectionHeader({ title, meta, children }) {
    return (
        <div className="flex items-center justify-between gap-3 mb-4">
            <h2 className="text-[15px] font-semibold text-primary">{title}</h2>
            {children ?? (meta && <span className="text-[12px] text-muted tabular-nums">{meta}</span>)}
        </div>
    );
}

export default function PlatformDashboardPage() {
    const [metrics, setMetrics] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState(null);
    const [updatedAt, setUpdatedAt] = useState(null);

    const fetchMetrics = useCallback(async () => {
        try {
            const token = localStorage.getItem('platform_token');
            const response = await fetch(`${PLATFORM_API}/metrics/dashboard`, {
                headers: { Authorization: `Bearer ${token}` },
            });

            if (response.ok) {
                setMetrics(await response.json());
                setUpdatedAt(new Date());
                setError(null);
            } else {
                setError('Failed to load platform metrics. Please check your session.');
            }
        } catch (err) {
            console.error('Failed to fetch metrics:', err);
            setError('Failed to load platform metrics. Please try again.');
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, []);

    useEffect(() => {
        fetchMetrics();
    }, [fetchMetrics]);

    const refresh = () => {
        setRefreshing(true);
        fetchMetrics();
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[60vh]">
                <div className="w-7 h-7 border-2 border-border border-t-accent rounded-full animate-spin" />
            </div>
        );
    }

    if (error && !metrics) {
        return (
            <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3 px-6">
                <p className="text-sm font-medium text-error text-center">{error}</p>
                <button type="button" onClick={refresh} className="btn-primary text-sm">
                    Retry
                </button>
            </div>
        );
    }

    const companiesTotal = metrics?.companies?.total || 0;
    const companiesActive = metrics?.companies?.active || 0;
    const companiesPending = metrics?.companies?.pending || 0;
    const companiesSuspended = metrics?.companies?.suspended || 0;
    const usersTotal = metrics?.users?.total || 0;
    const usersActive = metrics?.users?.active || 0;

    const kpis = [
        {
            label: 'Companies',
            value: companiesTotal,
            detail: `${companiesActive} active`,
            tone: TONE.accent,
        },
        {
            label: 'Pending approvals',
            value: companiesPending,
            detail: companiesPending ? 'Needs review' : 'Queue is clear',
            tone: TONE.warning,
            href: '/platform/requests',
        },
        {
            label: 'Users',
            value: usersTotal,
            detail: `${usersActive} active`,
            tone: TONE.success,
        },
        {
            label: 'Suspended',
            value: companiesSuspended,
            detail: `${pct(companiesSuspended, companiesTotal)}% of companies`,
            tone: TONE.error,
            href: '/platform/companies',
        },
    ];

    const statusRows = [
        { label: 'Active', value: companiesActive, tone: TONE.success },
        { label: 'Pending', value: companiesPending, tone: TONE.warning },
        { label: 'Suspended', value: companiesSuspended, tone: TONE.error },
    ];

    const planRows = metrics?.plan_distribution || [];
    const planTotal = planRows.reduce((sum, p) => sum + (p.count || 0), 0);

    const volume = [
        { label: 'Leads', value: metrics?.business_metrics?.leads },
        { label: 'Clients', value: metrics?.business_metrics?.clients },
        { label: 'Tasks', value: metrics?.business_metrics?.tasks },
        { label: 'Invoices', value: metrics?.business_metrics?.invoices },
    ];

    const shortcuts = [
        { label: 'Pending signups', detail: `${companiesPending} awaiting review`, href: '/platform/requests' },
        { label: 'All companies', detail: 'Browse and filter by status', href: '/platform/companies' },
        { label: 'Plans', detail: 'Subscription catalog', href: '/platform/plans' },
        { label: 'Audit log', detail: 'Recent operator activity', href: '/platform/logs' },
    ];

    return (
        <div className="p-5 sm:p-6 lg:p-8 space-y-5 max-w-[1400px]">
            <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3">
                <div>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent mb-1.5">
                        Overview
                    </p>
                    <h1 className="font-display text-[1.75rem] font-semibold text-primary tracking-tight">
                        Analytics
                    </h1>
                    <p className="text-[15px] text-muted mt-1">System-wide health across every tenant</p>
                </div>
                <div className="flex items-center gap-3 self-start sm:self-auto">
                    {updatedAt && (
                        <span className="text-[12px] text-muted tabular-nums">
                            Updated {updatedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </span>
                    )}
                    <button
                        type="button"
                        onClick={refresh}
                        disabled={refreshing}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface text-[13px] font-medium text-secondary hover:text-primary hover:border-border-strong transition-colors disabled:opacity-60"
                    >
                        <RotateCw size={13} className={refreshing ? 'animate-spin' : ''} />
                        Refresh
                    </button>
                </div>
            </div>

            {error && (
                <p className="text-[13px] text-error">{error}</p>
            )}

            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {kpis.map((kpi) => {
                    const body = (
                        <>
                            <p className="text-[13px] font-medium text-secondary pl-1">{kpi.label}</p>
                            <p className="text-[2rem] leading-none font-semibold text-primary tabular-nums tracking-tight pl-1 mt-3">
                                {kpi.value.toLocaleString()}
                            </p>
                            <p className="text-[12px] text-muted mt-2 pl-1">{kpi.detail}</p>
                        </>
                    );
                    return kpi.href ? (
                        <Link
                            key={kpi.label}
                            href={kpi.href}
                            className="platform-card platform-kpi block hover:border-border-strong transition-colors"
                            style={{ '--kpi-accent': kpi.tone }}
                        >
                            {body}
                        </Link>
                    ) : (
                        <div
                            key={kpi.label}
                            className="platform-card platform-kpi"
                            style={{ '--kpi-accent': kpi.tone }}
                        >
                            {body}
                        </div>
                    );
                })}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                <section className="platform-card p-5">
                    <SectionHeader title="Company status" meta={`${companiesTotal} total`} />

                    <div className="flex h-2 rounded-full overflow-hidden bg-surface-elevated border border-border-subtle mb-5">
                        {statusRows.map((row) =>
                            row.value > 0 ? (
                                <span
                                    key={row.label}
                                    style={{ width: `${pct(row.value, companiesTotal)}%`, background: row.tone }}
                                />
                            ) : null
                        )}
                    </div>

                    <dl className="divide-y divide-border-subtle">
                        {statusRows.map((row) => (
                            <div key={row.label} className="flex items-center justify-between py-2.5 first:pt-0 last:pb-0">
                                <dt className="flex items-center gap-2 text-[14px] text-secondary">
                                    <span className="w-2 h-2 rounded-full" style={{ background: row.tone }} />
                                    {row.label}
                                </dt>
                                <dd className="flex items-baseline gap-3 tabular-nums">
                                    <span className="text-[12px] text-muted">{pct(row.value, companiesTotal)}%</span>
                                    <span className="text-[14px] font-semibold text-primary w-8 text-right">
                                        {row.value}
                                    </span>
                                </dd>
                            </div>
                        ))}
                    </dl>
                </section>

                <section className="platform-card p-5">
                    <SectionHeader title="Plan distribution">
                        <Link
                            href="/platform/plans"
                            className="inline-flex items-center gap-1 text-[12px] font-medium text-accent hover:text-accent-hover"
                        >
                            View plans
                            <ArrowUpRight size={13} />
                        </Link>
                    </SectionHeader>

                    {planRows.length === 0 ? (
                        <p className="text-[14px] text-muted py-2">No plan data yet</p>
                    ) : (
                        <div className="space-y-4">
                            {planRows.map((plan) => {
                                const count = plan.count || 0;
                                const share = pct(count, planTotal);
                                return (
                                    <div key={plan.plan_id}>
                                        <div className="flex items-center justify-between mb-1.5">
                                            <span className="text-[14px] text-secondary">
                                                {PLAN_NAMES[plan.plan_id] || `Plan ${plan.plan_id}`}
                                            </span>
                                            <span className="text-[13px] tabular-nums">
                                                <span className="font-semibold text-primary">{count}</span>
                                                <span className="text-muted">
                                                    {' '}
                                                    {count === 1 ? 'company' : 'companies'} · {share}%
                                                </span>
                                            </span>
                                        </div>
                                        <div className="platform-meter">
                                            <span style={{ width: `${Math.max(share, count > 0 ? 4 : 0)}%` }} />
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </section>
            </div>

            <section className="platform-card p-5">
                <SectionHeader title="Business volume" meta="All tenants" />
                <div className="grid grid-cols-2 md:grid-cols-4 divide-x divide-border-subtle">
                    {volume.map((item, i) => (
                        <div key={item.label} className={`px-4 py-1 ${i === 0 ? 'pl-0' : ''} ${i === 2 ? 'max-md:pl-0 max-md:border-l-0' : ''}`}>
                            <p className="text-[13px] text-muted">{item.label}</p>
                            <p className="text-2xl font-semibold text-primary tabular-nums mt-1 tracking-tight">
                                {item.value != null ? Number(item.value).toLocaleString() : '—'}
                            </p>
                        </div>
                    ))}
                </div>
            </section>

            <section>
                <h2 className="text-[12px] font-semibold uppercase tracking-[0.12em] text-muted mb-2.5">
                    Shortcuts
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    {shortcuts.map((s) => (
                        <Link key={s.href} href={s.href} className="platform-action group">
                            <div className="min-w-0 flex-1">
                                <p className="text-[14px] font-semibold text-primary">{s.label}</p>
                                <p className="text-[12px] text-muted truncate">{s.detail}</p>
                            </div>
                            <ArrowUpRight
                                size={15}
                                className="text-muted shrink-0 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-accent"
                            />
                        </Link>
                    ))}
                </div>
            </section>
        </div>
    );
}
