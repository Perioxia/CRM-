import api from './api';
import { encodeHeader } from '../lib/chatMedia.cjs';

// Files go straight to the API host (not through the /api proxy), authorised by a
// short-lived upload token that is reused until shortly before it expires.
let uploadGrant = null;

async function getUploadGrant(force = false) {
  if (!force && uploadGrant && uploadGrant.expiresAt - Date.now() > 60000) return uploadGrant;
  const data = await api.post('/chat/upload-token').then((r) => r.data);
  uploadGrant = { token: data.token, base: data.upload_base, expiresAt: Date.now() + data.expires_in * 1000 };
  return uploadGrant;
}

function uploadError(status, detail) {
  const err = new Error(detail || 'Upload failed');
  err.response = { status, data: { detail } };
  return err;
}

function postFile(grant, conversationId, file, { caption, clientMsgId, onProgress, signal }) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${grant.base}/conversations/${conversationId}/files`);
    xhr.setRequestHeader('X-Chat-Upload-Token', grant.token);
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    xhr.setRequestHeader('X-File-Name', encodeHeader(file.name));
    xhr.setRequestHeader('X-Client-Msg-Id', clientMsgId);
    if (caption) xhr.setRequestHeader('X-Caption', encodeHeader(caption));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(uploadError(xhr.status, typeof body?.detail === 'string' ? body.detail : body?.detail?.message));
    };
    xhr.onerror = () => reject(uploadError(0, null));
    xhr.onabort = () => reject(Object.assign(uploadError(0, null), { aborted: true }));
    if (signal) {
      if (signal.aborted) {
        reject(Object.assign(uploadError(0, null), { aborted: true }));
        return;
      }
      signal.addEventListener('abort', () => xhr.abort(), { once: true });
    }
    xhr.send(file);
  });
}

export async function uploadChatFile(conversationId, file, options) {
  try {
    return await postFile(await getUploadGrant(), conversationId, file, options);
  } catch (err) {
    // A stale token (e.g. after logging in again elsewhere) looks like 404: retry once with a fresh one.
    if (err.response?.status !== 404 || err.aborted) throw err;
    return postFile(await getUploadGrant(true), conversationId, file, options);
  }
}

export const chatApi = {
  status: () => api.get('/chat/status').then((r) => r.data),
  sync: () => api.get('/chat/sync').then((r) => r.data),
  directory: (q, limit = 20) => api.get('/chat/directory', { params: { q: q || undefined, limit } }).then((r) => r.data.items),
  presence: (userIds) => api.get('/chat/presence', { params: { user_ids: userIds.join(',') } }).then((r) => r.data.online),
  conversations: (skip = 0, limit = 50) => api.get('/chat/conversations', { params: { skip, limit } }).then((r) => r.data),
  conversation: (id) => api.get(`/chat/conversations/${id}`).then((r) => r.data),
  startDirect: (userId) => api.post('/chat/conversations/direct', { user_id: userId }).then((r) => r.data),
  startTeam: (teamId) => api.post('/chat/conversations/team', { team_id: teamId }).then((r) => r.data),
  messages: (id, params = {}) => api.get(`/chat/conversations/${id}/messages`, { params }).then((r) => r.data),
  send: (id, body, clientMsgId) => api.post(`/chat/conversations/${id}/messages`, { body, client_msg_id: clientMsgId }).then((r) => r.data),
  markRead: (id, seq) => api.post(`/chat/conversations/${id}/read`, { seq }).then((r) => r.data),
  wsTicket: () => api.post('/chat/ws-ticket').then((r) => r.data),
  attachmentLinks: (ids) => api.post('/chat/attachments/links', { ids }).then((r) => r.data.links),
  iceServers: () => api.get('/chat/calls/ice-servers').then((r) => r.data.ice_servers),
};

export function apiErrorMessage(err, fallback) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  return fallback;
}

export function newClientMsgId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
