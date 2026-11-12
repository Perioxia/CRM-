'use client';

import { useState, useEffect } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import api from '../../../../services/api';
import {
    ArrowLeft,
    User,
    Mail,
    Phone,
    Shield,
    UsersRound,
    Calendar,
    Clock,
    CheckCircle,
    XCircle,
    RefreshCw,
    LogOut,
    AlertTriangle
} from 'lucide-react';

export default function UserDetailPage() {
    const router = useRouter();
    const params = useParams();
    const [loading, setLoading] = useState(true);
    const [user, setUser] = useState(null);
    const [error, setError] = useState(null);
    const [showDeactivateModal, setShowDeactivateModal] = useState(false);
    const [showRoleModal, setShowRoleModal] = useState(false);
    const [actionLoading, setActionLoading] = useState(false);

    // Form state
    const [selectedRole, setSelectedRole] = useState('');
    const [twilioPhone, setTwilioPhone] = useState('');
    const [twilioRingMobile, setTwilioRingMobile] = useState(true);
    const [savingTwilio, setSavingTwilio] = useState(false);

    const fetchUser = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.get(`/admin/users/${params.userId}`);
            const userData = res.data;
            setUser(userData);
            setSelectedRole(userData.role || '');
            setTwilioPhone(userData.twilio_phone_number || '');
            setTwilioRingMobile(userData.twilio_ring_mobile !== false && userData.twilio_ring_mobile !== 0);
        } catch (err) {
            console.error('Failed to fetch user detail', err);
            setError(err?.response?.data?.detail || err?.message || 'Failed to load user details.');
            setUser(null);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (params.userId) {
            fetchUser();
        }
    }, [params.userId]);

    const handleToggleStatus = () => {
        setShowDeactivateModal(true);
    };

    const confirmToggleStatus = async () => {
        setActionLoading(true);
        try {
            const newStatus = user.status === 'active' ? 'disabled' : 'active';
            await api.put(`/admin/users/${params.userId}`, { status: newStatus });
            setUser({ ...user, status: newStatus });
            setShowDeactivateModal(false);
        } catch (err) {
            console.error('Failed to update user status', err);
            alert(err?.response?.data?.detail || 'Failed to update user status.');
        } finally {
            setActionLoading(false);
        }
    };

    const confirmRoleChange = async () => {
        setActionLoading(true);
        try {
            await api.put(`/admin/users/${params.userId}`, { role: selectedRole });
            setUser({ ...user, role: selectedRole });
            setShowRoleModal(false);
        } catch (err) {
            console.error('Failed to update role', err);
            alert(err?.response?.data?.detail || 'Failed to update role.');
        } finally {
            setActionLoading(false);
        }
    };

    if (loading) return <DetailSkeleton />;

    if (error || !user) {
        return (
            <div className="mx-auto max-w-[1100px] space-y-6 pb-12 font-sans text-slate-900 dark:text-slate-100">
                <Link
                    href="/admin/users"
                    className="inline-flex items-center gap-2 text-[13px] font-medium text-slate-500 hover:text-indigo-600 transition-colors"
                >
                    <ArrowLeft size={16} />
                    Back to Users
                </Link>
                <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-12 text-center">
                    <AlertTriangle size={32} className="text-slate-400 mx-auto mb-4" />
                    <p className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-4">
                        {error || 'User not found.'}
                    </p>
                    <button
                        type="button"
                        onClick={fetchUser}
                        className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg"
                    >
                        Retry
                    </button>
                </div>
            </div>
        );
    }

    const isActive = user.status === 'active';

    return (
        <div className="mx-auto max-w-[1100px] space-y-6 pb-12 font-sans text-slate-900 dark:text-slate-100">

            {/* Back Link */}
            <Link
                href="/admin/users"
                className="inline-flex items-center gap-2 text-[13px] font-medium text-slate-500 hover:text-indigo-600 transition-colors"
            >
                <ArrowLeft size={16} />
                Back to Users
            </Link>

            {/* Identity Strip */}
            <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-6">
                <div className="flex items-start justify-between">
                    <div className="flex items-start gap-5">
                        <div className="w-16 h-16 rounded-full bg-indigo-100 dark:bg-indigo-900/30 flex items-center justify-center">
                            <User size={28} className="text-indigo-600 dark:text-indigo-400" />
                        </div>
                        <div>
                            <div className="flex items-center gap-3 mb-1">
                                <h1 className="text-[24px] font-semibold text-slate-900 dark:text-white">{user.full_name || user.name || 'Unknown'}</h1>
                                <StatusBadge status={user.status} />
                            </div>
                            <p className="text-[13px] font-mono text-slate-500">ID: {user.id}</p>
                            <div className="flex items-center gap-4 mt-3 text-[13px] text-slate-600 dark:text-slate-400">
                                <span className="flex items-center gap-1.5"><Mail size={14} /> {user.email}</span>
                                {user.phone && <span className="flex items-center gap-1.5"><Phone size={14} /> {user.phone}</span>}
                            </div>
                        </div>
                    </div>
                    <div className="text-right">
                        <RoleBadge role={user.role} />
                        {user.team_name && (
                            <p className="text-[13px] text-slate-500 mt-2">Team: {user.team_name}</p>
                        )}
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-12 gap-6">

                {/* Controls Panel */}
                <div className="col-span-12 lg:col-span-5 space-y-5">

                    {/* Role Controls */}
                    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                        <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white mb-4">Role Assignment</h3>
                        <div className="space-y-4">
                            <div>
                                <label className="block text-[12px] font-medium text-slate-500 mb-1">Current Role</label>
                                <div className="flex items-center justify-between p-3 bg-slate-50 dark:bg-slate-700/50 rounded-lg">
                                    <span className="text-[14px] font-medium text-slate-800 dark:text-slate-200">{user.role}</span>
                                    <button
                                        onClick={() => { setSelectedRole(user.role); setShowRoleModal(true); }}
                                        className="text-[12px] font-medium text-indigo-600 hover:text-indigo-700 dark:text-indigo-400"
                                    >
                                        Change
                                    </button>
                                </div>
                            </div>
                            {user.team_name && (
                                <div>
                                    <label className="block text-[12px] font-medium text-slate-500 mb-1">Current Team</label>
                                    <div className="p-3 bg-slate-50 dark:bg-slate-700/50 rounded-lg">
                                        <span className="text-[14px] font-medium text-slate-800 dark:text-slate-200">{user.team_name}</span>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                        <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white mb-4">Twilio softphone</h3>
                        <div className="space-y-3">
                            <label className="block text-[12px] font-medium text-slate-500">
                                Personal Twilio number
                                <input
                                    value={twilioPhone}
                                    onChange={(e) => setTwilioPhone(e.target.value)}
                                    placeholder="optional"
                                    className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600"
                                />
                            </label>
                            <label className="flex items-center gap-2 text-[12px] text-slate-600 dark:text-slate-300">
                                <input
                                    type="checkbox"
                                    checked={twilioRingMobile}
                                    onChange={(e) => setTwilioRingMobile(e.target.checked)}
                                />
                                Also ring mobile for inbound
                            </label>
                            <button
                                type="button"
                                disabled={savingTwilio}
                                onClick={async () => {
                                    setSavingTwilio(true);
                                    try {
                                        await api.put(`/admin/users/${params.userId}`, {
                                            twilio_phone_number: twilioPhone,
                                            twilio_ring_mobile: twilioRingMobile,
                                        });
                                        setUser({
                                            ...user,
                                            twilio_phone_number: twilioPhone || null,
                                            twilio_ring_mobile: twilioRingMobile,
                                        });
                                    } catch (err) {
                                        alert(err?.response?.data?.detail || 'Failed to save Twilio settings.');
                                    } finally {
                                        setSavingTwilio(false);
                                    }
                                }}
                                className="w-full px-3 py-2 text-sm font-medium rounded-lg bg-slate-900 text-white disabled:opacity-50"
                            >
                                {savingTwilio ? 'Saving…' : 'Save softphone'}
                            </button>
                        </div>
                    </div>

                    {/* Lifecycle Controls */}
                    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                        <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white mb-4">Account Controls</h3>
                        <div className="space-y-3">
                            <button
                                onClick={handleToggleStatus}
                                className={`w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg font-medium transition-colors ${isActive
                                    ? 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30'
                                    : 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-100 dark:hover:bg-emerald-900/30'
                                    }`}
                            >
                                {isActive ? <XCircle size={18} /> : <CheckCircle size={18} />}
                                {isActive ? 'Deactivate User' : 'Activate User'}
                            </button>
                        </div>
                    </div>
                </div>

                {/* User Info */}
                <div className="col-span-12 lg:col-span-7">
                    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                        <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white mb-4">Account Information</h3>
                        <div className="grid grid-cols-2 gap-4 text-[13px]">
                            <div>
                                <span className="text-slate-500">Created</span>
                                <p className="font-medium text-slate-800 dark:text-slate-200">
                                    {user.created_at ? new Date(user.created_at).toLocaleDateString() : '—'}
                                </p>
                            </div>
                            <div>
                                <span className="text-slate-500">Last Login</span>
                                <p className="font-medium text-slate-800 dark:text-slate-200">
                                    {user.last_login ? new Date(user.last_login).toLocaleString() : '—'}
                                </p>
                            </div>
                            <div>
                                <span className="text-slate-500">Active</span>
                                <p className="font-medium text-slate-800 dark:text-slate-200">
                                    {user.is_active ? 'Yes' : 'No'}
                                </p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Deactivate/Activate Modal */}
            {showDeactivateModal && (
                <ConfirmModal
                    title={isActive ? 'Deactivate User' : 'Activate User'}
                    message={`Are you sure you want to ${isActive ? 'deactivate' : 'activate'} ${user.full_name || user.email}?`}
                    confirmLabel={isActive ? 'Deactivate' : 'Activate'}
                    confirmColor={isActive ? 'red' : 'emerald'}
                    onConfirm={confirmToggleStatus}
                    onCancel={() => setShowDeactivateModal(false)}
                    loading={actionLoading}
                />
            )}

            {/* Role Change Modal */}
            {showRoleModal && (
                <div className="fixed inset-0 z-50 flex items-center justify-center">
                    <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={() => setShowRoleModal(false)}></div>
                    <div className="relative bg-white dark:bg-slate-900 rounded-xl shadow-xl p-6 w-full max-w-md">
                        <h3 className="text-[18px] font-semibold text-slate-900 dark:text-white mb-4">Change Role</h3>
                        <select
                            value={selectedRole}
                            onChange={(e) => setSelectedRole(e.target.value)}
                            className="w-full px-3 py-2.5 border border-slate-200 dark:border-slate-700 rounded-lg text-[14px] bg-white dark:bg-slate-800 mb-4"
                        >
                            <option value="sales">Sales Executive</option>
                            <option value="manager">Manager</option>
                            <option value="md">Managing Director</option>
                            <option value="purchase">Purchase</option>
                            <option value="admin">Admin</option>
                        </select>
                        <div className="flex gap-3">
                            <button onClick={() => setShowRoleModal(false)} className="flex-1 px-4 py-2.5 bg-slate-100 dark:bg-slate-800 rounded-lg font-medium">Cancel</button>
                            <button onClick={confirmRoleChange} disabled={actionLoading} className="flex-1 px-4 py-2.5 bg-indigo-500 text-white rounded-lg font-medium disabled:opacity-50">
                                {actionLoading ? 'Saving...' : 'Save'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function StatusBadge({ status }) {
    if (status === 'active') {
        return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 rounded text-[11px] font-bold uppercase">
                <CheckCircle size={12} /> Active
            </span>
        );
    }
    return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400 rounded text-[11px] font-bold uppercase">
            <XCircle size={12} /> {status || 'Inactive'}
        </span>
    );
}

function RoleBadge({ role }) {
    const colors = {
        'sales': 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
        'manager': 'bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
        'md': 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
        'purchase': 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
        'admin': 'bg-rose-50 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400'
    };

    return (
        <span className={`inline-flex px-2.5 py-1 rounded text-[12px] font-medium ${colors[role] || 'bg-slate-100 text-slate-600'}`}>
            {role}
        </span>
    );
}

function ConfirmModal({ title, message, confirmLabel, confirmColor, onConfirm, onCancel, loading }) {
    const colorClasses = {
        red: 'bg-red-500 hover:bg-red-600',
        emerald: 'bg-emerald-500 hover:bg-emerald-600',
        indigo: 'bg-indigo-500 hover:bg-indigo-600'
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onCancel}></div>
            <div className="relative bg-white dark:bg-slate-900 rounded-xl shadow-xl p-6 w-full max-w-md">
                <h3 className="text-[18px] font-semibold text-slate-900 dark:text-white mb-2">{title}</h3>
                <p className="text-[14px] text-slate-600 dark:text-slate-400 mb-6">{message}</p>
                <div className="flex gap-3">
                    <button onClick={onCancel} className="flex-1 px-4 py-2.5 bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 rounded-lg font-medium">Cancel</button>
                    <button onClick={onConfirm} disabled={loading} className={`flex-1 px-4 py-2.5 text-white rounded-lg font-medium disabled:opacity-50 ${colorClasses[confirmColor]}`}>
                        {loading ? 'Processing...' : confirmLabel}
                    </button>
                </div>
            </div>
        </div>
    );
}

function DetailSkeleton() {
    return (
        <div className="mx-auto max-w-[1100px] space-y-6 animate-pulse">
            <div className="h-4 w-24 bg-slate-200 dark:bg-slate-800 rounded"></div>
            <div className="h-[130px] bg-slate-200 dark:bg-slate-800 rounded-xl"></div>
            <div className="grid grid-cols-12 gap-6">
                <div className="col-span-5 space-y-5">
                    <div className="h-[200px] bg-slate-200 dark:bg-slate-800 rounded-xl"></div>
                    <div className="h-[180px] bg-slate-200 dark:bg-slate-800 rounded-xl"></div>
                </div>
                <div className="col-span-7 h-[300px] bg-slate-200 dark:bg-slate-800 rounded-xl"></div>
            </div>
        </div>
    );
}
