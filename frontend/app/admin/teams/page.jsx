'use client';

import { useState, useEffect } from 'react';
import api from '@/services/api';
import {
    UsersRound,
    User,
    ChevronRight,
    Plus,
    X,
    ArrowRight,
    Shield,
    RefreshCw,
    Check
} from 'lucide-react';
import { useNotification } from '@/contexts/NotificationContext';
import Skeleton, { TableRowSkeleton, CardSkeleton } from '@/components/shared/Skeleton';

export default function AdminTeamsPage() {
    const [loading, setLoading] = useState(true);
    const [teams, setTeams] = useState([]);
    const [allUsers, setAllUsers] = useState([]);
    const [selectedTeam, setSelectedTeam] = useState(null);
    const [showAddMemberModal, setShowAddMemberModal] = useState(false);
    const [showRemoveMemberModal, setShowRemoveMemberModal] = useState(false);
    const [showChangeManagerModal, setShowChangeManagerModal] = useState(false);
    const [showCreateTeamModal, setShowCreateTeamModal] = useState(false);
    const [memberToRemove, setMemberToRemove] = useState(null);

    // Form state
    const [newTeamName, setNewTeamName] = useState('');
    const [selectedMemberIds, setSelectedMemberIds] = useState([]);
    const [memberQuery, setMemberQuery] = useState('');
    const [selectedNewManager, setSelectedNewManager] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);
    const { showToast } = useNotification();

    const fetchTeams = async () => {
        try {
            setLoading(true);
            const [teamsRes, usersRes] = await Promise.all([
                api.get('/admin/teams'),
                api.get('/admin/users')
            ]);
            const teamsData = teamsRes.data.teams || [];
            setTeams(teamsData);
            setAllUsers(usersRes.data.users || []);

            if (teamsData.length > 0 && !selectedTeam) {
                fetchTeamDetail(teamsData[0].id);
            }
        } catch (err) {
            console.error('Failed to fetch teams', err);
            showToast('Unable to synchronize teams list', 'error');
        } finally {
            setLoading(false);
        }
    };

    const fetchTeamDetail = async (teamId) => {
        try {
            const res = await api.get(`/admin/teams/${teamId}`);
            setSelectedTeam(res.data);
        } catch (err) {
            console.error('Failed to fetch team detail', err);
        }
    };

    useEffect(() => {
        fetchTeams();
    }, []);

    const currentMemberIds = new Set(
        (selectedTeam?.members || []).map((member) => Number(member.user_id ?? member.id))
    );
    const availableMembers = allUsers.filter((user) => {
        const id = Number(user.user_id);
        if (!id || currentMemberIds.has(id)) return false;
        const status = String(user.status || '').toLowerCase();
        return status !== 'disabled' && status !== 'inactive';
    });
    const memberSearch = memberQuery.trim().toLowerCase();
    const visibleMembers = availableMembers.filter((user) => {
        if (!memberSearch) return true;
        return `${user.name || ''} ${user.role || ''}`.toLowerCase().includes(memberSearch);
    });
    const availableManagers = allUsers.filter(u => u.role === 'Manager' || u.role === 'manager');

    const toggleMember = (userId) => {
        setSelectedMemberIds((current) =>
            current.includes(userId) ? current.filter((id) => id !== userId) : [...current, userId]
        );
    };

    const handleRemoveMember = (member) => {
        setMemberToRemove(member);
        setShowRemoveMemberModal(true);
    };

    const confirmRemoveMember = async () => {
        if (isSubmitting) return;
        try {
            setIsSubmitting(true);
            await api.delete(`/admin/teams/${selectedTeam.id}/members/${memberToRemove.user_id || memberToRemove.id}`);
            fetchTeamDetail(selectedTeam.id);
            fetchTeams();
            setShowRemoveMemberModal(false);
            setMemberToRemove(null);
        } catch (err) {
            console.error('Remove member failed', err);
            const detail = err.response?.data?.detail;
            alert(typeof detail === 'object' ? JSON.stringify(detail) : detail || 'Removal failed');
        } finally {
            setIsSubmitting(false);
        }
    };

    const confirmAddMember = async () => {
        if (selectedMemberIds.length === 0 || isSubmitting) return;
        try {
            setIsSubmitting(true);
            const added = [];
            let errorDetail = null;
            for (const userId of selectedMemberIds) {
                try {
                    await api.post(`/admin/teams/${selectedTeam.id}/members`, { user_id: userId });
                    added.push(userId);
                } catch (err) {
                    console.error('Add member failed', err);
                    const detail = err.response?.data?.detail;
                    errorDetail = typeof detail === 'object' ? JSON.stringify(detail) : detail || 'Addition failed';
                    break;
                }
            }
            fetchTeamDetail(selectedTeam.id);
            fetchTeams();
            if (errorDetail) {
                setSelectedMemberIds((current) => current.filter((id) => !added.includes(id)));
                alert(errorDetail);
            } else {
                setShowAddMemberModal(false);
                setSelectedMemberIds([]);
                setMemberQuery('');
            }
        } finally {
            setIsSubmitting(false);
        }
    };

    const confirmChangeManager = async () => {
        if (isSubmitting) return;
        try {
            setIsSubmitting(true);
            // Changing manager usually means adding them to team or updating team record
            // The backend admin.py uses PUT /admin/users/{user_id} to set team_id
            await api.put(`/admin/users/${selectedNewManager}`, { team_id: selectedTeam.id, role: 'manager' });
            fetchTeamDetail(selectedTeam.id);
            fetchTeams();
            setShowChangeManagerModal(false);
            setSelectedNewManager('');
        } catch (err) {
            console.error('Change manager failed', err);
            const detail = err.response?.data?.detail;
            alert(typeof detail === 'object' ? JSON.stringify(detail) : detail || 'Update failed');
        } finally {
            setIsSubmitting(false);
        }
    };

    const confirmCreateTeam = async () => {
        if (!newTeamName.trim() || isSubmitting) return;
        try {
            setIsSubmitting(true);
            await api.post('/admin/teams', { name: newTeamName });
            showToast(`Team "${newTeamName}" created successfully`, 'success');
            setNewTeamName('');
            setShowCreateTeamModal(false);
            fetchTeams();
        } catch (err) {
            console.error('Create team failed', err);
            const detail = err.response?.data?.detail;
            showToast(typeof detail === 'object' ? JSON.stringify(detail) : detail || 'Creation failed', 'error');
        } finally {
            setIsSubmitting(false);
        }
    };

    if (loading) return <TeamsSkeleton />;

    return (
        <div className="mx-auto max-w-[1360px] space-y-6 pb-12 font-sans text-slate-900 dark:text-slate-100">

            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-[28px] font-semibold tracking-tight text-slate-900 dark:text-white leading-tight">Teams</h1>
                    <p className="text-[15px] text-slate-500 dark:text-slate-400 font-medium mt-0.5">Manage team composition and membership.</p>
                </div>
                <button
                    onClick={() => setShowCreateTeamModal(true)}
                    className="flex items-center gap-2 px-4 py-2.5 bg-indigo-500 text-white rounded-lg font-medium hover:bg-indigo-600 transition-colors"
                >
                    <Plus size={16} />
                    Create Team
                </button>
            </div>

            {/* Teams Grid */}
            <div className="grid grid-cols-12 gap-6">

                {/* Team List */}
                <div className="col-span-12 lg:col-span-4 bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden">
                    <div className="px-5 py-4 border-b border-slate-100 dark:border-slate-700/50">
                        <h3 className="text-[14px] font-semibold text-slate-900 dark:text-white">{teams.length} Teams</h3>
                    </div>
                    <div className="divide-y divide-slate-50 dark:divide-slate-700/50">
                        {teams.map((team) => (
                            <div
                                key={team.id}
                                onClick={() => fetchTeamDetail(team.id)}
                                className={`flex items-center justify-between px-5 py-4 cursor-pointer transition-colors ${selectedTeam?.id === team.id
                                    ? 'bg-indigo-50 dark:bg-indigo-900/20 border-l-2 border-indigo-500'
                                    : 'hover:bg-slate-50 dark:hover:bg-slate-800/80'
                                    }`}
                            >
                                <div className="flex items-center gap-3">
                                    <div className="w-10 h-10 rounded-lg bg-indigo-100 dark:bg-indigo-900/30 flex items-center justify-center">
                                        <UsersRound size={18} className="text-indigo-600 dark:text-indigo-400" />
                                    </div>
                                    <div>
                                        <div className="text-[13px] font-semibold text-slate-800 dark:text-white">{team.name}</div>
                                        <div className="text-[11px] text-slate-400">{team.member_count} members</div>
                                    </div>
                                </div>
                                <ChevronRight size={16} className="text-slate-300" />
                            </div>
                        ))}
                    </div>
                </div>

                {/* Team Details */}
                {selectedTeam && (
                    <div className="col-span-12 lg:col-span-8 space-y-5">

                        {/* Manager */}
                        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                            <div className="flex items-center justify-between mb-4">
                                <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white">{selectedTeam.name}</h3>
                            </div>
                            <div className="flex items-center justify-between p-4 bg-slate-50 dark:bg-slate-700/50 rounded-lg">
                                <div className="flex items-center gap-3">
                                    <Shield size={18} className="text-purple-500" />
                                    <div>
                                        <div className="text-[11px] uppercase tracking-wider text-slate-400 font-medium">Team Manager</div>
                                        <div className="text-[14px] font-semibold text-slate-800 dark:text-slate-200">
                                            {selectedTeam.manager?.name || 'No manager assigned'}
                                        </div>
                                    </div>
                                </div>
                                <button
                                    onClick={() => { setSelectedNewManager(selectedTeam.managerId || ''); setShowChangeManagerModal(true); }}
                                    className="text-[12px] font-medium text-indigo-600 hover:text-indigo-700 dark:text-indigo-400"
                                >
                                    Change
                                </button>
                            </div>
                        </div>

                        {/* Members */}
                        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                            <div className="flex items-center justify-between mb-4">
                                <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white">Team Members ({selectedTeam.members.length})</h3>
                                <button
                                    onClick={() => { setSelectedMemberIds([]); setMemberQuery(''); setShowAddMemberModal(true); }}
                                    className="flex items-center gap-1 text-[12px] font-medium text-indigo-600 hover:text-indigo-700 dark:text-indigo-400"
                                >
                                    <Plus size={14} /> Add Member
                                </button>
                            </div>
                            <div className="space-y-2">
                                {selectedTeam.members.map((member) => (
                                    <div key={member.id} className="flex items-center justify-between p-3 bg-slate-50 dark:bg-slate-700/50 rounded-lg">
                                        <div className="flex items-center gap-3">
                                            <div className="w-8 h-8 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center">
                                                <User size={14} className="text-blue-600 dark:text-blue-400" />
                                            </div>
                                            <div>
                                                <div className="text-[13px] font-medium text-slate-800 dark:text-slate-200">{member.name}</div>
                                                <div className="text-[11px] text-slate-400">{member.id} | {member.role}</div>
                                            </div>
                                        </div>
                                        <button
                                            onClick={() => handleRemoveMember(member)}
                                            className="p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors"
                                            title="Remove from team"
                                        >
                                            <X size={14} />
                                        </button>
                                    </div>
                                ))}
                                {selectedTeam.members.length === 0 && (
                                    <div className="py-8 text-center text-slate-400 text-[13px]">No members in this team</div>
                                )}
                            </div>
                        </div>
                    </div>
                )}
            </div>

            {/* Create Team Modal */}
            {showCreateTeamModal && (
                <Modal title="Create Team" onClose={() => setShowCreateTeamModal(false)}>
                    <div className="mb-4">
                        <label className="block text-[12px] font-medium text-slate-500 mb-1">Team Name</label>
                        <input
                            type="text"
                            value={newTeamName}
                            onChange={(e) => setNewTeamName(e.target.value)}
                            className="w-full px-3 py-2.5 border border-slate-200 dark:border-slate-700 rounded-lg text-[14px] bg-white dark:bg-slate-800"
                            placeholder="Enter team name..."
                        />
                    </div>
                    <div className="flex gap-3">
                        <button onClick={() => setShowCreateTeamModal(false)} className="flex-1 px-4 py-2.5 bg-slate-100 dark:bg-slate-800 rounded-lg font-medium">Cancel</button>
                        <button onClick={confirmCreateTeam} disabled={!newTeamName.trim() || isSubmitting} className="flex-1 px-4 py-2.5 bg-indigo-500 text-white rounded-lg font-medium disabled:opacity-50">
                            {isSubmitting ? 'Creating...' : 'Create'}
                        </button>
                    </div>
                </Modal>
            )}

            {/* Add Member Modal */}
            {showAddMemberModal && (
                <Modal title="Add Member" onClose={() => setShowAddMemberModal(false)}>
                    <div className="mb-4">
                        <input
                            type="text"
                            value={memberQuery}
                            onChange={(e) => setMemberQuery(e.target.value)}
                            placeholder="Search people"
                            className="w-full px-3 py-2.5 border border-slate-200 dark:border-slate-700 rounded-lg text-[14px] bg-white dark:bg-slate-800 mb-2"
                        />
                        <div className="max-h-72 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700">
                            {visibleMembers.map((member) => {
                                const checked = selectedMemberIds.includes(member.user_id);
                                const initials = String(member.name || '?')
                                    .split(' ')
                                    .filter(Boolean)
                                    .slice(0, 2)
                                    .map((part) => part[0])
                                    .join('')
                                    .toUpperCase();
                                return (
                                    <button
                                        key={member.user_id}
                                        type="button"
                                        onClick={() => toggleMember(member.user_id)}
                                        aria-pressed={checked}
                                        className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-slate-50 dark:hover:bg-slate-800/80"
                                    >
                                        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[12px] font-semibold text-indigo-600 dark:bg-indigo-900/40 dark:text-indigo-300">
                                            {initials}
                                        </div>
                                        <div className="min-w-0 flex-1">
                                            <div className="truncate text-[14px] font-medium text-slate-800 dark:text-slate-100">{member.name}</div>
                                            <div className="text-[12px] text-slate-400">{member.role}</div>
                                        </div>
                                        <span
                                            className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 ${checked
                                                ? 'border-indigo-500 bg-indigo-500 text-white'
                                                : 'border-slate-300 dark:border-slate-600'
                                                }`}
                                        >
                                            {checked && <Check size={12} strokeWidth={3} />}
                                        </span>
                                    </button>
                                );
                            })}
                            {visibleMembers.length === 0 && (
                                <p className="px-3 py-8 text-center text-[13px] text-slate-400">
                                    {availableMembers.length === 0
                                        ? (allUsers.length === 0 ? 'No people found in this company.' : 'Everyone is already on this team.')
                                        : 'No matches.'}
                                </p>
                            )}
                        </div>
                    </div>
                    <div className="flex gap-3">
                        <button onClick={() => setShowAddMemberModal(false)} className="flex-1 px-4 py-2.5 bg-slate-100 dark:bg-slate-800 rounded-lg font-medium">Cancel</button>
                        <button onClick={confirmAddMember} disabled={selectedMemberIds.length === 0 || isSubmitting} className="flex-1 px-4 py-2.5 bg-indigo-500 text-white rounded-lg font-medium disabled:opacity-50">
                            {isSubmitting ? 'Adding...' : selectedMemberIds.length > 1 ? `Add ${selectedMemberIds.length}` : 'Add'}
                        </button>
                    </div>
                </Modal>
            )}

            {/* Change Manager Modal */}
            {showChangeManagerModal && (
                <Modal title="Change Manager" onClose={() => setShowChangeManagerModal(false)}>
                    <div className="mb-4">
                        <label className="block text-[12px] font-medium text-slate-500 mb-1">Select Manager</label>
                        <select
                            value={selectedNewManager}
                            onChange={(e) => setSelectedNewManager(e.target.value)}
                            className="w-full px-3 py-2.5 border border-slate-200 dark:border-slate-700 rounded-lg text-[14px] bg-white dark:bg-slate-800"
                        >
                            <option value="">No manager</option>
                            {availableManagers.map(m => (
                                <option key={m.user_id} value={m.user_id}>{m.name}</option>
                            ))}
                        </select>
                    </div>
                    <div className="flex gap-3">
                        <button onClick={() => setShowChangeManagerModal(false)} className="flex-1 px-4 py-2.5 bg-slate-100 dark:bg-slate-800 rounded-lg font-medium">Cancel</button>
                        <button onClick={confirmChangeManager} disabled={isSubmitting} className="flex-1 px-4 py-2.5 bg-indigo-500 text-white rounded-lg font-medium disabled:opacity-50">
                            {isSubmitting ? 'Saving...' : 'Save'}
                        </button>
                    </div>
                </Modal>
            )}

            {/* Remove Member Modal */}
            {showRemoveMemberModal && memberToRemove && (
                <Modal title="Remove Member" onClose={() => setShowRemoveMemberModal(false)}>
                    <p className="text-[14px] text-slate-600 dark:text-slate-400 mb-6">
                        Are you sure you want to remove <strong>{memberToRemove.name}</strong> from {selectedTeam?.name}?
                    </p>
                    <div className="flex gap-3">
                        <button onClick={() => setShowRemoveMemberModal(false)} className="flex-1 px-4 py-2.5 bg-slate-100 dark:bg-slate-800 rounded-lg font-medium">Cancel</button>
                        <button onClick={confirmRemoveMember} disabled={isSubmitting} className="flex-1 px-4 py-2.5 bg-red-500 text-white rounded-lg font-medium disabled:opacity-50">
                            {isSubmitting ? 'Removing...' : 'Remove'}
                        </button>
                    </div>
                </Modal>
            )}
        </div>
    );
}

function Modal({ title, children, onClose }) {
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose}></div>
            <div className="relative bg-white dark:bg-slate-900 rounded-xl shadow-xl p-6 w-full max-w-md">
                <h3 className="text-[18px] font-semibold text-slate-900 dark:text-white mb-4">{title}</h3>
                {children}
            </div>
        </div>
    );
}

function TeamsSkeleton() {
    return (
        <div className="mx-auto max-w-[1360px] space-y-6 animate-pulse">
            <div className="flex justify-between">
                <div className="space-y-2">
                    <div className="h-7 w-24 bg-slate-200 dark:bg-slate-800 rounded"></div>
                    <div className="h-4 w-56 bg-slate-200 dark:bg-slate-800 rounded"></div>
                </div>
                <div className="h-10 w-32 bg-slate-200 dark:bg-slate-800 rounded-lg"></div>
            </div>
            <div className="grid grid-cols-12 gap-6">
                <div className="col-span-4 h-[400px] bg-slate-200 dark:bg-slate-800 rounded-xl"></div>
                <div className="col-span-8 h-[400px] bg-slate-200 dark:bg-slate-800 rounded-xl"></div>
            </div>
        </div>
    );
}
