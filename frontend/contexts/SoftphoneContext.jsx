'use client';

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { useAuth } from './AuthContext';
import api from '../services/api';

const SoftphoneContext = createContext(null);

function formatElapsed(seconds) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function SoftphoneProvider({ children }) {
  const { user } = useAuth();
  const deviceRef = useRef(null);
  const callRef = useRef(null);
  const timerRef = useRef(null);

  const [ready, setReady] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [status, setStatus] = useState('idle'); // idle | connecting | ringing | open | incoming
  const [error, setError] = useState('');
  const [fromPhone, setFromPhone] = useState('');
  const [remoteLabel, setRemoteLabel] = useState('');
  const [callLogId, setCallLogId] = useState(null);
  const [muted, setMuted] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [incomingCall, setIncomingCall] = useState(null);

  const clearTimer = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  const startTimer = () => {
    clearTimer();
    setElapsed(0);
    timerRef.current = setInterval(() => setElapsed((n) => n + 1), 1000);
  };

  const resetCallUi = useCallback(() => {
    clearTimer();
    callRef.current = null;
    setIncomingCall(null);
    setStatus('idle');
    setMuted(false);
    setElapsed(0);
    setRemoteLabel('');
    setCallLogId(null);
  }, []);

  const destroyDevice = useCallback(async () => {
    clearTimer();
    try {
      if (callRef.current) {
        callRef.current.disconnect();
      }
    } catch (_) { /* ignore */ }
    try {
      if (deviceRef.current) {
        deviceRef.current.destroy();
      }
    } catch (_) { /* ignore */ }
    deviceRef.current = null;
    callRef.current = null;
    setReady(false);
    setEnabled(false);
    resetCallUi();
  }, [resetCallUi]);

  const bindCallEvents = useCallback((call) => {
    call.on('accept', () => {
      setStatus('open');
      startTimer();
    });
    call.on('ringing', () => setStatus('ringing'));
    call.on('disconnect', () => resetCallUi());
    call.on('cancel', () => resetCallUi());
    call.on('reject', () => resetCallUi());
    call.on('error', (err) => {
      const code = err?.code || err?.twilioError?.code;
      const raw = err?.message || 'Call error';
      const msg = code === 31005
        ? 'Call failed (31005): Twilio rejected the call. Confirm TwiML App Voice URL is https://crm.perioxia.com/api/telephony/twilio/voice, Dial caller ID is your Twilio number (not a personal mobile), and on trial accounts the destination number is verified in Twilio.'
        : raw;
      setError(msg);
      resetCallUi();
    });
  }, [resetCallUi]);

  const registerDevice = useCallback(async () => {
    if (!user?.id) return;
    setError('');
    try {
      const conn = await api.get('/telephony/connection');
      if (!conn.data?.twilio?.configured) {
        await destroyDevice();
        return;
      }
      setEnabled(true);
      const tok = await api.post('/telephony/twilio/token');
      setFromPhone(tok.data.from_phone || conn.data.twilio.phone_number || '');

      const { Device } = await import('@twilio/voice-sdk');
      if (deviceRef.current) {
        try { deviceRef.current.destroy(); } catch (_) { /* ignore */ }
      }
      const device = new Device(tok.data.token, { logLevel: 1 });
      deviceRef.current = device;

      device.on('registered', () => setReady(true));
      device.on('unregistered', () => setReady(false));
      device.on('error', (err) => {
        const code = err?.code || err?.twilioError?.code;
        if (code === 31005) {
          setError('Softphone gateway error (31005). Check TwiML Voice URL, Twilio caller ID, and trial verified numbers.');
        } else {
          setError(err?.message || 'Softphone error');
        }
      });
      device.on('incoming', (call) => {
        callRef.current = call;
        bindCallEvents(call);
        setIncomingCall(call);
        setStatus('incoming');
        setRemoteLabel(call.parameters?.From || call.parameters?.from || 'Incoming call');
      });
      await device.register();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const statusCode = err?.response?.status;
      setEnabled(false);
      setReady(false);
      if (statusCode === 400) {
        setError('');
        return;
      }
      if (statusCode === 502) {
        setError(
          'Twilio secrets cannot be read on this server. An admin must re-save the Auth Token and API Key Secret under Settings → Telephony (on crm.perioxia.com, not local).',
        );
        return;
      }
      setError(typeof detail === 'string' ? detail : 'Could not start softphone');
    }
  }, [user?.id, bindCallEvents, destroyDevice]);

  useEffect(() => {
    if (!user?.id || user.role === 'platform_admin') {
      destroyDevice();
      return undefined;
    }
    registerDevice();
    return () => {
      destroyDevice();
    };
  }, [user?.id, user?.role, registerDevice, destroyDevice]);

  const placeCall = useCallback(async ({ leadId, clientId, dealId, toPhone, label } = {}) => {
    if (!deviceRef.current || !ready) {
      throw new Error('Softphone is not ready');
    }
    if (status !== 'idle') {
      throw new Error('Already on a call');
    }
    setError('');
    setStatus('connecting');
    setRemoteLabel(label || toPhone || 'Calling…');
    try {
      const created = await api.post('/telephony/twilio/calls', {
        lead_id: leadId || undefined,
        client_id: clientId || undefined,
        deal_id: dealId || undefined,
        to_phone: toPhone || undefined,
      });
      setCallLogId(created.data.id);
      const params = {
        To: created.data.to_e164 || created.data.to_phone,
        CallLogId: String(created.data.id),
      };
      const call = await deviceRef.current.connect({ params });
      callRef.current = call;
      bindCallEvents(call);
      setStatus('ringing');
      return created.data;
    } catch (err) {
      resetCallUi();
      const detail = err?.response?.data?.detail || err.message || 'Could not place call';
      setError(typeof detail === 'string' ? detail : 'Could not place call');
      throw err;
    }
  }, [ready, status, bindCallEvents, resetCallUi]);

  const hangup = useCallback(() => {
    try {
      if (callRef.current) callRef.current.disconnect();
      else if (deviceRef.current) deviceRef.current.disconnectAll();
    } catch (_) { /* ignore */ }
    resetCallUi();
  }, [resetCallUi]);

  const acceptIncoming = useCallback(async () => {
    if (!incomingCall) return;
    try {
      await incomingCall.accept();
      setStatus('open');
      startTimer();
      setIncomingCall(null);
    } catch (err) {
      setError(err?.message || 'Could not accept call');
      resetCallUi();
    }
  }, [incomingCall, resetCallUi]);

  const rejectIncoming = useCallback(() => {
    try {
      if (incomingCall) incomingCall.reject();
    } catch (_) { /* ignore */ }
    resetCallUi();
  }, [incomingCall, resetCallUi]);

  const toggleMute = useCallback(() => {
    if (!callRef.current) return;
    const next = !muted;
    callRef.current.mute(next);
    setMuted(next);
  }, [muted]);

  const value = {
    enabled,
    ready,
    status,
    error,
    fromPhone,
    remoteLabel,
    callLogId,
    muted,
    elapsed,
    elapsedLabel: formatElapsed(elapsed),
    placeCall,
    hangup,
    acceptIncoming,
    rejectIncoming,
    toggleMute,
    refresh: registerDevice,
  };

  return (
    <SoftphoneContext.Provider value={value}>
      {children}
    </SoftphoneContext.Provider>
  );
}

export function useSoftphone() {
  const ctx = useContext(SoftphoneContext);
  if (!ctx) throw new Error('useSoftphone must be used within SoftphoneProvider');
  return ctx;
}
