'use client';

import Link from 'next/link';
import { Bell, Calendar, Shield, KeyRound, MessageCircle, MapPin, Mail, Webhook, Target, Brain, BookMarked, Layers, Store, FileText, Tags, ShieldCheck, Table2, Phone } from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import { useT } from '../../contexts/LocaleContext';
import NotificationPreferencesPanel from '../../components/shared/NotificationPreferencesPanel';

export default function SettingsHomePage() {
  const { user } = useAuth();
  const t = useT();

  return (
    <div className="min-h-full bg-page pb-10">
      <div className="bg-surface border-b border-border px-8 py-6 mb-8">
        <div className="max-w-4xl mx-auto">
          <h1 className="text-xl font-bold text-primary">Settings</h1>
          <p className="text-sm text-muted mt-1">
            Personal preferences for {user?.full_name || user?.email || 'your account'}
          </p>
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-8 space-y-6">
        <div className="p-6 rounded-xl border border-border bg-surface">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Shield size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Two-Factor Authentication</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Secure your account with an authenticator app.
                </div>
              </div>
            </div>
            <Link
              href="/settings/security"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Shield size={12} />
              Manage Security
            </Link>
          </div>
        </div>

        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Mail size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Email</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Connect Gmail or Outlook to send and log mail on records.
                </div>
              </div>
            </div>
            <Link
              href="/settings/email"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Mail size={12} />
              Manage mailbox
            </Link>
          </div>
        </div>

        <div className="p-6 rounded-xl border border-border bg-surface">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-surface-elevated">
                <FileText size={14} className="text-muted" />
              </div>
              <div>
                <div className="text-sm font-semibold text-primary">{t('Email templates')}</div>
                <div className="text-xs text-muted">
                  {t('Reusable subject and body for email campaigns.')}
                </div>
              </div>
            </div>
            <Link
              href="/settings/email-templates"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-border text-secondary hover:bg-surface-elevated"
            >
              <FileText size={12} />
              {t('Email templates')}
            </Link>
          </div>
        </div>

        <div className="p-6 rounded-xl border border-border bg-surface">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-surface-elevated">
                <Table2 size={14} className="text-muted" />
              </div>
              <div>
                <div className="text-sm font-semibold text-primary">{t('Google Sheet leads')}</div>
                <div className="text-xs text-muted">
                  {t('Pull new rows from a shared Google Sheet. Existing email or phone matches are skipped.')}
                </div>
              </div>
            </div>
            <Link
              href="/settings/lead-sheet"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-border text-secondary hover:bg-surface-elevated"
            >
              <Table2 size={12} />
              {t('Google Sheet leads')}
            </Link>
          </div>
        </div>

        <div className="p-6 rounded-xl border border-border bg-surface">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-surface-elevated">
                <FileText size={14} className="text-muted" />
              </div>
              <div>
                <div className="text-sm font-semibold text-primary">Quote plans</div>
                <div className="text-xs text-muted">
                  Plans, default prices, and scope bullets for sales quotations.
                </div>
              </div>
            </div>
            <Link
              href="/settings/quote-plans"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-border text-secondary hover:bg-surface-elevated"
            >
              <FileText size={12} />
              Manage plans
            </Link>
          </div>
        </div>

        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Calendar size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Calendar</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Push CRM meetings to Google Calendar or Outlook.
                </div>
              </div>
            </div>
            <Link
              href="/settings/calendar"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Calendar size={12} />
              Manage calendar
            </Link>
          </div>
        </div>

        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Shield size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Privacy</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Download your data. Admins can export or erase lead and client PII.
                </div>
              </div>
            </div>
            <Link
              href="/settings/privacy"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Shield size={12} />
              Privacy
            </Link>
          </div>
        </div>

        {(user?.role === 'admin' || user?.role === 'md') && (
        <>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <KeyRound size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">API keys</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Issue read or write keys for /api/v1/ integrations.
                </div>
              </div>
            </div>
            <Link
              href="/settings/api-keys"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <KeyRound size={12} />
              Manage keys
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Webhook size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Outbound webhooks</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Signed HTTPS callbacks for lead, deal, and invoice events.
                </div>
              </div>
            </div>
            <Link
              href="/settings/webhooks"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Webhook size={12} />
              Manage endpoints
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Shield size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">SAML SSO</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Company IdP login for existing users.
                </div>
              </div>
            </div>
            <Link
              href="/settings/sso"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Shield size={12} />
              Manage SAML
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <MessageCircle size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">WhatsApp</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Gupshup connection and approved message templates.
                </div>
              </div>
            </div>
            <Link
              href="/settings/whatsapp"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <MessageCircle size={12} />
              Manage templates
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Phone size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Telephony</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Browser softphone (Twilio) and optional Exotel click-to-call.
                </div>
              </div>
            </div>
            <Link
              href="/settings/telephony"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Phone size={12} />
              Manage telephony
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <MapPin size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Territories</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Route leads to teams by service type or source.
                </div>
              </div>
            </div>
            <Link
              href="/settings/territories"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <MapPin size={12} />
              Manage territories
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Target size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Lead &amp; deal scoring</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Point rules that rank leads and deals by how hot they are.
                </div>
              </div>
            </div>
            <Link
              href="/settings/scoring"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Target size={12} />
              Manage scoring
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Brain size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Predictive AI</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Win-probability and churn risk learned from your own history.
                </div>
              </div>
            </div>
            <Link
              href="/settings/predictions"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Brain size={12} />
              Manage predictions
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <BookMarked size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Tally / QuickBooks</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Push invoices to accounting. Stub provider until live Tally/QBO keys exist.
                </div>
              </div>
            </div>
            <Link
              href="/settings/accounting"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <BookMarked size={12} />
              Manage accounting
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Tags size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Price books</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Dealer / retail lists; default book prices product lines on quotes and invoices.
                </div>
              </div>
            </div>
            <Link
              href="/settings/price-books"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Tags size={12} />
              Manage price books
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <ShieldCheck size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Deal & discount approvals</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Thresholds for large deals and discounted quote lines before close or accept.
                </div>
              </div>
            </div>
            <Link
              href="/settings/approvals"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <ShieldCheck size={12} />
              Manage approvals
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <FileText size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">GST e-invoice (IRN)</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  NIC/IRP credentials for live IRN. Stub when unset.
                </div>
              </div>
            </div>
            <Link
              href="/settings/einvoice"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <FileText size={12} />
              Manage e-invoice
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Layers size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Custom modules</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Company-defined objects with their own fields and records.
                </div>
              </div>
            </div>
            <Link
              href="/settings/modules"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Layers size={12} />
              Manage modules
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Store size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Marketplace</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  First-party apps. Install to bookmark a feature; this is not a third-party store.
                </div>
              </div>
            </div>
            <Link
              href="/settings/marketplace"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Store size={12} />
              Browse apps
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Mail size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Email campaigns</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Send one message to leads or clients through the existing CRM mailbox/SMTP path. Not a marketing platform.
                </div>
              </div>
            </div>
            <Link
              href="/campaigns"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Mail size={12} />
              Open campaigns
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Mail size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Mass email</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  One-shot blast, 25 recipients per send and 100 per UTC day. Not a campaign.
                </div>
              </div>
            </div>
            <Link
              href="/mass-email"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Mail size={12} />
              Open mass email
            </Link>
          </div>
        </div>
        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Mail size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Cases</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Thin customer requests plus a web-to-case form. Not a helpdesk product.
                </div>
              </div>
            </div>
            <Link
              href="/cases"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              Open cases
            </Link>
          </div>
        </div>
        </>
        )}

        <NotificationPreferencesPanel />

        <div className="p-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 p-1.5 rounded-md bg-slate-100 dark:bg-slate-700">
                <Calendar size={14} className="text-slate-500" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900 dark:text-white">Leave Management</div>
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Submit and track leave requests.
                </div>
              </div>
            </div>
            <Link
              href="/settings/leave"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              <Bell size={12} />
              Open Leave
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
