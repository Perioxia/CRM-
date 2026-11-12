'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useAuth } from '../../../contexts/AuthContext';
import api from '../../../services/api';

function canManage(user) {
  const role = user?.role;
  return role === 'admin' || role === 'md';
}

export default function TelephonySettingsPage() {
  const { user } = useAuth();
  const allowed = canManage(user);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sid, setSid] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [apiToken, setApiToken] = useState('');
  const [subdomain, setSubdomain] = useState('api.exotel.com');
  const [callerId, setCallerId] = useState('');
  const [configured, setConfigured] = useState(false);
  const [saving, setSaving] = useState(false);

  const [twilioConfigured, setTwilioConfigured] = useState(false);
  const [twilioAccountSid, setTwilioAccountSid] = useState('');
  const [twilioAuthToken, setTwilioAuthToken] = useState('');
  const [twilioApiKeySid, setTwilioApiKeySid] = useState('');
  const [twilioApiKeySecret, setTwilioApiKeySecret] = useState('');
  const [twilioTwimlAppSid, setTwilioTwimlAppSid] = useState('');
  const [twilioPhone, setTwilioPhone] = useState('');
  const [twilioDefaultUserId, setTwilioDefaultUserId] = useState('');
  const [twilioRingMobileDefault, setTwilioRingMobileDefault] = useState(true);

  const [mePhone, setMePhone] = useState('');
  const [meTwilioPhone, setMeTwilioPhone] = useState('');
  const [meRingMobile, setMeRingMobile] = useState(true);
  const [savingMe, setSavingMe] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      // Any logged-in user can read connection status; only admin/MD edit company credentials.
      const [connRes, meRes] = await Promise.all([
        api.get('/telephony/connection'),
        api.get('/telephony/me'),
      ]);
      if (connRes.data) {
        setConfigured(Boolean(connRes.data.configured));
        setSid(connRes.data.sid || '');
        setSubdomain(connRes.data.subdomain || 'api.exotel.com');
        setCallerId(connRes.data.caller_id || '');
        const tw = connRes.data.twilio || {};
        setTwilioConfigured(Boolean(tw.configured));
        setTwilioAccountSid(tw.account_sid || '');
        setTwilioApiKeySid(tw.api_key_sid || '');
        setTwilioTwimlAppSid(tw.twiml_app_sid || '');
        setTwilioPhone(tw.phone_number || '');
        setTwilioDefaultUserId(tw.default_user_id ? String(tw.default_user_id) : '');
        setTwilioRingMobileDefault(tw.ring_mobile_default !== false);
      }
      setMePhone(meRes.data.phone || '');
      setMeTwilioPhone(meRes.data.twilio_phone_number || '');
      setMeRingMobile(meRes.data.twilio_ring_mobile !== false);
      if (!connRes.data?.twilio?.configured && meRes.data?.twilio_configured) {
        setTwilioConfigured(true);
      }
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not load telephony settings.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const saveExotel = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      const payload = { sid, subdomain, caller_id: callerId };
      if (apiKey.trim()) payload.api_key = apiKey.trim();
      if (apiToken.trim()) payload.api_token = apiToken.trim();
      const data = (await api.put('/telephony/connection', payload)).data;
      setConfigured(Boolean(data.configured));
      setSid(data.sid || '');
      setCallerId(data.caller_id || '');
      setApiToken('');
      setApiKey('');
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not save Exotel.');
    } finally {
      setSaving(false);
    }
  };

  const saveTwilio = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      const payload = {
        twilio_account_sid: twilioAccountSid,
        twilio_api_key_sid: twilioApiKeySid,
        twilio_twiml_app_sid: twilioTwimlAppSid,
        twilio_phone_number: twilioPhone,
        twilio_ring_mobile_default: twilioRingMobileDefault,
      };
      if (twilioDefaultUserId.trim()) {
        payload.twilio_default_user_id = Number(twilioDefaultUserId);
      } else {
        payload.twilio_default_user_id = 0;
      }
      if (twilioAuthToken.trim()) payload.twilio_auth_token = twilioAuthToken.trim();
      if (twilioApiKeySecret.trim()) payload.twilio_api_key_secret = twilioApiKeySecret.trim();
      const data = (await api.put('/telephony/connection', payload)).data;
      const tw = data.twilio || {};
      setTwilioConfigured(Boolean(tw.configured));
      setTwilioAccountSid(tw.account_sid || '');
      setTwilioApiKeySid(tw.api_key_sid || '');
      setTwilioTwimlAppSid(tw.twiml_app_sid || '');
      setTwilioPhone(tw.phone_number || '');
      setTwilioDefaultUserId(tw.default_user_id ? String(tw.default_user_id) : '');
      setTwilioRingMobileDefault(tw.ring_mobile_default !== false);
      setTwilioAuthToken('');
      setTwilioApiKeySecret('');
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not save Twilio.');
    } finally {
      setSaving(false);
    }
  };

  const saveMe = async (e) => {
    e.preventDefault();
    setSavingMe(true);
    setError('');
    try {
      const data = (await api.put('/telephony/me', {
        twilio_phone_number: meTwilioPhone,
        twilio_ring_mobile: meRingMobile,
      })).data;
      setMeTwilioPhone(data.twilio_phone_number || '');
      setMeRingMobile(data.twilio_ring_mobile !== false);
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not save your softphone settings.');
    } finally {
      setSavingMe(false);
    }
  };

  return (
    <div className="min-h-full bg-slate-50 dark:bg-slate-900 pb-10">
      <div className="bg-white dark:bg-slate-800 border-b border-slate-200 dark:border-slate-700 px-8 py-6 mb-8">
        <div className="max-w-4xl mx-auto">
          <p className="text-xs text-slate-400 mb-1">
            <Link href="/settings" className="hover:underline">Settings</Link>
            {' / Telephony'}
          </p>
          <h1 className="text-xl font-bold text-slate-900 dark:text-white">Telephony</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            Browser softphone via Twilio, plus optional Exotel click-to-call.
          </p>
        </div>
      </div>
      <div className="max-w-4xl mx-auto px-8 space-y-6">
        {error && <p className="text-sm text-red-600">{error}</p>}
        {loading ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : (
          <>
            <form onSubmit={saveMe} className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 space-y-3">
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white">My softphone</h2>
              <p className={`text-xs ${twilioConfigured ? 'text-emerald-700 dark:text-emerald-400' : 'text-amber-700 dark:text-amber-400'}`}>
                {twilioConfigured
                  ? 'Company Twilio is configured. Softphone should appear on lead pages (Call from browser).'
                  : 'Company Twilio is not configured yet. Ask an admin or MD to finish Settings → Telephony.'}
              </p>
              <p className="text-xs text-slate-500">
                Personal phone on file: {mePhone || 'not set'}. When ring-mobile is on, inbound calls also ring that number.
              </p>
              <label className="block text-xs">
                My Twilio DID (optional — only if this account assigned you a Twilio number)
                <input value={meTwilioPhone} onChange={(e) => setMeTwilioPhone(e.target.value)} placeholder="leave blank to use company Twilio number" className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
              </label>
              <p className="text-[11px] text-slate-500">
                Do not put your personal mobile here. Outbound calls always show the company Twilio number as caller ID; this field is for inbound DID routing only.
              </p>
              <label className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={meRingMobile} onChange={(e) => setMeRingMobile(e.target.checked)} />
                Also ring my mobile for inbound calls
              </label>
              <button type="submit" disabled={savingMe} className="px-3 py-1.5 bg-slate-900 text-white text-xs rounded-lg disabled:opacity-50">
                {savingMe ? 'Saving…' : 'Save my settings'}
              </button>
            </form>

            {allowed && (
              <>
                <form onSubmit={saveTwilio} className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 space-y-3">
                  <h2 className="text-sm font-semibold text-slate-900 dark:text-white">Twilio softphone</h2>
                  <p className="text-xs text-slate-500">
                    {twilioConfigured ? 'Twilio is configured.' : 'Not configured yet.'}
                    {' '}Point the phone number Voice webhook to <code className="text-[10px]">/api/telephony/twilio/inbound</code>
                    {' '}and the TwiML App Voice URL to <code className="text-[10px]">/api/telephony/twilio/voice</code>.
                  </p>
                  <label className="block text-xs">
                    Account SID
                    <input value={twilioAccountSid} onChange={(e) => setTwilioAccountSid(e.target.value)} className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
                  </label>
                  <label className="block text-xs">
                    Auth token
                    <input type="password" value={twilioAuthToken} onChange={(e) => setTwilioAuthToken(e.target.value)} placeholder={twilioConfigured ? 'unchanged' : ''} className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
                  </label>
                  <label className="block text-xs">
                    API key SID
                    <input value={twilioApiKeySid} onChange={(e) => setTwilioApiKeySid(e.target.value)} className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
                  </label>
                  <label className="block text-xs">
                    API key secret
                    <input type="password" value={twilioApiKeySecret} onChange={(e) => setTwilioApiKeySecret(e.target.value)} placeholder={twilioConfigured ? 'unchanged' : ''} className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
                  </label>
                  <label className="block text-xs">
                    TwiML App SID
                    <input value={twilioTwimlAppSid} onChange={(e) => setTwilioTwimlAppSid(e.target.value)} className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
                  </label>
                  <label className="block text-xs">
                    Company Twilio number
                    <input value={twilioPhone} onChange={(e) => setTwilioPhone(e.target.value)} placeholder="+91…" className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
                  </label>
                  <label className="block text-xs">
                    Default inbound user id
                    <input value={twilioDefaultUserId} onChange={(e) => setTwilioDefaultUserId(e.target.value)} placeholder="fallback when caller is unknown" className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
                  </label>
                  <label className="flex items-center gap-2 text-xs">
                    <input type="checkbox" checked={twilioRingMobileDefault} onChange={(e) => setTwilioRingMobileDefault(e.target.checked)} />
                    Ring agent mobile by default
                  </label>
                  <button type="submit" disabled={saving} className="px-3 py-1.5 bg-slate-900 text-white text-xs rounded-lg disabled:opacity-50">
                    {saving ? 'Saving…' : 'Save Twilio'}
                  </button>
                </form>

                <form onSubmit={saveExotel} className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 space-y-3">
                  <h2 className="text-sm font-semibold text-slate-900 dark:text-white">Exotel click-to-call</h2>
                  <p className="text-xs text-slate-500">
                    We dial the agent’s phone, then the customer. Set your user phone so you can be called.
                  </p>
                  <p className="text-xs text-slate-500">{configured ? 'Exotel is configured.' : 'Not configured yet.'}</p>
                  <label className="block text-xs">
                    Account SID
                    <input value={sid} onChange={(e) => setSid(e.target.value)} className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
                  </label>
                  <label className="block text-xs">
                    API key (defaults to SID)
                    <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="optional" className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
                  </label>
                  <label className="block text-xs">
                    API token
                    <input type="password" value={apiToken} onChange={(e) => setApiToken(e.target.value)} placeholder={configured ? 'unchanged' : ''} className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
                  </label>
                  <label className="block text-xs">
                    Subdomain
                    <input value={subdomain} onChange={(e) => setSubdomain(e.target.value)} className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
                  </label>
                  <label className="block text-xs">
                    ExoPhone / Caller ID
                    <input value={callerId} onChange={(e) => setCallerId(e.target.value)} className="mt-1 w-full px-2 py-1.5 border rounded text-sm bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-600" />
                  </label>
                  <button type="submit" disabled={saving} className="px-3 py-1.5 bg-slate-900 text-white text-xs rounded-lg disabled:opacity-50">
                    {saving ? 'Saving…' : 'Save Exotel'}
                  </button>
                </form>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
