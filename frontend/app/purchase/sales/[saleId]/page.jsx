'use client';

import { useState, useEffect } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import api from '../../../../services/api';
import {
    ArrowLeft,
    CheckCircle,
    XCircle,
    User,
    Building,
    Calendar,
    DollarSign,
    Percent,
    Receipt,
    Clock,
    FileText,
    AlertTriangle
} from 'lucide-react';

export default function SalesReviewDetailPage() {
    const router = useRouter();
    const params = useParams();
    const [loading, setLoading] = useState(true);
    const [sale, setSale] = useState(null);
    const [rejectionReason, setRejectionReason] = useState('');
    const [showApproveModal, setShowApproveModal] = useState(false);
    const [showRejectModal, setShowRejectModal] = useState(false);
    const [actionLoading, setActionLoading] = useState(false);

    useEffect(() => {
        const fetchSale = async () => {
            try {
                const res = await api.get(`/purchase/sales/${params.saleId}`);
                const d = res.data;
                const total = d.deal?.amount || 0;
                const subtotal = d.deal?.subtotal || total;
                const tax = d.deal?.tax || 0;
                const discount = Number(d.deal?.discount || 0);
                const money = (value) => `₹${Number(value).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
                let uiStatus = 'Pending Review';
                const rawStatus = (d.status || '').toLowerCase();
                if (['pending', 'paid', 'sent', 'overdue'].includes(rawStatus)) {
                    uiStatus = 'Approved';
                } else if (['cancelled', 'rejected'].includes(rawStatus)) {
                    uiStatus = 'Rejected';
                } else if (['draft'].includes(rawStatus)) {
                    uiStatus = 'Pending Review';
                } else {
                    uiStatus = d.status ? d.status.charAt(0).toUpperCase() + d.status.slice(1) : 'Pending Review';
                }

                setSale({
                    id: d.id,
                    saleId: `SAL-${d.id}`,
                    status: uiStatus,
                    client: {
                        name: d.client?.name || 'Unknown',
                        contact: d.client?.name || '',
                        email: d.client?.email || '',
                        phone: '',
                        type: 'Client'
                    },
                    pricing: {
                        subtotal: money(subtotal),
                        discount: `${Number(subtotal) > 0 ? ((discount / Number(subtotal)) * 100).toFixed(1).replace(/\.0$/, '') : 0}%`,
                        discountAmount: money(discount),
                        tax: money(tax),
                        total: money(total)
                    },
                    invoiceLinkage: { hasInvoice: true, invoiceId: d.number, invoiceStatus: d.status },
                    rep: d.salesperson?.name || 'Unknown',
                    createdAt: '',
                    createdBy: d.salesperson?.name || 'Unknown',
                    reviewedBy: null,
                    reviewedAt: null,
                    notes: '',
                    items: (d.deal?.items || []).map(i => ({
                        description: i.description,
                        qty: i.quantity || 1,
                        unitPrice: `₹${Number(i.total / (i.quantity || 1)).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`,
                        total: `₹${Number(i.total).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`
                    }))
                });
            } catch (err) {
                console.error('Failed to fetch sale detail:', err);
            } finally {
                setLoading(false);
            }
        };
        fetchSale();
    }, [params.saleId]);

    const handleApprove = async () => {
        setActionLoading(true);
        try {
            await api.post(`/purchase/sales/${params.saleId}/approve`);
            alert('Sale approved successfully');
            router.push('/purchase/sales');
        } catch (err) {
            alert('Failed to approve sale');
        } finally {
            setActionLoading(false);
            setShowApproveModal(false);
        }
    };

    const handleReject = async () => {
        if (!rejectionReason.trim()) { alert('Please enter a reason'); return; }
        setActionLoading(true);
        try {
            await api.post(`/purchase/sales/${params.saleId}/reject?reason=${encodeURIComponent(rejectionReason)}`);
            alert('Sale rejected');
            router.push('/purchase/sales');
        } catch (err) {
            alert('Failed to reject sale');
        } finally {
            setActionLoading(false);
            setShowRejectModal(false);
        }
    };

    if (loading) return <DetailSkeleton />;

    const isPending = sale.status === 'Pending Review';

    return (
        <div className="mx-auto max-w-[1100px] space-y-6 pb-12 font-sans text-slate-900 dark:text-slate-100">

            {/* Back Link */}
            <Link
                href="/purchase/sales"
                className="inline-flex items-center gap-2 text-[13px] font-medium text-slate-500 hover:text-emerald-600 transition-colors"
            >
                <ArrowLeft size={16} />
                Back to Sales Review
            </Link>

            {/* Header */}
            <div className="flex items-start justify-between">
                <div>
                    <div className="flex items-center gap-3 mb-2">
                        <h1 className="text-[28px] font-semibold tracking-tight text-slate-900 dark:text-white leading-tight">{sale.saleId}</h1>
                        <StatusBadge status={sale.status} />
                    </div>
                    <p className="text-[15px] text-slate-500 dark:text-slate-400 font-medium">{sale.client.name}</p>
                </div>
                <div className="text-right">
                    <div className="text-[32px] font-bold text-slate-900 dark:text-white">{sale.pricing.total}</div>
                    <div className="text-[13px] text-slate-500">Total Amount</div>
                </div>
            </div>

            <div className="grid grid-cols-12 gap-6">

                {/* Main Content */}
                <div className="col-span-12 lg:col-span-8 space-y-6">

                    {/* Sale Summary */}
                    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                        <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white mb-4">Sale Summary</h3>
                        <div className="space-y-2">
                            <div className="overflow-x-auto">
                                <table className="w-full text-[13px]">
                                    <thead>
                                        <tr className="border-b border-slate-100 dark:border-slate-700/50">
                                            <th className="text-left py-2 font-medium text-slate-500 whitespace-nowrap">Description</th>
                                            <th className="text-center py-2 font-medium text-slate-500 w-20 whitespace-nowrap">Qty</th>
                                            <th className="text-right py-2 font-medium text-slate-500 w-28 whitespace-nowrap">Unit Price</th>
                                            <th className="text-right py-2 font-medium text-slate-500 w-28 whitespace-nowrap">Total</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-50 dark:divide-slate-700/50">
                                        {sale.items.map((item, i) => (
                                            <tr key={i}>
                                                <td className="py-2.5 text-slate-800 dark:text-slate-200 whitespace-nowrap">{item.description}</td>
                                                <td className="py-2.5 text-center text-slate-600 dark:text-slate-400 whitespace-nowrap">{item.qty}</td>
                                                <td className="py-2.5 text-right text-slate-600 dark:text-slate-400 whitespace-nowrap">{item.unitPrice}</td>
                                                <td className="py-2.5 text-right font-medium text-slate-800 dark:text-slate-200 whitespace-nowrap">{item.total}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>

                    {/* Pricing Breakdown */}
                    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                        <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white mb-4">Pricing Breakdown</h3>
                        <div className="space-y-3">
                            <div className="flex justify-between text-[14px]">
                                <span className="text-slate-600 dark:text-slate-400">Subtotal</span>
                                <span className="text-slate-800 dark:text-slate-200">{sale.pricing.subtotal}</span>
                            </div>
                            <div className="flex justify-between text-[14px]">
                                <span className="text-slate-600 dark:text-slate-400 flex items-center gap-2">
                                    <Percent size={14} className="text-amber-500" />
                                    Discount ({sale.pricing.discount})
                                </span>
                                <span className="text-red-600 dark:text-red-400">-{sale.pricing.discountAmount}</span>
                            </div>
                            <div className="flex justify-between text-[14px]">
                                <span className="text-slate-600 dark:text-slate-400">GST</span>
                                <span className="text-slate-800 dark:text-slate-200">{sale.pricing.tax}</span>
                            </div>
                            <div className="border-t border-slate-100 dark:border-slate-700/50 pt-3 flex justify-between text-[16px] font-semibold">
                                <span className="text-slate-900 dark:text-white">Total</span>
                                <span className="text-slate-900 dark:text-white">{sale.pricing.total}</span>
                            </div>
                        </div>
                        {parseFloat(sale.pricing.discount) >= 10 && (
                            <div className="mt-4 p-3 bg-amber-50 dark:bg-amber-900/20 rounded-lg border border-amber-200 dark:border-amber-800 flex items-start gap-2">
                                <AlertTriangle size={16} className="text-amber-600 dark:text-amber-400 mt-0.5" />
                                <div>
                                    <div className="text-[13px] font-medium text-amber-700 dark:text-amber-400">High Discount Applied</div>
                                    <div className="text-[12px] text-amber-600 dark:text-amber-500">Discount exceeds 10% threshold. Review required.</div>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Notes */}
                    {sale.notes && (
                        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                            <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white mb-3">Notes</h3>
                            <p className="text-[14px] text-slate-600 dark:text-slate-400">{sale.notes}</p>
                        </div>
                    )}
                </div>

                {/* Sidebar */}
                <div className="col-span-12 lg:col-span-4 space-y-6">

                    {/* Approval Panel */}
                    {isPending && (
                        <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                            <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white mb-4">Approval Decision</h3>
                            <div className="space-y-3">
                                <button
                                    onClick={() => setShowApproveModal(true)}
                                    disabled={actionLoading}
                                    className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-emerald-500 hover:bg-emerald-600 text-white rounded-lg font-medium transition-colors disabled:opacity-50"
                                >
                                    <CheckCircle size={18} />
                                    Approve Sale
                                </button>
                                <button
                                    onClick={() => setShowRejectModal(true)}
                                    disabled={actionLoading}
                                    className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-red-500 hover:bg-red-600 text-white rounded-lg font-medium transition-colors disabled:opacity-50"
                                >
                                    <XCircle size={18} />
                                    Reject Sale
                                </button>
                            </div>
                        </div>
                    )}

                    {/* Client Info */}
                    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                        <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white mb-4">Client Information</h3>
                        <div className="space-y-3">
                            <div className="flex items-center gap-3">
                                <Building size={16} className="text-slate-400" />
                                <div>
                                    <div className="text-[13px] font-medium text-slate-800 dark:text-slate-200">{sale.client.name}</div>
                                    <div className="text-[11px] text-slate-400">{sale.client.type}</div>
                                </div>
                            </div>
                            <div className="flex items-center gap-3">
                                <User size={16} className="text-slate-400" />
                                <div className="text-[13px] text-slate-600 dark:text-slate-400">{sale.client.contact}</div>
                            </div>
                            <div className="text-[12px] text-slate-500 dark:text-slate-400">
                                {sale.client.email}<br />
                                {sale.client.phone}
                            </div>
                        </div>
                    </div>

                    {/* Invoice Linkage */}
                    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                        <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white mb-4">Invoice Linkage</h3>
                        {sale.invoiceLinkage.hasInvoice ? (
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <Receipt size={16} className="text-emerald-500" />
                                    <span className="text-[13px] font-medium text-slate-800 dark:text-slate-200">{sale.invoiceLinkage.invoiceId}</span>
                                </div>
                                <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded ${sale.invoiceLinkage.invoiceStatus === 'Draft' ? 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300' : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400'}`}>
                                    {sale.invoiceLinkage.invoiceStatus}
                                </span>
                            </div>
                        ) : (
                            <div className="text-[13px] text-slate-500 dark:text-slate-400">No invoice linked</div>
                        )}
                    </div>

                    {/* Audit Strip */}
                    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
                        <h3 className="text-[16px] font-semibold text-slate-900 dark:text-white mb-4">Audit Trail</h3>
                        <div className="space-y-3 text-[12px]">
                            <div className="flex items-start gap-3">
                                <Clock size={14} className="text-slate-400 mt-0.5" />
                                <div>
                                    <div className="text-slate-600 dark:text-slate-400">Created</div>
                                    <div className="font-medium text-slate-800 dark:text-slate-200">{sale.createdAt}</div>
                                    <div className="text-slate-500">by {sale.createdBy}</div>
                                </div>
                            </div>
                            {sale.reviewedAt && (
                                <div className="flex items-start gap-3">
                                    <FileText size={14} className="text-slate-400 mt-0.5" />
                                    <div>
                                        <div className="text-slate-600 dark:text-slate-400">Reviewed</div>
                                        <div className="font-medium text-slate-800 dark:text-slate-200">{sale.reviewedAt}</div>
                                        <div className="text-slate-500">by {sale.reviewedBy}</div>
                                    </div>
                                </div>
                            )}
                            {!sale.reviewedAt && (
                                <div className="text-slate-400 italic">Not yet reviewed</div>
                            )}
                        </div>
                    </div>
                </div>
            </div>

            {/* Approve Confirmation Modal */}
            {showApproveModal && (
                <ConfirmationModal
                    title="Approve Sale"
                    message={`Are you sure you want to approve sale ${sale.saleId} for ${sale.pricing.total}?`}
                    confirmLabel="Approve"
                    confirmColor="emerald"
                    onConfirm={handleApprove}
                    onCancel={() => setShowApproveModal(false)}
                />
            )}

            {/* Reject Confirmation Modal */}
            {showRejectModal && (
                <ConfirmationModal
                    title="Reject Sale"
                    message={`Are you sure you want to reject sale ${sale.saleId}?`}
                    confirmLabel="Reject"
                    confirmColor="red"
                    showReason
                    reason={rejectionReason}
                    onReasonChange={setRejectionReason}
                    onConfirm={handleReject}
                    onCancel={() => setShowRejectModal(false)}
                />
            )}
        </div>
    );
}

function StatusBadge({ status }) {
    let colors = 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300';
    if (status === 'Pending Review') {
        colors = 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400';
    } else if (status === 'Approved') {
        colors = 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400';
    } else if (status === 'Rejected') {
        colors = 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400';
    }

    return (
        <span className={`inline-flex px-2.5 py-1 rounded text-[11px] font-bold uppercase ${colors}`}>
            {status === 'Pending Review' ? 'Pending' : status}
        </span>
    );
}

function ConfirmationModal({ title, message, confirmLabel, confirmColor, showReason, reason, onReasonChange, onConfirm, onCancel }) {
    const colorClasses = {
        emerald: 'bg-emerald-500 hover:bg-emerald-600',
        red: 'bg-red-500 hover:bg-red-600'
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onCancel}></div>
            <div className="relative bg-white dark:bg-slate-900 rounded-xl shadow-xl p-6 w-full max-w-md" style={{ animation: 'fadeInUp 200ms ease-out forwards' }}>
                <h3 className="text-[18px] font-semibold text-slate-900 dark:text-white mb-2">{title}</h3>
                <p className="text-[14px] text-slate-600 dark:text-slate-400 mb-4">{message}</p>
                {showReason && (
                    <div className="mb-4">
                        <label className="block text-[12px] font-medium text-slate-600 dark:text-slate-400 mb-1">Rejection Reason</label>
                        <textarea
                            value={reason}
                            onChange={(e) => onReasonChange(e.target.value)}
                            className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-[14px] bg-slate-50 dark:bg-slate-800 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-red-500"
                            rows={3}
                            placeholder="Enter reason for rejection..."
                        />
                    </div>
                )}
                <div className="flex gap-3">
                    <button
                        onClick={onCancel}
                        className="flex-1 px-4 py-2.5 bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 rounded-lg font-medium hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={onConfirm}
                        className={`flex-1 px-4 py-2.5 text-white rounded-lg font-medium transition-colors ${colorClasses[confirmColor]}`}
                    >
                        {confirmLabel}
                    </button>
                </div>
            </div>
        </div>
    );
}

function DetailSkeleton() {
    return (
        <div className="mx-auto max-w-[1100px] space-y-6 animate-pulse">
            <div className="h-4 w-32 bg-slate-200 dark:bg-slate-800 rounded"></div>
            <div className="flex justify-between">
                <div className="space-y-2">
                    <div className="h-8 w-48 bg-slate-200 dark:bg-slate-800 rounded"></div>
                    <div className="h-4 w-32 bg-slate-200 dark:bg-slate-800 rounded"></div>
                </div>
                <div className="h-10 w-32 bg-slate-200 dark:bg-slate-800 rounded"></div>
            </div>
            <div className="grid grid-cols-12 gap-6">
                <div className="col-span-8 space-y-6">
                    <div className="h-[200px] bg-slate-200 dark:bg-slate-800 rounded-xl"></div>
                    <div className="h-[150px] bg-slate-200 dark:bg-slate-800 rounded-xl"></div>
                </div>
                <div className="col-span-4 space-y-6">
                    <div className="h-[150px] bg-slate-200 dark:bg-slate-800 rounded-xl"></div>
                    <div className="h-[120px] bg-slate-200 dark:bg-slate-800 rounded-xl"></div>
                </div>
            </div>
        </div>
    );
}
