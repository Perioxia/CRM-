'use client';

import { useState, useEffect, useRef } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import api from '../../../services/api';

export default function AcceptInvitePage() {
    const router = useRouter();
    const params = useParams();
    const token = params.token;
    const hasAttempted = useRef(false);

    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [accepted, setAccepted] = useState(false);
    const [alreadyAccepted, setAlreadyAccepted] = useState(false);

    useEffect(() => {
        if (!token || hasAttempted.current) return;
        hasAttempted.current = true;

        const acceptInvite = async () => {
            try {
                const response = await api.post(`/auth/accept-invite/${token}`, {
                    password: '',
                });

                const { user } = response.data;
                // Auth cookie is set by the backend. No need for localStorage.
                setAccepted(true);
                setLoading(false);

                setTimeout(() => {
                    const routes = {
                        sales: '/sales/dashboard',
                        manager: '/manager/dashboard',
                        md: '/md/dashboard',
                        purchase: '/purchase/dashboard',
                        admin: '/admin/dashboard',
                    };
                    router.push(routes[user?.role] || '/sales/dashboard');
                }, 2000);
            } catch (err) {
                const detail = err.response?.data?.detail;
                const msg = typeof detail === 'object' ? JSON.stringify(detail) : (detail || 'Failed to accept invite.');

                if (msg.includes('already been accepted') || msg.includes('already exists')) {
                    setAlreadyAccepted(true);
                    setLoading(false);
                } else {
                    setError(msg);
                    setLoading(false);
                }
            }
        };

        acceptInvite();
    }, [token, router]);

    if (loading) {
        return (
            <div className="min-h-screen bg-page flex items-center justify-center">
                <div className="bg-surface rounded-2xl shadow-xl border border-border p-8 sm:p-10 text-center max-w-md w-full">
                    <div className="w-12 h-12 border-4 border-accent border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
                    <h2 className="text-xl font-bold text-primary mb-2">Setting Up Your Account</h2>
                    <p className="text-sm text-secondary">Please wait while we create your account...</p>
                </div>
            </div>
        );
    }

    if (accepted) {
        return (
            <div className="min-h-screen bg-page flex items-center justify-center py-12 px-4 animate-fade-in-up">
                <div className="max-w-md w-full">
                    <div className="bg-surface rounded-2xl shadow-xl border border-border p-8 sm:p-10 text-center">
                        <div className="w-16 h-16 bg-success/10 rounded-full flex items-center justify-center mx-auto mb-4">
                            <svg className="w-8 h-8 text-success" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                            </svg>
                        </div>
                        <h2 className="text-xl font-bold text-primary mb-2">Account Created!</h2>
                        <p className="text-sm text-secondary">Your account has been set up. Redirecting to your dashboard...</p>
                    </div>
                </div>
            </div>
        );
    }

    if (alreadyAccepted) {
        return (
            <div className="min-h-screen bg-page flex items-center justify-center py-12 px-4 animate-fade-in-up">
                <div className="max-w-md w-full">
                    <div className="bg-surface rounded-2xl shadow-xl border border-border p-8 sm:p-10 text-center">
                        <div className="w-16 h-16 bg-success/10 rounded-full flex items-center justify-center mx-auto mb-4">
                            <svg className="w-8 h-8 text-success" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                            </svg>
                        </div>
                        <h2 className="text-xl font-bold text-primary mb-2">Invitation accepted</h2>
                        <p className="text-sm text-secondary mb-6">
                            Your account is ready. Log in with your email and the password you received with your invitation.
                        </p>
                        <Link href="/login" className="btn-primary inline-flex justify-center w-full">
                            Go to login
                        </Link>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-page flex items-center justify-center py-12 px-4 animate-fade-in-up">
            <div className="max-w-md w-full">
                <div className="bg-surface rounded-2xl shadow-xl border border-border p-8 sm:p-10 text-center">
                    {error && (
                        <div className="bg-error/10 border border-error/20 text-error px-4 py-3 rounded-lg text-sm flex items-center gap-2 mb-5">
                            <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            {error}
                        </div>
                    )}
                    <div className="mt-4">
                        <Link href="/login" className="font-semibold text-accent hover:text-accent-hover hover:underline transition-colors">
                            Go to Login
                        </Link>
                    </div>
                </div>
            </div>
        </div>
    );
}
