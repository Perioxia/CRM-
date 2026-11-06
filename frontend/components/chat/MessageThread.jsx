'use client';

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, ArrowDown, ArrowLeft, Check, CheckCheck, Loader2, Phone, PhoneIncoming, PhoneMissed, PhoneOutgoing, X } from 'lucide-react';
import { useT } from '../../contexts/LocaleContext';
import { useChat } from '../../contexts/ChatContext';
import { useCall } from '../../contexts/CallContext';
import { chatApi, apiErrorMessage, newClientMsgId, uploadChatFile } from '../../services/chat';
import { dayLabel, groupMessages, mergeMessages, roleLabel, timeLabel } from '../../lib/chatFormat.cjs';
import { applyLinks, expiringAttachmentIds, fileCategory } from '../../lib/chatMedia.cjs';
import { callEntryLabel } from '../../lib/chatCalls.cjs';
import Attachment from './Attachment';
import Avatar from './Avatar';
import Composer from './Composer';

const PAGE = 50;
const POLL_WHEN_OFFLINE_MS = 5000;
const NEAR_BOTTOM_PX = 120;
const LINK_CHECK_MS = 30000;

function lastConfirmedSeq(messages) {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
        if (Number.isInteger(messages[i].seq)) return messages[i].seq;
    }
    return 0;
}

