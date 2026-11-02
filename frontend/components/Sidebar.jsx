'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '../contexts/AuthContext';
import { useT } from '../contexts/LocaleContext';
import { useChat } from '../contexts/ChatContext';
import LanguageToggle from './LanguageToggle';
import { financeService } from '../services/financeService';
import api from '../services/api';
import { useLiveRefresh } from '../lib/useLiveRefresh';
import {
    LayoutDashboard,
    Users,
    User,
    Calendar,
    PieChart,
    Settings,
    LogOut,
    Briefcase,
    BarChart,
    Activity,
    Receipt,
    CheckSquare,
    BarChart3,
    ShoppingCart,
    DollarSign,
    Award,
    UserSearch,
    UserCheck,
    UsersRound,
    GitBranch,
    FileText,
    BookOpen,
    Bug,
    Target,
    TrendingUp,
    Package,
    ShoppingBag,
    Sparkles,
    Menu,
    PanelLeftClose,
    Mail,
    MessageCircle,
    MessagesSquare,
    Building2,
    Layers,
} from 'lucide-react';

const ICON_MAP = {
    LayoutDashboard,
    CheckSquare,
    Users,
    BarChart3,
    BarChart,
    Settings,
    Briefcase,
    Activity,
    Receipt,
    Calendar,
    PieChart,
    LogOut,
    ShoppingCart,
    DollarSign,
    Award,
    UserSearch,
    UserCheck,
    UsersRound,
    GitBranch,
    FileText,
    BookOpen,
    Bug,
    Target,
    TrendingUp,
    Package,
    ShoppingBag,
    Sparkles,
    Mail,
    MessageCircle,
    MessagesSquare,
    Building2,
    Layers,
};

