'use client';

import { useEffect, useState } from 'react';
import { X, Loader2, Upload } from 'lucide-react';
import api, { getActiveTeamId, setActiveTeamId } from '../../services/api';
import { suggestNotesExtra } from '../../lib/leadCsv.cjs';

const NONE = '__none__';

const ENTITY_CONFIG = {
  leads: {
    title: 'Import leads from CSV or Excel',
    accept: '.csv,.xlsx',
    pick: 'Choose CSV or Excel (.xlsx) file',
    fields: [
      { key: 'name', label: 'Name (required)' },
      { key: 'email', label: 'Email (optional)' },
      { key: 'phone', label: 'Phone (optional)' },
      { key: 'company', label: 'Company (optional)' },
      { key: 'source', label: 'Source (optional)' },
      { key: 'service_type', label: 'Service type (optional)' },
      { key: 'website', label: 'Website (optional)' },
      { key: 'industry', label: 'Industry (optional)' },
      { key: 'linkedin_url', label: 'LinkedIn (optional)' },
      { key: 'notes', label: 'Notes (optional)' },
    ],
  },
  clients: {
    title: 'Import clients from CSV',
    fields: [
      { key: 'name', label: 'Name' },
      { key: 'email', label: 'Email' },
      { key: 'phone', label: 'Phone' },
      { key: 'company', label: 'Company' },
      { key: 'address', label: 'Address' },
      { key: 'gstin', label: 'GSTIN' },
    ],
  },
  deals: {
    title: 'Import deals from CSV',
    fields: [
      { key: 'title', label: 'Title' },
      { key: 'amount', label: 'Amount' },
      { key: 'client_email', label: 'Client email' },
      { key: 'client_name', label: 'Client name' },
      { key: 'expected_close', label: 'Expected close' },
      { key: 'source', label: 'Source' },
    ],
  },
};

