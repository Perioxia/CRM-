'use client';

import { Mic, MicOff, Phone, PhoneOff } from 'lucide-react';
import { useSoftphone } from '../../contexts/SoftphoneContext';

export default function SoftphoneBar() {
  const {
    enabled,
    status,
    error,
    remoteLabel,
    muted,
    elapsedLabel,
    hangup,
    acceptIncoming,
    rejectIncoming,
    toggleMute,
  } = useSoftphone();

  if (!enabled) return null;

  if (status === 'incoming') {
    return (
      <div className="fixed bottom-4 left-1/2 z-[80] -translate-x-1/2 w-[min(420px,92vw)] rounded-xl border border-emerald-200 bg-white dark:bg-slate-800 dark:border-emerald-800 shadow-lg px-4 py-3">
        <p className="text-sm font-semibold text-slate-900 dark:text-white">Incoming call</p>
        <p className="text-xs text-slate-500 mt-0.5 truncate">{remoteLabel}</p>
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={acceptIncoming}
            className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-lg bg-emerald-600 text-white text-sm py-2 hover:bg-emerald-700"
          >
            <Phone size={14} /> Accept
          </button>
          <button
            type="button"
            onClick={rejectIncoming}
            className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-lg bg-red-600 text-white text-sm py-2 hover:bg-red-700"
          >
            <PhoneOff size={14} /> Decline
          </button>
        </div>
      </div>
    );
  }

  if (status === 'idle') {
    if (!error) return null;
    return (
      <div className="fixed bottom-4 right-4 z-[80] max-w-sm rounded-lg border border-amber-200 bg-amber-50 text-amber-900 text-xs px-3 py-2 shadow">
        Softphone: {error}
      </div>
    );
  }

  const label =
    status === 'connecting' ? 'Connecting…'
      : status === 'ringing' ? 'Ringing…'
        : 'On call';

  return (
    <div className="fixed bottom-4 left-1/2 z-[80] -translate-x-1/2 w-[min(420px,92vw)] rounded-xl border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 shadow-lg px-4 py-3 flex items-center gap-3">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-slate-900 dark:text-white truncate">{remoteLabel || label}</p>
        <p className="text-xs text-slate-500">{label}{status === 'open' ? ` · ${elapsedLabel}` : ''}</p>
        {error && <p className="text-xs text-red-600 mt-0.5">{error}</p>}
      </div>
      {status === 'open' && (
        <button
          type="button"
          onClick={toggleMute}
          className="p-2 rounded-lg border border-slate-200 dark:border-slate-600 text-slate-700 dark:text-slate-200"
          aria-label={muted ? 'Unmute' : 'Mute'}
        >
          {muted ? <MicOff size={16} /> : <Mic size={16} />}
        </button>
      )}
      <button
        type="button"
        onClick={hangup}
        className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 text-white text-sm px-3 py-2 hover:bg-red-700"
      >
        <PhoneOff size={14} /> End
      </button>
    </div>
  );
}