const ROLE_NAVIGATION = {
    sales: [
        { category: 'Workspace' },
        { name: 'Dashboard', href: '/sales/dashboard', icon: 'LayoutDashboard' },
        { name: 'Tasks', href: '/sales/tasks', icon: 'CheckSquare' },
        { name: 'Follow-ups', href: '/sales/follow-ups', icon: 'Calendar' },
        { name: 'Conversations', href: '/sales/conversations', icon: 'MessageCircle' },
        { name: 'Appointments', href: '/sales/appointments', icon: 'Calendar' },
        { name: 'AI Assistant', href: '/sales/assistant', icon: 'Sparkles' },
        { category: 'Pipeline' },
        { name: 'Leads', href: '/sales/leads', icon: 'Users' },
        { name: 'Unassigned Pool', href: '/sales/leads/unassigned', icon: 'Users' },
        { name: 'Clients', href: '/sales/clients', icon: 'Briefcase' },
        { name: 'Accounts', href: '/sales/accounts', icon: 'Building2' },
        { name: 'Deals', href: '/sales/deals', icon: 'Target' },
        { name: 'Quotations', href: '/sales/quotes', icon: 'FileText' },
        { category: 'Catalog' },
        { name: 'My Orders', href: '/sales/orders', icon: 'Receipt' },
        { name: 'Stock', href: '/sales/stock', icon: 'Package' },
        { name: 'Products', href: '/sales/products', icon: 'ShoppingBag' },
        { category: 'Insights' },
        { name: 'Performance', href: '/sales/performance', icon: 'BarChart' },
        { name: 'Forecast', href: '/reports/forecast', icon: 'TrendingUp' },
    ],
    manager: [
        { category: 'Workspace' },
        { name: 'Dashboard', href: '/manager/dashboard', icon: 'LayoutDashboard' },
        { name: 'Team', href: '/manager/team', icon: 'UsersRound' },
        { name: 'Tasks', href: '/manager/tasks', icon: 'CheckSquare' },
        { name: 'Conversations', href: '/manager/conversations', icon: 'MessageCircle' },
        { name: 'Appointments', href: '/manager/appointments', icon: 'Calendar' },
        { name: 'AI Assistant', href: '/manager/assistant', icon: 'Sparkles' },
        { category: 'Pipeline' },
        { name: 'Leads', href: '/manager/leads', icon: 'Users' },
        { name: 'Unassigned Pool', href: '/manager/leads/unassigned', icon: 'Users' },
        { name: 'Clients', href: '/manager/clients', icon: 'Briefcase' },
        { name: 'Accounts', href: '/manager/accounts', icon: 'Building2' },
        { name: 'Deals', href: '/manager/deals', icon: 'Target' },
        { category: 'Catalog' },
        { name: 'Stock', href: '/manager/stock', icon: 'Package' },
        { name: 'Products', href: '/manager/products', icon: 'ShoppingBag' },
        { category: 'Insights' },
        { name: 'Reports', href: '/manager/reports', icon: 'PieChart' },
        { name: 'Saved reports', href: '/reports', icon: 'BarChart3' },
        { name: 'Forecast', href: '/reports/forecast', icon: 'TrendingUp' },
    ],
    admin: [
        { name: 'Dashboard', href: '/admin/dashboard', icon: 'LayoutDashboard' },
        { name: 'User Management', href: '/admin/users', icon: 'UsersRound' },
        { name: 'Team Management', href: '/admin/teams', icon: 'GitBranch' },
        { name: 'Approvals', href: '/admin/approvals', icon: 'UserCheck' },
        { name: 'Audit Logs', href: '/admin/audit', icon: 'FileText' },
        { name: 'Products', href: '/admin/products', icon: 'ShoppingBag' },
        { name: 'Saved reports', href: '/reports', icon: 'BarChart3' },
        { name: 'Forecast', href: '/reports/forecast', icon: 'TrendingUp' },
        { name: 'AI Assistant', href: '/admin/assistant', icon: 'Sparkles' },
        { category: 'SETTINGS' },
        {
            name: 'Settings',
            href: '/settings',
            icon: 'Settings',
            children: [
                { name: 'Email', href: '/settings/email' },
                { name: 'Lead sheet', href: '/settings/lead-sheet' },
                { name: 'Calendar', href: '/settings/calendar' },
                { name: 'Webhooks', href: '/settings/webhooks' },
                { name: 'SAML SSO', href: '/settings/sso' },
                { name: 'Privacy', href: '/settings/privacy' },
                { name: 'Territories', href: '/settings/territories' },
                { name: 'Sandbox', href: '/settings/sandbox' },
            ],
        },
    ],
    md: [
        { category: 'Workspace' },
        { name: 'Dashboard', href: '/md/dashboard', icon: 'LayoutDashboard' },
        { name: 'Revenue', href: '/md/revenue', icon: 'DollarSign' },
        { name: 'Teams', href: '/md/teams', icon: 'Users' },
        { name: 'Employee Lookup', href: '/md/employee-lookup', icon: 'UserSearch' },
        { name: 'Appointments', href: '/md/appointments', icon: 'Calendar' },
        { name: 'Conversations', href: '/md/conversations', icon: 'MessageCircle' },
        { name: 'AI Assistant', href: '/md/assistant', icon: 'Sparkles' },
        { category: 'Pipeline' },
        { name: 'Leads', href: '/md/leads', icon: 'Target' },
        { name: 'Unassigned Pool', href: '/md/leads/unassigned', icon: 'Users' },
        { name: 'Clients', href: '/md/clients', icon: 'Briefcase' },
        { name: 'Accounts', href: '/md/accounts', icon: 'Building2' },
        { name: 'Deals', href: '/md/deals', icon: 'Target' },
        { category: 'Catalog' },
        { name: 'Stock', href: '/md/stock', icon: 'Package' },
        { name: 'Products', href: '/md/products', icon: 'ShoppingBag' },
        { name: 'Invoices', href: '/md/invoices', icon: 'Receipt' },
        { category: 'Insights' },
        { name: 'Saved reports', href: '/reports', icon: 'BarChart3' },
        { name: 'Forecast', href: '/reports/forecast', icon: 'TrendingUp' },
        { category: 'SETTINGS' },
        {
            name: 'Settings',
            href: '/settings',
            icon: 'Settings',
            children: [
                { name: 'Email', href: '/settings/email' },
                { name: 'Lead sheet', href: '/settings/lead-sheet' },
                { name: 'Calendar', href: '/settings/calendar' },
                { name: 'Webhooks', href: '/settings/webhooks' },
                { name: 'SAML SSO', href: '/settings/sso' },
                { name: 'Privacy', href: '/settings/privacy' },
                { name: 'Territories', href: '/settings/territories' },
                { name: 'Sandbox', href: '/settings/sandbox' },
            ],
        },
    ],
    purchase: [
        { name: 'Dashboard', href: '/purchase/dashboard', icon: 'LayoutDashboard' },
        { name: 'Sales Approvals', href: '/purchase/sales', icon: 'ShoppingCart' },
        { name: 'Stock', href: '/purchase/stock', icon: 'Package' },
        { name: 'Products', href: '/purchase/products', icon: 'ShoppingBag' },
        { name: 'Invoice Management', href: '/purchase/invoices', icon: 'Receipt' },
        { name: 'Purchase Monitoring', href: '/purchase/monitoring', icon: 'BarChart3' },
        { name: 'AI Assistant', href: '/purchase/assistant', icon: 'Sparkles' },
    ],
};