export default function CsvImportModal({ entity, isOpen, onClose, onRefresh }) {
  const config = ENTITY_CONFIG[entity];
  const [file, setFile] = useState(null);
  const [headers, setHeaders] = useState([]);
  const [mapping, setMapping] = useState({});
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [committing, setCommitting] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setFile(null);
      setHeaders([]);
      setMapping({});
      setPreview(null);
      setError(null);
      setLoading(false);
      setCommitting(false);
    }
  }, [isOpen]);

  if (!isOpen || !config) return null;

  const cleanedMapping = (payload) => {
    const cleaned = {};
    Object.entries(payload || {}).forEach(([k, v]) => {
      if (k === 'notes_extra' && Array.isArray(v)) {
        cleaned.notes_extra = v.filter(Boolean);
        return;
      }
      if (v && v !== NONE) cleaned[k] = v;
    });
    return cleaned;
  };

  const extraNoteColumns = entity === 'leads' ? suggestNotesExtra(headers, mapping) : [];

  const ensureActiveTeamId = async () => {
    try {
      const res = await api.get('/teams/mine');
      const fromServer = res.data?.active_team_id ?? res.data?.teams?.[0]?.id;
      if (fromServer != null && fromServer !== '') {
        setActiveTeamId(fromServer);
        return String(fromServer);
      }
    } catch {
      // import may still succeed if backend resolves team from company
    }
    const cached = getActiveTeamId();
    return cached ? String(cached) : null;
  };

  const postForm = async (path, fd) => {
    const headers = {};
    const teamId = await ensureActiveTeamId();
    if (teamId) headers['X-Team-Id'] = teamId;
    const res = await fetch(`/api${path}`, {
      method: 'POST',
      body: fd,
      credentials: 'include',
      headers,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      let message = 'Request failed';
      if (typeof data.detail === 'string') {
        message = data.detail === 'Not Found'
          ? 'Import is not available on the server. Redeploy the app or contact support.'
          : data.detail;
      }
      const err = new Error(message);
      err.response = { status: res.status, data };
      throw err;
    }
    return { data };
  };

  const postImport = (path, mappingOverride) => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('mapping', JSON.stringify(cleanedMapping(mappingOverride || mapping)));
    return postForm(path, fd);
  };

  const handleFile = async (e) => {
    const next = e.target.files?.[0];
    if (!next) return;
    setFile(next);
    setError(null);
    setPreview(null);
    setLoading(true);
    try {
      const fd = new FormData();
      fd.append('file', next);
      const res = await postForm(`/import/${entity}/preview`, fd);
      setHeaders(res.data.headers || []);
      setMapping(res.data.suggested_mapping || {});
      setPreview(res.data);
    } catch (err) {
      const detail = err.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'Could not read the file.');
    } finally {
      setLoading(false);
    }
  };

  const remap = async (nextMapping) => {
    setMapping(nextMapping);
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const res = await postImport(`/import/${entity}/preview`, nextMapping);
      setPreview(res.data);
    } catch (err) {
      const detail = err.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'Could not preview CSV.');
    } finally {
      setLoading(false);
    }
  };

  const commit = async () => {
    setCommitting(true);
    setError(null);
    try {
      await postImport(`/import/${entity}/commit`);
      onRefresh?.();
      onClose();
    } catch (err) {
      const detail = err.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'Import failed.');
    } finally {
      setCommitting(false);
    }
  };

  const counts = preview?.counts || { new: 0, duplicate: 0, invalid: 0 };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white dark:bg-slate-800 rounded-lg shadow-xl w-full max-w-3xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200 dark:border-slate-700">
          <h2 className="text-sm font-bold text-slate-900 dark:text-white">{config.title}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-slate-500 hover:text-slate-800">
            <X size={16} />
          </button>
        </div>
        <div className="p-5 overflow-y-auto space-y-4">
          <label className="flex flex-col items-center justify-center border-2 border-dashed border-slate-300 dark:border-slate-600 rounded-lg p-8 cursor-pointer hover:border-blue-400">
            <Upload size={24} className="text-slate-400 mb-2" />
            <span className="text-sm text-slate-600 dark:text-slate-300">{file ? file.name : (config.pick || 'Choose CSV file')}</span>
            <input type="file" accept={config.accept || '.csv'} className="hidden" onChange={handleFile} />
          </label>

          {loading && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 size={16} className="animate-spin" /> Previewing…
            </div>
          )}

          {preview && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                {config.fields.map((field) => (
                  <label key={field.key} className="block text-xs">
                    <span className="text-slate-500 uppercase font-semibold">{field.label}</span>
                    <select
                      value={mapping[field.key] || NONE}
                      onChange={(e) => {
                        const next = {
                          ...mapping,
                          [field.key]: e.target.value,
                          notes_extra: suggestNotesExtra(headers, {
                            ...mapping,
                            [field.key]: e.target.value,
                          }).filter((col) => (mapping.notes_extra || []).includes(col)),
                        };
                        remap(next);
                      }}
                      className="mt-1 w-full text-sm rounded border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 px-2 py-1"
                    >
                      <option value={NONE}>— skip —</option>
                      {headers.map((h) => (
                        <option key={h} value={h}>{h}</option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
              {entity === 'leads' && extraNoteColumns.length > 0 && (
                <div className="rounded border border-slate-200 dark:border-slate-600 p-3 space-y-2">
                  <p className="text-xs font-semibold text-slate-500 uppercase">
                    Extra CSV columns → append to notes (optional)
                  </p>
                  <div className="flex flex-wrap gap-x-4 gap-y-2 max-h-36 overflow-y-auto">
                    {extraNoteColumns.map((col) => (
                      <label key={col} className="flex items-center gap-1.5 text-xs text-slate-700 dark:text-slate-200">
                        <input
                          type="checkbox"
                          checked={(mapping.notes_extra || []).includes(col)}
                          onChange={(e) => {
                            const current = new Set(mapping.notes_extra || []);
                            if (e.target.checked) current.add(col);
                            else current.delete(col);
                            remap({ ...mapping, notes_extra: [...current] });
                          }}
                        />
                        {col}
                      </label>
                    ))}
                  </div>
                </div>
              )}
              <p className="text-sm text-slate-600 dark:text-slate-300">
                {counts.new} new · {counts.duplicate} duplicate · {counts.invalid} invalid
              </p>
              {entity === 'leads' && (
                <p className="text-xs text-slate-500">
                  Importing this file again adds only new rows. A row is a duplicate when its email or phone already belongs to a lead.
                </p>
              )}
              <div className="max-h-48 overflow-auto border border-slate-200 dark:border-slate-600 rounded text-xs">
                <table className="w-full">
                  <tbody>
                    {(preview.rows || []).slice(0, 20).map((row) => (
                      <tr key={row.index} className="border-b border-slate-100 dark:border-slate-700">
                        <td className="px-2 py-1 font-mono">{row.status}</td>
                        <td className="px-2 py-1">{JSON.stringify(row.values)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>
        <div className="px-5 py-3 border-t border-slate-200 dark:border-slate-700 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-3 py-1.5 text-sm border rounded-lg">Cancel</button>
          <button
            type="button"
            disabled={!preview || counts.new === 0 || committing}
            onClick={commit}
            className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg disabled:opacity-50 flex items-center gap-2"
          >
            {committing && <Loader2 size={14} className="animate-spin" />}
            Import {counts.new} new
          </button>
        </div>
      </div>
    </div>
  );
}

export function useImportUndo(entity, onRefresh) {
  const [lastBatch, setLastBatch] = useState(null);
  const [undoing, setUndoing] = useState(false);

  const refreshBatch = async () => {
    try {
      const res = await api.get('/import/last');
      setLastBatch(res.data?.batch || null);
    } catch {
      setLastBatch(null);
    }
  };

  useEffect(() => {
    refreshBatch();
  }, [entity]);

  const undo = async () => {
    setUndoing(true);
    try {
      await api.post('/import/undo');
      await refreshBatch();
      onRefresh?.();
    } finally {
      setUndoing(false);
    }
  };

  const canUndo = lastBatch?.entity_type === entity && (lastBatch?.item_count || 0) > 0;

  return { canUndo, undo, undoing, refreshBatch };
}
