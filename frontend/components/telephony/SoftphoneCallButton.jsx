'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Phone } from 'lucide-react';
import { useSoftphone } from '../../contexts/SoftphoneContext';

export default function SoftphoneCallButton({ leadId, clientId, dealId, toPhone, label, onPlaced }) {
  const { enabled, ready, status, placeCall } = useSoftphone();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  if (!enabled) {
    return (
      <div className="rounded-lg border border-dashed border-slate-200 dark:border-slate-600 p-3 space-y-1">
        <p className="text-xs font-medium text-slate-700 dark:text-slate-200">Browser calling</p>
        <p className="text-xs text-slate-500">
          Twilio is not configured yet.{' '}
          <Link href="/settings/telephony" className="text-emerald-700 dark:text-emerald-400 underline">
            Open Telephony settings
          </Link>
        </p>
      </div>
    );
  }

  const disabled = !ready || status !== 'idle' || busy || !toPhone;

  const onClick = async () => {
    setBusy(true);
    setError('');
    try {
      const call = await placeCall({ leadId, clientId, dealId, toPhone, label });
      if (onPlaced) onPlaced(call);
    } catch (err) {
      const detail = err?.response?.data?.detail || err.message;
      setError(typeof detail === 'string' ? detail : 'Call failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 text-sm font-medium rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
      >
        <Phone size={14} />
        {!toPhone
          ? 'Add a phone number first'
          : !ready
            ? 'Connecting phone…'
            : busy || status !== 'idle'
              ? 'In a call…'
              : 'Call from browser'}
      </button>
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}