export default function Sidebar({ isOpen, setIsOpen }) {
    const pathname = usePathname();
    const { user, loading } = useAuth();
    const t = useT();
    const { enabled: chatEnabled, unreadTotal: chatUnread } = useChat();
    const [navigation, setNavigation] = useState([]);
    const [authorizedLedgers, setAuthorizedLedgers] = useState([]);
    const [ledgerError, setLedgerError] = useState(null);
    const [ledgerLoading, setLedgerLoading] = useState(true);
    const [customModules, setCustomModules] = useState([]);
    const [unassignedCount, setUnassignedCount] = useState(0);
    const [overdueCount, setOverdueCount] = useState(0);
    const [unansweredCount, setUnansweredCount] = useState(0);

    const fetchLedgers = useCallback(async () => {
        setLedgerError(null);
        setLedgerLoading(true);
        try {
            const data = await financeService.getAuthorizedLedgers();
            setAuthorizedLedgers(Array.isArray(data) ? data : []);
        } catch (err) {
            setAuthorizedLedgers([]);
            setLedgerError(err?.response?.data?.detail || err?.message || 'Failed to load ledgers');
        } finally {
            setLedgerLoading(false);
        }
    }, []);

    const fetchModules = useCallback(async () => {
        try {
            const res = await api.get('/modules');
            setCustomModules(res.data.items || []);
        } catch {
            setCustomModules([]);
        }
    }, []);

    useEffect(() => {
        fetchLedgers();
    }, [fetchLedgers]);

    useEffect(() => {
        if (loading || !user) return;
        fetchModules();
    }, [fetchModules, loading, user]);

    const fetchPoolCount = useCallback(async () => {
        if (loading || !user) return;
        const role = user?.role || 'sales';
        const hasPool = (ROLE_NAVIGATION[role] || []).some((item) => item.name === 'Unassigned Pool');
        if (!hasPool) {
            setUnassignedCount(0);
            return;
        }
        try {
            const res = await api.get('/leads', { params: { unassigned: true, limit: 1 } });
            setUnassignedCount(Number(res.data?.total) || 0);
        } catch {
            // Keep the last count: a failed background refresh is not worth an error in the sidebar.
        }
    }, [loading, user?.role, user?.id]);

    useEffect(() => {
        fetchPoolCount();
    }, [fetchPoolCount]);

    useLiveRefresh(fetchPoolCount);

    useEffect(() => {
        if (loading || !user) return;
        const role = user?.role || 'sales';
        const hasAppointments = (ROLE_NAVIGATION[role] || []).some((item) => item.name === 'Appointments');
        if (!hasAppointments) {
            setOverdueCount(0);
            return undefined;
        }
        let cancelled = false;
        api.get('/meetings/summary')
            .then((res) => {
                if (!cancelled) setOverdueCount(Number(res.data?.overdue) || 0);
            })
            .catch(() => {
                if (!cancelled) setOverdueCount(0);
            });
        return () => {
            cancelled = true;
        };
    }, [loading, user?.role, user?.id]);

    useEffect(() => {
        if (loading || !user) return;
        const role = user?.role || 'sales';
        const hasConversations = (ROLE_NAVIGATION[role] || []).some((item) => item.name === 'Conversations');
        if (!hasConversations) {
            setUnansweredCount(0);
            return undefined;
        }
        let cancelled = false;
        api.get('/whatsapp/threads', { params: { unanswered: true, limit: 1 } })
            .then((res) => {
                if (!cancelled) setUnansweredCount(Number(res.data?.total) || 0);
            })
            .catch(() => {
                if (!cancelled) setUnansweredCount(0);
            });
        return () => {
            cancelled = true;
        };
    }, [loading, user?.role, user?.id]);

    // Build navigation: role-based nav (dashboard, leads, etc.) + Financial Ledgers from API only
    useEffect(() => {
        if (loading) return;

        let role = user?.role;
        if (pathname.startsWith('/manager')) role = 'manager';
        else if (pathname.startsWith('/admin')) role = 'admin';
        else if (pathname.startsWith('/md')) role = 'md';
        else if (pathname.startsWith('/purchase')) role = 'purchase';
        else if (pathname.startsWith('/sales')) role = 'sales';
        role = role || 'sales';

        const navData = [...(ROLE_NAVIGATION[role] || ROLE_NAVIGATION.sales)];

        if (chatEnabled) {
            const dashboardIdx = navData.findIndex((item) => item.name === 'Dashboard');
            navData.splice(dashboardIdx + 1, 0, { name: 'Chat', href: '/chat', icon: 'MessagesSquare' });
        }

        if (!ledgerLoading && authorizedLedgers.length > 0) {
            navData.push({
                name: 'Financial Ledgers',
                href: '/financial-ledgers',
                icon: 'BookOpen',
                children: authorizedLedgers.map((l) => ({
                    name: l.name,
                    href: `/financial-ledgers/${(l.slug || '').replace(/_/g, '-')}`
                }))
            });
        }

        if (customModules.length > 0) {
            navData.push({
                name: 'Custom modules',
                href: '/modules',
                icon: 'Layers',
                children: customModules.map((m) => ({
                    name: m.name,
                    href: `/modules/${m.slug}`,
                })),
            });
        }

        navData.push({ category: 'Outreach' });
        navData.push({ name: 'Campaigns', href: '/campaigns', icon: 'Mail' });
        navData.push({ name: 'Mass email', href: '/mass-email', icon: 'Mail' });
        navData.push({ name: 'Cases', href: '/cases', icon: 'Bug' });
        navData.push({ category: 'Support' });
        navData.push({ name: 'Report Bug', href: '/report-bug', icon: 'Bug' });

        setNavigation(navData);
    }, [user?.role, loading, pathname, ledgerLoading, authorizedLedgers, customModules, chatEnabled]);

    return (
        <>
            {/* Mobile overlay */}
            {isOpen && (
                <div
                    className="fixed inset-0 bg-primary/20 z-20 lg:hidden"
                    onClick={() => setIsOpen(false)}
                />
            )}

            {/* Sidebar */}
            <div
                className={`fixed top-0 left-0 z-30 h-full bg-surface border-r border-border transition-[width] duration-200 ${isOpen ? 'w-60' : 'w-16'}`}
            >
                <div className="flex flex-col h-full">
                    <div className={`flex items-center h-14 shrink-0 border-b border-border ${isOpen ? 'px-3 justify-between' : 'px-2 justify-center'}`}>
                        {isOpen && (
                            <Link
                                href="/"
                                className="flex items-center gap-2.5 min-w-0 text-primary hover:opacity-80 transition-opacity"
                            >
                                <span className="brand-mark shrink-0">P</span>
                                <span className="font-display text-[15px] font-semibold tracking-tight truncate">Perioxia</span>
                            </Link>
                        )}
                        <button
                            onClick={() => setIsOpen(!isOpen)}
                            className="p-1.5 rounded-md hover:bg-surface-elevated text-muted hover:text-primary transition-colors"
                            aria-label={isOpen ? t('Collapse sidebar') : t('Expand sidebar')}
                        >
                            {isOpen ? <PanelLeftClose size={16} /> : <Menu size={16} />}
                        </button>
                    </div>

                    <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
                        {ledgerError && (
                            <div className="mb-3 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20">
                                <p className="text-xs text-red-600 dark:text-red-400 mb-2">{ledgerError}</p>
                                <button
                                    type="button"
                                    onClick={fetchLedgers}
                                    className="text-xs font-medium text-red-600 dark:text-red-400 hover:underline"
                                >
                                    Retry
                                </button>
                            </div>
                        )}
                        {navigation.map((item, idx) => {
                            // Category Header
                            if (item.category) {
                                return isOpen && (
                                    <div
                                        key={`cat-${idx}`}
                                        className="pt-3.5 pb-1 px-2.5 text-[11px] font-medium text-muted"
                                    >
                                        {t(item.category)}
                                    </div>
                                );
                            }

                            const isActive = pathname === item.href || (item.children && item.children.some(child => pathname === child.href));
                            const Icon = ICON_MAP[item.icon] || Activity;

                            return (
                                <NavItem
                                    key={item.name || `item-${idx}`}
                                    item={item}
                                    isActive={isActive}
                                    Icon={Icon}
                                    isOpen={isOpen}
                                    pathname={pathname}
                                    t={t}
                                    badge={
                                        item.name === 'Chat' && chatUnread > 0
                                            ? chatUnread
                                            : item.name === 'Unassigned Pool' && unassignedCount > 0
                                            ? unassignedCount
                                            : item.name === 'Appointments' && overdueCount > 0
                                                ? overdueCount
                                                : item.name === 'Conversations' && unansweredCount > 0
                                                    ? unansweredCount
                                                    : null
                                    }
                                />
                            );
                        })}
                    </nav>

                    {/* Footer */}
                    <div className="p-2 border-t border-border shrink-0">
                        <LanguageToggle isOpen={isOpen} />
                    </div>
                </div>
            </div>
        </>
    );
}

