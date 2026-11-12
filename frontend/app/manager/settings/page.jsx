'use client';

import { useState, useEffect } from 'react';
import api from '../../../services/api';
import NotificationPreferencesPanel from '../../../components/shared/NotificationPreferencesPanel';
import Link from 'next/link';
import {
    Users,
    Shield,
    FileText,
    Building2,
    ArrowUpRight,
    Bell,
    Phone
} from 'lucide-react';

export default function ManagerSettingsPage() {
    const [teamData, setTeamData] = useState(null);
    const [requests, setRequests] = useState([]);
    const [loading, setLoading] = useState(true);
    const [view, setView] = useState('overview'); // 'overview' | 'requests'

    useEffect(() => {
        const fetchTeamData = async () => {
            try {
                setLoading(true);
                // Use the monitoring endpoint to get team members
                const res = await api.get('/manager/monitoring');
                const monitoringData = res.data;

                // Map monitoring data to the structure expected by the page
                const mappedTeamData = {
                    name: monitoringData.team_name || 'My Sales Team',
                    id: 'TEAM-001',
                    tier: 'Tier 1 - Standard',
                    manager: { name: 'Current Manager', role: 'Team Manager' },
                    members: monitoringData.team_members?.map(m => ({
                        id: m.id,
                        name: m.name,
                        role: m.role,
                        performance: 'Consistent', // Backend doesn't provide this yet
                        joined: '2024-01-01'
                    })) || [],
                    openRoles: 0
                };

                setTeamData(mappedTeamData);
                // Requests endpoint doesn't exist yet, so we'll just keep it empty for now
                setRequests([]);
            } catch (error) {
                console.error("Failed to fetch team settings", error);
            } finally {
                setLoading(false);
            }
        };
        fetchTeamData();
    }, []);

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-slate-50 dark:bg-slate-900">
                <div className="w-6 h-6 border-2 border-slate-300 border-t-blue-600 rounded-full animate-spin"></div>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-slate-50 dark:bg-slate-900 font-sans text-slate-900 dark:text-slate-100">

            {/* HEADER */}
            <div className="bg-white dark:bg-slate-800 border-b border-slate-200 dark:border-slate-700 px-6 py-8">
                <div className="max-w-5xl mx-auto">
                    <h1 className="text-2xl font-bold text-slate-900 dark:text-white flex items-center gap-3">
                        <Users className="text-blue-600" size={28} />
                        Team Administration
                    </h1>
                    <p className="text-slate-500 dark:text-slate-400 mt-2 max-w-2xl">
                        Manage team operations and view hierarchy. Structure changes require Admin approval.
                        Team change requests are currently read-only until backend request APIs are connected.
                    </p>
                </div>
            </div>

            <div className="max-w-5xl mx-auto px-6 py-8 space-y-8">

                <Link
                    href="/settings/telephony"
                    className="flex items-center justify-between p-4 rounded-xl border border-emerald-200 dark:border-emerald-800 bg-emerald-50/50 dark:bg-emerald-950/20 hover:bg-emerald-50 dark:hover:bg-emerald-950/40 transition-colors"
                >
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-full bg-emerald-100 dark:bg-emerald-900/40 flex items-center justify-center text-emerald-700 dark:text-emerald-300">
                            <Phone size={18} />
                        </div>
                        <div>
                            <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Softphone / Telephony</h3>
                            <p className="text-xs text-slate-500">Browser calling prefs and ring-mobile settings</p>
                        </div>
                    </div>
                    <span className="text-slate-400">→</span>
                </Link>

                {/* TOP CARDS: INFO & STATUS */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    {/* Card 1: Team Identity */}
                    <div className="bg-white dark:bg-slate-800 p-6 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm relative overflow-hidden group">
                        <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
                            <Building2 size={64} />
                        </div>
                        <p className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">My Team</p>
                        <h3 className="text-lg font-bold text-slate-900 dark:text-white">{teamData?.name}</h3>
                        <div className="flex items-center gap-2 mt-2">
                            <span className="px-2 py-0.5 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 text-xs font-bold rounded border border-blue-100 dark:border-blue-800">
                                {teamData?.tier}
                            </span>
                            <span className="text-xs text-slate-500">ID: {teamData?.id}</span>
                        </div>
                    </div>

                    {/* Card 2: Manager Profile */}
                    <div className="bg-white dark:bg-slate-800 p-6 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm">
                        <p className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Team Lead</p>
                        <div className="flex items-center gap-3">
                            <div className="w-10 h-10 rounded-full bg-slate-100 dark:bg-slate-700 flex items-center justify-center text-slate-600 dark:text-slate-300 font-bold">
                                {teamData?.manager?.name.charAt(0)}
                            </div>
                            <div>
                                <h3 className="text-sm font-bold text-slate-900 dark:text-white">{teamData?.manager?.name}</h3>
                                <p className="text-xs text-slate-500">{teamData?.manager?.role}</p>
                            </div>
                        </div>
                    </div>

                    {/* Card 3: Action Required */}
                    <div className="bg-white dark:bg-slate-800 p-6 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm">
                        <p className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Pending Requests</p>
                        <div className="flex items-center justify-between">
                            <h3 className="text-2xl font-bold text-slate-900 dark:text-white">
                                {requests.filter(r => r.status === 'Pending').length}
                            </h3>
                            <button
                                onClick={() => setView('requests')}
                                className="text-xs font-semibold text-blue-600 hover:text-blue-700 flex items-center gap-1"
                            >
                                View History <ArrowUpRight size={12} />
                            </button>
                        </div>
                        <p className="text-xs text-slate-500 mt-1">History view only (submission disabled)</p>
                    </div>
                </div>

                {/* MAIN CONTENT TABS */}
                <div className="flex items-center gap-6 border-b border-slate-200 dark:border-slate-700">
                    <button
                        onClick={() => setView('overview')}
                        className={`pb-3 text-sm font-medium transition-colors relative ${view === 'overview'
                            ? 'text-blue-600 dark:text-blue-400'
                            : 'text-slate-500 hover:text-slate-700 dark:text-slate-400'
                            }`}
                    >
                        Team Roster
                        {view === 'overview' && <div className="absolute bottom-0 left-0 w-full h-0.5 bg-blue-600 rounded-t-full"></div>}
                    </button>
                    <button
                        onClick={() => setView('requests')}
                        className={`pb-3 text-sm font-medium transition-colors relative ${view === 'requests'
                            ? 'text-blue-600 dark:text-blue-400'
                            : 'text-slate-500 hover:text-slate-700 dark:text-slate-400'
                            }`}
                    >
                        Change Requests
                        <span className="ml-2 inline-flex px-1.5 py-0.5 rounded text-[10px] font-bold uppercase bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300">
                            Read Only
                        </span>
                        {view === 'requests' && <div className="absolute bottom-0 left-0 w-full h-0.5 bg-blue-600 rounded-t-full"></div>}
                    </button>
                </div>

                {/* VIEW: TEAM ROSTER */}
                {view === 'overview' && (
                    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm overflow-hidden">
                        <div className="px-6 py-4 bg-slate-50/50 dark:bg-slate-800 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
                            <h3 className="text-sm font-bold text-slate-800 dark:text-white flex items-center gap-2">
                                <Users size={16} className="text-slate-400" />
                                Current Members
                            </h3>
                            <span className="text-xs font-medium text-slate-500 bg-slate-100 dark:bg-slate-700 px-2 py-1 rounded">
                                {teamData?.members.length} Active / {teamData?.openRoles} Open
                            </span>
                        </div>
                        <table className="w-full text-left text-sm">
                            <thead className="bg-slate-50 dark:bg-slate-700/50 border-b border-slate-200 dark:border-slate-700 text-slate-500 font-medium uppercase tracking-wider text-[11px]">
                                <tr>
                                    <th className="px-6 py-3">User</th>
                                    <th className="px-6 py-3">Role</th>
                                    <th className="px-6 py-3">Performance Band</th>
                                    <th className="px-6 py-3">Joined Team</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                                {teamData?.members.map((member) => (
                                    <tr key={member.id} className="group hover:bg-slate-50 dark:hover:bg-slate-700/30 transition-colors">
                                        <td className="px-6 py-4 font-medium text-slate-900 dark:text-white">
                                            {member.name}
                                        </td>
                                        <td className="px-6 py-4 text-slate-600 dark:text-slate-300">
                                            {member.role}
                                        </td>
                                        <td className="px-6 py-4">
                                            <span className={`inline-flex px-2 py-0.5 text-[11px] font-bold rounded ${member.performance.includes('Top')
                                                ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-400'
                                                : 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300'
                                                }`}>
                                                {member.performance}
                                            </span>
                                        </td>
                                        <td className="px-6 py-4 text-slate-500 text-xs font-mono">
                                            {member.joined}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}

                {/* VIEW: REQUESTS */}
                {view === 'requests' && (
                    <div className="space-y-6">

                        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/20 dark:text-amber-300">
                            Team change request submission is temporarily read-only. This UI is disabled until the backend request API is fully connected.
                        </div>

                        {/* Request History List */}
                        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm overflow-hidden">
                            <div className="px-6 py-4 bg-slate-50/50 dark:bg-slate-800 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
                                <h3 className="text-sm font-bold text-slate-800 dark:text-white flex items-center gap-2">
                                    <FileText size={16} className="text-slate-400" />
                                    Request History
                                </h3>
                            </div>
                            <div className="divide-y divide-slate-100 dark:divide-slate-700">
                                {requests.length > 0 ? requests.map((req) => (
                                    <div key={req.id} className="p-6 hover:bg-slate-50 dark:hover:bg-slate-700/30 transition-colors">
                                        <div className="flex items-start justify-between mb-2">
                                            <div className="flex items-center gap-3">
                                                <span className={`px-2 py-1 rounded text-[10px] font-bold uppercase ${req.status === 'Approved' ? 'bg-emerald-100 text-emerald-700' :
                                                    req.status === 'Rejected' ? 'bg-red-100 text-red-700' :
                                                        'bg-amber-100 text-amber-700'
                                                    }`}>
                                                    {req.status}
                                                </span>
                                                <span className="text-xs text-slate-400 font-mono">{req.date}</span>
                                            </div>
                                            <span className="text-xs font-bold text-slate-500 uppercase tracking-wide">{req.type.replace('_', ' ')}</span>
                                        </div>
                                        <h4 className="text-sm font-bold text-slate-900 dark:text-white mb-1">
                                            Target: {req.target}
                                        </h4>
                                        <p className="text-xs text-slate-600 dark:text-slate-400 mb-3 bg-slate-50 dark:bg-slate-900/50 p-2 rounded border border-slate-100 dark:border-slate-700">
                                            "{req.notes}"
                                        </p>
                                        {req.adminResponse && (
                                            <div className="flex items-start gap-2 text-xs text-slate-500 mt-2 pl-2 border-l-2 border-slate-200 dark:border-slate-600">
                                                <Shield size={12} className="mt-0.5" />
                                                <span>Admin: {req.adminResponse}</span>
                                            </div>
                                        )}
                                    </div>
                                )) : (
                                    <div className="p-12 text-center text-slate-400 text-sm">
                                        No request history found.
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                )}

                <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm p-6">
                    <div className="flex items-center gap-2 mb-4">
                        <Bell size={16} className="text-slate-500" />
                        <h3 className="text-sm font-bold text-slate-900 dark:text-white">Personal Notification Preferences</h3>
                    </div>
                    <NotificationPreferencesPanel />
                </div>

            </div>
        </div>
    );
}