export default function MessageThread({ conversation, onlineIds, maxChars, files, onBack, onActivity }) {
    const t = useT();
    const { myId, live, typing, subscribe, requestReconcile, sendTyping } = useChat();
    const { callsEnabled, canCall, startCall } = useCall();
    const conversationId = conversation.id;
    const isTeam = conversation.kind === 'team';
    const other = conversation.other_user;
    const otherActive = Boolean(other?.is_active);
    const canCompose = isTeam || otherActive;
    const membersById = useMemo(() => {
        const map = new Map();
        for (const member of conversation.members || []) map.set(member.id, member);
        return map;
    }, [conversation.members]);
    const online = onlineIds instanceof Set ? onlineIds : new Set();
    const peerOnline = !isTeam && online.has(other?.id);
    const teamOnline = isTeam
        ? (conversation.members || []).filter((m) => m.id !== myId && m.is_active && online.has(m.id)).length
        : 0;

    const [messages, setMessages] = useState([]);
    const [loadState, setLoadState] = useState('loading');
    const [loadError, setLoadError] = useState(null);
    const [hasMoreBefore, setHasMoreBefore] = useState(false);
    const [loadingOlder, setLoadingOlder] = useState(false);
    const [otherLastRead, setOtherLastRead] = useState(conversation.other_last_read_seq || 0);
    const [showJump, setShowJump] = useState(false);
    const [retry, setRetry] = useState(0);

    const scrollRef = useRef(null);
    const nearBottomRef = useRef(true);
    const preserveRef = useRef(null);
    const fetchingNewRef = useRef(false);
    const messagesRef = useRef(messages);
    const readSentRef = useRef(conversation.last_read_seq || 0);
    const uploadsRef = useRef(new Map());
    const previewUrlsRef = useRef(new Map());
    const brokenRef = useRef(new Set());
    const brokenTimer = useRef(null);
    messagesRef.current = messages;

    const dropPreview = useCallback((clientMsgId) => {
        const url = previewUrlsRef.current.get(clientMsgId);
        if (url) URL.revokeObjectURL(url);
        previewUrlsRef.current.delete(clientMsgId);
    }, []);

    useEffect(() => () => {
        uploadsRef.current.forEach((controller) => controller.abort());
        uploadsRef.current.clear();
        previewUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
        previewUrlsRef.current.clear();
        clearTimeout(brokenTimer.current);
    }, [conversationId]);

    const refreshLinks = useCallback(async (ids) => {
        const unique = [...new Set(ids)].slice(0, 100);
        if (!unique.length) return;
        try {
            const links = await chatApi.attachmentLinks(unique);
            setMessages((prev) => applyLinks(prev, links));
        } catch {
            // The next check retries.
        }
    }, []);

    const onBrokenAttachment = useCallback((id) => {
        brokenRef.current.add(id);
        clearTimeout(brokenTimer.current);
        brokenTimer.current = setTimeout(() => {
            const ids = [...brokenRef.current];
            brokenRef.current.clear();
            refreshLinks(ids);
        }, 300);
    }, [refreshLinks]);

    const scrollToBottom = useCallback((smooth = false) => {
        const el = scrollRef.current;
        if (!el) return;
        el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
        setShowJump(false);
    }, []);

    const markRead = useCallback(() => {
        const seq = lastConfirmedSeq(messagesRef.current);
        if (!seq || seq <= readSentRef.current) return;
        if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
        if (!nearBottomRef.current) return;
        readSentRef.current = seq;
        chatApi.markRead(conversationId, seq)
            .then(() => requestReconcile())
            .catch(() => {
                readSentRef.current = 0;
            });
    }, [conversationId, requestReconcile]);

    useEffect(() => {
        let cancelled = false;
        setMessages([]);
        setLoadState('loading');
        setLoadError(null);
        setOtherLastRead(conversation.other_last_read_seq || 0);
        readSentRef.current = conversation.last_read_seq || 0;
        chatApi.messages(conversationId, { limit: PAGE })
            .then((data) => {
                if (cancelled) return;
                setMessages(data.items);
                setHasMoreBefore(Boolean(data.has_more_before));
                setLoadState('ready');
                nearBottomRef.current = true;
            })
            .catch((err) => {
                if (cancelled) return;
                setLoadError(apiErrorMessage(err, t('Could not load messages.')));
                setLoadState('error');
            });
        return () => {
            cancelled = true;
        };
        // conversation summary fields are only read on (re)open
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [conversationId, retry, t]);

    const fetchNew = useCallback(async () => {
        if (fetchingNewRef.current || loadState !== 'ready') return;
        fetchingNewRef.current = true;
        try {
            let after = lastConfirmedSeq(messagesRef.current);
            for (let page = 0; page < 20; page += 1) {
                const data = await chatApi.messages(conversationId, { after_seq: after, limit: PAGE });
                if (!data.items.length) break;
                setMessages((prev) => mergeMessages(prev, data.items));
                after = data.items[data.items.length - 1].seq;
                if (data.items.length < PAGE) break;
            }
        } catch {
            // The next event, poll or reconcile retries.
        } finally {
            fetchingNewRef.current = false;
        }
    }, [conversationId, loadState]);

    useEffect(() => subscribe((event) => {
        if (event.type === 'message.new' && event.conversation_id === conversationId) {
            fetchNew();
        } else if (event.type === 'conversation.read' && event.conversation_id === conversationId && event.user_id !== myId) {
            setOtherLastRead((prev) => Math.max(prev, event.last_read_seq || 0));
        } else if (event.type === 'reconcile') {
            const entry = event.state?.conversations?.find((c) => c.id === conversationId);
            if (entry && entry.last_seq > lastConfirmedSeq(messagesRef.current)) fetchNew();
        }
    }), [subscribe, conversationId, fetchNew, myId]);

    useEffect(() => {
        if (live === 'live' || loadState !== 'ready') return undefined;
        const timer = setInterval(fetchNew, POLL_WHEN_OFFLINE_MS);
        return () => clearInterval(timer);
    }, [live, loadState, fetchNew]);

    // Download links expire after ~15 minutes; renew the ones about to lapse in one request.
    useEffect(() => {
        if (loadState !== 'ready') return undefined;
        const timer = setInterval(() => refreshLinks(expiringAttachmentIds(messagesRef.current, Date.now())), LINK_CHECK_MS);
        return () => clearInterval(timer);
    }, [loadState, refreshLinks]);

    useEffect(() => {
        if (live !== 'live' || loadState !== 'ready') return undefined;
        const timer = setInterval(() => {
            chatApi.conversation(conversationId)
                .then((c) => setOtherLastRead((prev) => Math.max(prev, c.other_last_read_seq || 0)))
                .catch(() => {});
        }, 60000);
        return () => clearInterval(timer);
    }, [live, loadState, conversationId]);

    useLayoutEffect(() => {
        const el = scrollRef.current;
        if (!el) return;
        if (preserveRef.current !== null) {
            el.scrollTop = el.scrollHeight - preserveRef.current;
            preserveRef.current = null;
            return;
        }
        if (nearBottomRef.current) {
            el.scrollTop = el.scrollHeight;
        } else if (messages.length) {
            setShowJump(true);
        }
    }, [messages]);

    useEffect(() => {
        markRead();
    }, [messages, markRead]);

    useEffect(() => {
        const onVisible = () => markRead();
        document.addEventListener('visibilitychange', onVisible);
        return () => document.removeEventListener('visibilitychange', onVisible);
    }, [markRead]);

    const loadOlder = useCallback(async () => {
        const first = messagesRef.current.find((m) => Number.isInteger(m.seq));
        if (!first || loadingOlder || !hasMoreBefore) return;
        setLoadingOlder(true);
        try {
            const data = await chatApi.messages(conversationId, { before_seq: first.seq, limit: PAGE });
            const el = scrollRef.current;
            preserveRef.current = el ? el.scrollHeight - el.scrollTop : null;
            setMessages((prev) => mergeMessages(data.items, prev));
            setHasMoreBefore(Boolean(data.has_more_before));
        } catch {
            // user can scroll up again to retry
        } finally {
            setLoadingOlder(false);
        }
    }, [conversationId, hasMoreBefore, loadingOlder]);

    const onScroll = () => {
        const el = scrollRef.current;
        if (!el) return;
        const near = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
        nearBottomRef.current = near;
        if (near) {
            setShowJump(false);
            markRead();
        }
        if (el.scrollTop < 80) loadOlder();
    };

    const send = useCallback(async (text, existingClientId) => {
        const clientMsgId = existingClientId || newClientMsgId();
        const pending = {
            client_msg_id: clientMsgId,
            body: text,
            sender_id: myId,
            created_at: new Date().toISOString(),
            status: 'sending',
        };
        nearBottomRef.current = true;
        setMessages((prev) => mergeMessages(prev.filter((m) => m.client_msg_id !== clientMsgId || Number.isInteger(m.seq)), [pending]));
        try {
            const saved = await chatApi.send(conversationId, text, clientMsgId);
            // Sending advances my read position on the server; don't re-send it.
            readSentRef.current = Math.max(readSentRef.current, saved.seq);
            setMessages((prev) => mergeMessages(prev, [saved]));
            onActivity?.();
        } catch (err) {
            const error = apiErrorMessage(err, t('Not sent'));
            setMessages((prev) => prev.map((m) => (
                m.client_msg_id === clientMsgId && !Number.isInteger(m.seq) ? { ...m, status: 'failed', error } : m
            )));
        }
    }, [conversationId, myId, onActivity, t]);

    const sendFile = useCallback(async (file, caption, existingClientId) => {
        const clientMsgId = existingClientId || newClientMsgId();
        const category = fileCategory(file.name);
        let previewUrl = previewUrlsRef.current.get(clientMsgId) || null;
        if (!previewUrl && (category === 'image' || category === 'video')) {
            previewUrl = URL.createObjectURL(file);
            previewUrlsRef.current.set(clientMsgId, previewUrl);
        }
        const pending = {
            client_msg_id: clientMsgId,
            body: caption,
            sender_id: myId,
            created_at: new Date().toISOString(),
            status: 'sending',
            progress: 0,
            file,
            attachment: { category, filename: file.name, size: file.size, url: previewUrl },
        };
        const update = (patch) => setMessages((prev) => prev.map((m) => (
            m.client_msg_id === clientMsgId && !Number.isInteger(m.seq) ? { ...m, ...patch } : m
        )));
        nearBottomRef.current = true;
        setMessages((prev) => mergeMessages(prev.filter((m) => m.client_msg_id !== clientMsgId || Number.isInteger(m.seq)), [pending]));

        const controller = new AbortController();
        uploadsRef.current.set(clientMsgId, controller);
        try {
            const saved = await uploadChatFile(conversationId, file, {
                caption,
                clientMsgId,
                signal: controller.signal,
                onProgress: (fraction) => update({ progress: fraction }),
            });
            readSentRef.current = Math.max(readSentRef.current, saved.seq);
            setMessages((prev) => mergeMessages(prev, [saved]));
            dropPreview(clientMsgId);
            onActivity?.();
        } catch (err) {
            if (err.aborted) {
                setMessages((prev) => prev.filter((m) => m.client_msg_id !== clientMsgId || Number.isInteger(m.seq)));
                dropPreview(clientMsgId);
                return;
            }
            update({ status: 'failed', error: apiErrorMessage(err, t('Not sent')) });
        } finally {
            uploadsRef.current.delete(clientMsgId);
        }
    }, [conversationId, dropPreview, myId, onActivity, t]);

    const cancelUpload = useCallback((clientMsgId) => {
        uploadsRef.current.get(clientMsgId)?.abort();
    }, []);

    const days = useMemo(() => groupMessages(messages), [messages]);
    const lastMineSeq = useMemo(() => {
        for (let i = messages.length - 1; i >= 0; i -= 1) {
            const m = messages[i];
            if (m.sender_id === myId && Number.isInteger(m.seq)) return m.seq;
        }
        return 0;
    }, [messages, myId]);
    const typingId = typing[conversationId];
    const isTyping = Boolean(typingId) && typingId !== myId && (isTeam || typingId === other?.id);
    const typingName = membersById.get(typingId)?.full_name;

    return (
        <section className="flex h-full min-h-0 flex-col bg-page" aria-label={isTeam ? (conversation.team?.name || t('Team')) : (other?.full_name || t('Chat'))}>
            <header className="flex items-center gap-3 border-b border-border bg-surface px-4 py-3">
                {onBack && (
                    <button type="button" onClick={onBack} className="-ml-1 rounded p-1 text-muted hover:text-primary md:hidden" aria-label={t('Back')}>
                        <ArrowLeft size={18} />
                    </button>
                )}
                <Avatar
                    name={isTeam ? conversation.team?.name : other?.full_name}
                    group={isTeam}
                    online={peerOnline}
                    inactive={!isTeam && !otherActive}
                />
                <div className="min-w-0">
                    <p className="truncate text-[14px] font-semibold text-primary">
                        {isTeam ? (conversation.team?.name || t('Team')) : (other?.full_name || '—')}
                    </p>
                    <p className="truncate text-[12px] text-muted">
                        {isTeam
                            ? (isTyping
                                ? (typingName ? `${typingName} · ${t('typing…')}` : t('typing…'))
                                : [
                                    t('{count} members').replace('{count}', String(conversation.team?.member_count || (conversation.members || []).length)),
                                    teamOnline > 0 ? t('{count} online').replace('{count}', String(teamOnline)) : null,
                                ].filter(Boolean).join(' · '))
                            : (!otherActive
                                ? t('Deactivated')
                                : isTyping
                                    ? t('typing…')
                                    : [t(roleLabel(other?.role)), other?.team, peerOnline ? t('Online') : null].filter(Boolean).join(' · '))}
                    </p>
                </div>
                {!isTeam && callsEnabled && (
                    <button
                        type="button"
                        onClick={() => startCall(conversation)}
                        disabled={!canCall || !otherActive}
                        title={!otherActive ? t('This person has been deactivated') : live !== 'live' ? t('Calls need a live connection') : t('Voice call')}
                        aria-label={`${t('Voice call')} ${other?.full_name || ''}`}
                        className="btn btn-ghost ml-auto h-9 w-9 shrink-0 p-0 disabled:opacity-40"
                    >
                        <Phone size={17} />
                    </button>
                )}
            </header>

            <div ref={scrollRef} onScroll={onScroll} className="relative min-h-0 flex-1 overflow-y-auto px-4 py-4" aria-live="polite">
                {loadState === 'loading' && (
                    <div className="flex h-full items-center justify-center">
                        <Loader2 className="h-6 w-6 animate-spin text-accent" aria-label={t('Loading')} />
                    </div>
                )}
                {loadState === 'error' && (
                    <div className="flex h-full flex-col items-center justify-center gap-3">
                        <p className="text-[13px] text-error">{loadError}</p>
                        <button type="button" onClick={() => setRetry((n) => n + 1)} className="btn btn-primary">{t('Retry')}</button>
                    </div>
                )}
                {loadState === 'ready' && messages.length === 0 && (
                    <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
                        <Avatar name={isTeam ? conversation.team?.name : other?.full_name} group={isTeam} inactive={!isTeam && !otherActive} />
                        <p className="text-[13px] text-muted">
                            {isTeam ? t('This chat is only for people on this team.') : t('Say hello to start the conversation.')}
                        </p>
                    </div>
                )}
                {loadState === 'ready' && messages.length > 0 && (
                    <>
                        {hasMoreBefore && (
                            <div className="mb-3 flex justify-center">
                                <button type="button" onClick={loadOlder} disabled={loadingOlder} className="btn btn-ghost text-[12px]">
                                    {loadingOlder ? <Loader2 className="h-4 w-4 animate-spin" /> : t('Load older messages')}
                                </button>
                            </div>
                        )}
                        {days.map((day) => (
                            <div key={day.key} className="space-y-3">
                                <div className="sticky top-0 z-10 flex justify-center py-1">
                                    <span className="rounded-full border border-border bg-surface px-3 py-0.5 text-[11px] font-medium text-muted">
                                        {t(dayLabel(day.date))}
                                    </span>
                                </div>
                                {day.runs.map((run) => {
                                    const mine = run.sender_id === myId;
                                    const senderName = membersById.get(run.sender_id)?.full_name;
                                    return (
                                        <div key={`${run.messages[0].seq ?? run.messages[0].client_msg_id}`} className={`flex flex-col gap-0.5 ${mine ? 'items-end' : 'items-start'}`}>
                                            {isTeam && !mine && !run.messages[0].call && (
                                                <span className="px-1 text-[11px] font-medium text-muted">{senderName || t('Teammate')}</span>
                                            )}
                                            {run.messages.map((m, idx) => {
                                                const last = idx === run.messages.length - 1;
                                                if (m.call) {
                                                    const entry = callEntryLabel(m.call, myId);
                                                    const Icon = entry.missed ? PhoneMissed : entry.outgoing ? PhoneOutgoing : PhoneIncoming;
                                                    return (
                                                        <div key={m.seq ?? m.client_msg_id} className="flex w-full justify-center py-1">
                                                            <span className={`inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-[12px] ${entry.missed ? 'text-error' : 'text-muted'}`}>
                                                                <Icon size={13} aria-hidden="true" />
                                                                {t(entry.text)}
                                                                {entry.duration ? ` · ${entry.duration}` : ''}
                                                                <span className="text-muted">· {timeLabel(m.created_at)}</span>
                                                            </span>
                                                        </div>
                                                    );
                                                }
                                                return (
                                                    <div key={m.seq ?? m.client_msg_id} className={`flex max-w-[78%] flex-col gap-1 ${mine ? 'items-end' : 'items-start'}`}>
                                                        {m.attachment && (
                                                            <div className={m.status === 'failed' ? 'opacity-60' : ''}>
                                                                <Attachment attachment={m.attachment} mine={mine} onBroken={onBrokenAttachment} />
                                                            </div>
                                                        )}
                                                        {m.attachment && m.status === 'sending' && (
                                                            <div className="flex items-center gap-2 px-1 text-[11px] text-muted">
                                                                <span>{t('Uploading…')} {Math.round((m.progress || 0) * 100)}%</span>
                                                                <button type="button" onClick={() => cancelUpload(m.client_msg_id)} className="inline-flex items-center gap-0.5 hover:text-primary" aria-label={t('Cancel')}>
                                                                    <X size={12} /> {t('Cancel')}
                                                                </button>
                                                            </div>
                                                        )}
                                                        {(m.unavailable || m.body || !m.attachment) && (
                                                            <div
                                                                className={`whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-[14px] leading-relaxed ${
                                                                    mine
                                                                        ? `bg-accent text-inverse ${last ? 'rounded-br-md' : ''} ${m.status === 'failed' ? 'opacity-60' : ''}`
                                                                        : `border border-border bg-surface text-primary ${last ? 'rounded-bl-md' : ''}`
                                                                }`}
                                                            >
                                                                {m.unavailable ? <span className="italic opacity-70">{t('Message unavailable')}</span> : m.body}
                                                            </div>
                                                        )}
                                                        {last && !(m.attachment && m.status === 'sending') && (
                                                            <div className="mt-0.5 flex items-center gap-1 px-1 text-[11px] text-muted">
                                                                {m.status === 'failed' ? (
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => (m.file ? sendFile(m.file, m.body, m.client_msg_id) : send(m.body, m.client_msg_id))}
                                                                        className="inline-flex items-center gap-1 text-error hover:underline"
                                                                    >
                                                                        <AlertCircle size={12} /> {m.error || t('Not sent')} · {t('Retry')}
                                                                    </button>
                                                                ) : m.status === 'sending' ? (
                                                                    <span>{t('Sending…')}</span>
                                                                ) : (
                                                                    <>
                                                                        <span>{timeLabel(m.created_at)}</span>
                                                                        {mine && !isTeam && m.seq === lastMineSeq && (
                                                                            otherLastRead >= m.seq
                                                                                ? <span className="inline-flex items-center gap-0.5 text-accent"><CheckCheck size={13} /> {t('Seen')}</span>
                                                                                : <Check size={13} aria-label={t('Sent')} />
                                                                        )}
                                                                    </>
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    );
                                })}
                            </div>
                        ))}
                        {isTyping && (
                            <div className="mt-3 flex items-center gap-1 text-[12px] text-muted" aria-label={t('typing…')}>
                                <span className="inline-flex gap-1 rounded-2xl border border-border bg-surface px-3 py-2">
                                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.2s]" />
                                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.1s]" />
                                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" />
                                </span>
                            </div>
                        )}
                    </>
                )}
            </div>

            {showJump && (
                <div className="pointer-events-none relative">
                    <button
                        type="button"
                        onClick={() => scrollToBottom(true)}
                        className="pointer-events-auto absolute -top-12 left-1/2 inline-flex -translate-x-1/2 items-center gap-1 rounded-full border border-border bg-surface px-3 py-1 text-[12px] font-medium text-primary shadow-sm"
                    >
                        <ArrowDown size={13} /> {t('New messages')}
                    </button>
                </div>
            )}

            {canCompose ? (
                <Composer
                    conversationId={conversationId}
                    maxChars={maxChars}
                    files={files}
                    onSend={(text) => send(text)}
                    onSendFile={(file, caption) => sendFile(file, caption)}
                    onTyping={() => sendTyping(conversationId)}
                />
            ) : (
                <p className="border-t border-border bg-surface px-4 py-3 text-center text-[12px] text-muted">
                    {t('This person has been deactivated. You can read past messages but not send new ones.')}
                </p>
            )}
        </section>
    );
}
