'use client';

import { useEffect, useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import api from '../../services/api';

const FIELDS = [
  { name: 'name', label: 'Full Name *', type: 'text', required: true },
  { name: 'company', label: 'Company', type: 'text' },
  { name: 'email', label: 'Email Address', type: 'email' },
  { name: 'phone', label: 'Phone Number', type: 'tel' },
];

function detailsFrom(lead) {
  return {
    name: lead?.name || '',
    company: lead?.company || '',
    email: lead?.email || '',
    phone: lead?.phone || '',
  };
}

export default function LeadEditModal({ isOpen, onClose, onSaved, lead }) {
  const [form, setForm] = useState(detailsFrom(lead));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (isOpen) {
      setForm(detailsFrom(lead));
      setError(null);
    }
  }, [isOpen, lead]);

  if (!isOpen) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.put(`/leads/${lead.id}`, form);
      await onSaved();
      onClose();
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not save this lead.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center">
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" onClick={onClose}></div>
      <div className="relative bg-white dark:bg-slate-800 w-full max-w-md rounded-2xl shadow-2xl border border-slate-200 dark:border-slate-700 overflow-hidden">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 dark:border-slate-700">
          <h2 className="text-lg font-bold text-slate-800 dark:text-white">Edit Lead</h2>
          <button type="button" aria-label="Close" onClick={onClose} className="p-2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 rounded-full transition-colors">
            <X size={20} />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {error && (
            <div className="p-3 bg-red-50 border border-red-100 text-red-600 text-xs font-medium rounded-lg">{error}</div>
          )}
          {FIELDS.map((field) => (
            <div key={field.name} className="space-y-1.5">
              <label htmlFor={`lead-edit-${field.name}`} className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
                {field.label}
              </label>
              <input
                id={`lead-edit-${field.name}`}
                name={field.name}
                type={field.type}
                required={field.required}
                value={form[field.name]}
                onChange={(e) => setForm((prev) => ({ ...prev, [field.name]: e.target.value }))}
                className="w-full px-3 py-2 bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all"
              />
            </div>
          ))}
          <div className="pt-2 flex gap-3">
            <button type="button" onClick={onClose} className="flex-1 px-4 py-2.5 bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 text-sm font-bold rounded-xl hover:bg-slate-200 dark:hover:bg-slate-600 transition-all">
              Cancel
            </button>
            <button type="submit" disabled={saving} className="flex-[2] px-4 py-2.5 bg-blue-600 text-white text-sm font-bold rounded-xl hover:bg-blue-700 transition-all disabled:opacity-50 flex items-center justify-center gap-2">
              {saving ? <Loader2 size={18} className="animate-spin" /> : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