// Sub-component for individual items to handle toggle state cleanly
function NavItem({ item, isActive, Icon, isOpen, pathname, t = (s) => s, badge = null }) {
    const [expanded, setExpanded] = useState(isActive);
    const hasChildren = item.children && item.children.length > 0;
    const label = t(item.name);

    // Auto-expand if a child is active
    useEffect(() => {
        if (isActive && hasChildren) {
            setExpanded(true);
        }
    }, [isActive, hasChildren]);

    const handleClick = (e) => {
        if (hasChildren) {
            e.preventDefault(); // Prevent navigation if it has children, just toggle
            if (isOpen) setExpanded(!expanded);
        }
    };

    if (hasChildren) {
        return (
            <div className="mb-0.5">
                <button
                    onClick={handleClick}
                    className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-md transition-colors group ${isActive && !expanded
                        ? 'bg-accent-subtle text-accent'
                        : 'text-secondary hover:bg-surface-elevated hover:text-primary'
                        }`}
                >
                    <Icon
                        size={16}
                        strokeWidth={1.75}
                        className={`${isActive ? 'text-accent' : 'text-muted group-hover:text-primary'}`}
                    />
                    {isOpen && (
                        <>
                            <span className={`text-[13px] flex-1 text-left ${isActive ? 'font-semibold' : 'font-medium'}`}>
                                {label}
                            </span>
                            {/* Chevron */}
                            <svg
                                className={`w-3.5 h-3.5 transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
                                fill="none" viewBox="0 0 24 24" stroke="currentColor"
                            >
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                            </svg>
                        </>
                    )}
                </button>

                {/* Submenu (Refinement) */}
                {isOpen && expanded && (
                    <div className="mt-0.5 ml-4 pl-2.5 border-l border-border space-y-0.5">
                        {item.children.map(child => {
                            const isChildActive = pathname === child.href;
                            return (
                                <Link
                                    key={child.name}
                                    href={child.href}
                                    className={`block text-[13px] py-1.5 px-2 rounded-md transition-colors ${isChildActive
                                        ? 'text-accent font-medium bg-accent-subtle'
                                        : 'text-muted hover:text-primary hover:bg-surface-elevated'
                                        }`}
                                >
                                    {child.name}
                                </Link>
                            );
                        })}
                    </div>
                )}
            </div>
        );
    }

    return (
        <Link
            href={item.href}
            title={!isOpen ? label : undefined}
            className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-md transition-colors group ${isActive
                ? 'bg-accent-subtle text-accent'
                : 'text-secondary hover:bg-surface-elevated hover:text-primary'
                } ${!isOpen ? 'justify-center px-0' : ''}`}
        >
            <Icon
                size={16}
                strokeWidth={1.75}
                className={`${isActive ? 'text-accent' : 'text-muted group-hover:text-primary'}`}
            />
            {isOpen && (
                <span className={`text-[13px] flex-1 ${isActive ? 'font-semibold' : 'font-medium'}`}>
                    {label}
                </span>
            )}
            {isOpen && badge != null && (
                <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded bg-accent-subtle text-accent tabular-nums">
                    {badge}
                </span>
            )}
        </Link>
    );
}
