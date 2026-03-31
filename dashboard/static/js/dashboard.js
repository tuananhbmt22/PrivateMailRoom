/* Kajima Mailroom Dashboard — Client-side logic */

const API = {
    state: () => fetch('/api/state').then(r => r.json()),
    folder: (key) => fetch(`/api/folder/${key}`).then(r => r.json()),
    event: (folderKey, eventId) => fetch(`/api/event/${folderKey}/${eventId}`).then(r => r.json()),
};

let currentState = null;
let previousCounts = {};

/* ── Initialization ── */

document.addEventListener('DOMContentLoaded', () => {
    loadState();
    setupSSE();
    setupPanelClose();
    setupModalClose();
});

/* ── State Loading ── */

async function loadState() {
    try {
        currentState = await API.state();
        renderFolderGrid(currentState.folders);
        renderPending(currentState.pending, currentState.pending_count);
        renderEventLog(currentState.event_log);
        updateStats(currentState);
        previousCounts = buildCountMap(currentState.folders);
    } catch (err) {
        console.error('Failed to load state:', err);
    }
}

function buildCountMap(folders) {
    const map = {};
    folders.forEach(f => map[f.key] = f.count);
    return map;
}

/* ── SSE Real-time Updates ── */

function setupSSE() {
    const source = new EventSource('/api/stream');
    source.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'update') {
            handleRealtimeUpdate(data);
        }
    };
    source.onerror = () => {
        document.getElementById('liveIndicator').style.opacity = '0.3';
    };
    source.onopen = () => {
        document.getElementById('liveIndicator').style.opacity = '1';
    };
}

function handleRealtimeUpdate(data) {
    // Check for new items in folders
    const newCounts = data.counts;
    for (const [key, count] of Object.entries(newCounts)) {
        const prev = previousCounts[key] || 0;
        if (count > prev) {
            highlightFolder(key);
        }
    }
    previousCounts = newCounts;

    // Update counts on cards
    updateFolderCounts(newCounts);

    // Update pending
    const pendingEl = document.getElementById('pendingCount');
    if (pendingEl) pendingEl.textContent = data.pending_count;

    // Refresh full state for event log
    loadState();
}

/* ── Folder Grid ── */

let currentFolderOrder = [];
let savedFolderOrder = [];
let activeTemplateName = 'alphabetical';
let layoutDirty = false;

function renderFolderGrid(folders) {
    const grid = document.getElementById('folderGrid');
    grid.innerHTML = '';

    let orderedFolders;

    if (currentFolderOrder.length > 0) {
        // Custom order from template or drag
        const orderMap = {};
        currentFolderOrder.forEach((key, i) => orderMap[key] = i);
        orderedFolders = [...folders].sort((a, b) => {
            const ai = orderMap[a.key] !== undefined ? orderMap[a.key] : 999;
            const bi = orderMap[b.key] !== undefined ? orderMap[b.key] : 999;
            return ai - bi;
        });
    } else {
        // Default: Junk first, Undetermined second, rest alphabetical
        const junk = folders.find(f => f.key === 'junk');
        const undetermined = folders.find(f => f.key === 'undetermined');
        const rest = folders.filter(f => f.key !== 'junk' && f.key !== 'undetermined');
        rest.sort((a, b) => a.name.localeCompare(b.name));
        orderedFolders = [];
        if (junk) orderedFolders.push(junk);
        if (undetermined) orderedFolders.push(undetermined);
        orderedFolders.push(...rest);
    }

    // Store current order
    currentFolderOrder = orderedFolders.map(f => f.key);

    // Save snapshot if this is a clean render (not from a drag)
    if (savedFolderOrder.length === 0 || !layoutDirty) {
        savedFolderOrder = [...currentFolderOrder];
    }

    const total = orderedFolders.length;

    // Place all folders in circle — all draggable
    orderedFolders.forEach((folder, i) => {
        const angle = (i / total) * 2 * Math.PI - Math.PI / 2;
        const xPct = 50 + 38 * Math.cos(angle);
        const yPct = 50 + 42 * Math.sin(angle);

        const isUndetermined = folder.key === 'undetermined';
        const isJunk = folder.key === 'junk';
        const card = createFolderCard(folder, isUndetermined);
        card.style.left = `${xPct}%`;
        card.style.top = `${yPct}%`;
        card.style.transform = 'translate(-50%, -50%)';
        card.setAttribute('draggable', 'true');
        card.dataset.orderIndex = i;
        setupDragHandlers(card);
        grid.appendChild(card);
    });

    setTimeout(drawRouteLines, 50);
}

function createFolderCard(folder, isUndetermined) {
    const card = document.createElement('div');
    card.className = `folder-card${folder.count > 0 ? ' has-items' : ''}${isUndetermined ? ' undetermined' : ''}`;
    card.dataset.key = folder.key;
    card.onclick = () => openFolderPanel(folder.key);

    const prefix = isUndetermined ? '❓ ' : '';
    card.innerHTML = `
        <div class="folder-name">${prefix}${escapeHtml(folder.name)}</div>
        <div class="folder-count ${folder.count === 0 ? 'empty' : ''}" id="count-${folder.key}">${folder.count}</div>
        <div class="folder-count-label">events</div>
        <div class="folder-badge">+${folder.count}</div>
    `;
    return card;
}

function updateFolderCounts(counts) {
    for (const [key, count] of Object.entries(counts)) {
        const el = document.getElementById(`count-${key}`);
        if (el) {
            el.textContent = count;
            el.className = `folder-count${count === 0 ? ' empty' : ''}`;
        }

        // Update card class
        const card = document.querySelector(`.folder-card[data-key="${key}"]`);
        if (card) {
            card.classList.toggle('has-items', count > 0);
        }
    }
}

function highlightFolder(key) {
    const card = document.querySelector(`.folder-card[data-key="${key}"]`);
    if (card) {
        card.classList.add('highlight');
        setTimeout(() => card.classList.remove('highlight'), 7000);
    }
}

/* ── Pending List ── */

function renderPending(pending, count) {
    document.getElementById('pendingCount').textContent = count;

    // Update receiver box active state
    const receiverBox = document.getElementById('receiverBox');
    if (receiverBox) {
        receiverBox.classList.toggle('active', count > 0);
    }
}

/* ── Event Log ── */

function renderEventLog(entries) {
    const list = document.getElementById('eventLogList');
    list.innerHTML = '';

    entries.forEach(entry => {
        const row = document.createElement('div');
        row.className = 'event-entry';

        const isUndetermined = entry.outcome === 'Undetermined';
        const statusIcon = isUndetermined ? '❓' : '✓';
        const time = entry.classified_at ? formatTime(entry.classified_at) : '';
        const confidence = entry.confidence != null ? entry.confidence.toFixed(2) : '—';
        const subject = resolveDisplayTitle(entry);

        row.innerHTML = `
            <span class="event-status">${statusIcon}</span>
            <span class="event-subject" title="${escapeHtml(entry.event_id)}">${escapeHtml(subject)}</span>
            <span class="event-arrow">→</span>
            <span class="event-outcome">${escapeHtml(entry.outcome)}</span>
            <span class="event-confidence">${confidence}</span>
            <span class="event-time">${time}</span>
            <button class="event-receipt-btn" onclick="event.stopPropagation(); showReceipt(${escapeAttr(JSON.stringify(entry))})">📋</button>
        `;

        row.onclick = () => {
            if (entry._folder_key) {
                openFolderPanel(entry._folder_key);
            }
        };

        list.appendChild(row);
    });
}

/* ── Folder Panel ── */

async function openFolderPanel(folderKey) {
    const panel = document.getElementById('detailPanel');
    const overlay = document.getElementById('panelOverlay');

    try {
        const data = await API.folder(folderKey);

        document.getElementById('panelTitle').textContent = data.name;
        document.getElementById('panelDescription').textContent = data.description || 'No description';

        const eventsContainer = document.getElementById('panelEvents');
        eventsContainer.innerHTML = '';

        if (data.events.length === 0) {
            eventsContainer.innerHTML = '<div style="color: var(--text-dim); font-size: 13px; padding: 20px 0; text-align: center;">No events in this folder</div>';
        } else {
            data.events.forEach(ev => {
                const card = document.createElement('div');
                card.className = 'panel-event';

                const files = ev.files.filter(f => !f.startsWith('_')).join(', ');
                const confidence = ev.receipt ? ev.receipt.confidence : null;
                const subject = ev.receipt ? resolveDisplayTitle(ev.receipt) : (ev.subject || ev.event_id);
                const sender = ev.sender || '';
                const fileCount = ev.file_count || 0;

                const isJunkFolder = folderKey === 'junk';
                const isUndeterminedFolder = folderKey === 'undetermined';

                let actionsHtml = '';
                if (isJunkFolder) {
                    actionsHtml = `
                        <button class="btn-event-action confirm-junk" onclick="event.stopPropagation(); openJunkConfirm('${folderKey}', '${ev.event_id}')">✓ Confirm Junk</button>
                        <button class="btn-event-action" onclick="event.stopPropagation(); openCorrectionModal('redirect', '${folderKey}', '${ev.event_id}')">↗ Not Junk</button>
                        <button class="btn-event-action history" onclick="event.stopPropagation(); showEventHistory('${folderKey}', '${ev.event_id}')">📜</button>
                    `;
                } else {
                    actionsHtml = `
                        <button class="btn-event-action" onclick="event.stopPropagation(); openCorrectionModal('redirect', '${folderKey}', '${ev.event_id}')">↗ Reassign</button>
                        <button class="btn-event-action requeue" onclick="event.stopPropagation(); requeueEvent('${folderKey}', '${ev.event_id}')">⏪ Requeue</button>
                        <button class="btn-event-action history" onclick="event.stopPropagation(); showEventHistory('${folderKey}', '${ev.event_id}')">📜</button>
                    `;
                }

                card.innerHTML = `
                    <div class="panel-event-subject">${escapeHtml(subject)}</div>
                    <div class="panel-event-meta">
                        ${sender ? `<span>From: ${escapeHtml(sender)}</span> · ` : ''}
                        <span>${fileCount} file${fileCount !== 1 ? 's' : ''}</span>
                        ${confidence !== null ? ` · <span class="panel-event-confidence">Confidence: ${confidence}</span>` : ''}
                    </div>
                    <div class="panel-event-id-small" title="${ev.event_id}">${ev.event_id}</div>
                    <div class="panel-event-actions">${actionsHtml}</div>
                `;

                card.onclick = () => {
                    if (ev.receipt) showReceipt(ev.receipt);
                };

                eventsContainer.appendChild(card);
            });
        }

        panel.classList.add('open');
        overlay.classList.add('open');
    } catch (err) {
        console.error('Failed to load folder:', err);
    }
}

function setupPanelClose() {
    document.getElementById('panelClose').onclick = closePanel;
    document.getElementById('panelOverlay').onclick = closePanel;
}

function closePanel() {
    document.getElementById('detailPanel').classList.remove('open');
    document.getElementById('panelOverlay').classList.remove('open');
}

/* ── Receipt Modal ── */

let devMode = localStorage.getItem('devMode') === 'true';

function showReceipt(receipt) {
    const modal = document.getElementById('modalOverlay');
    const body = document.getElementById('modalBody');
    const title = document.getElementById('modalTitle');

    if (devMode) {
        // Developer mode: raw JSON
        title.textContent = `Raw Payload — ${receipt.event_id || 'Event'}`;
        body.innerHTML = '';
        body.textContent = JSON.stringify(receipt, null, 2);
        body.style.whiteSpace = 'pre-wrap';
    } else {
        // Staff mode: human-friendly card
        const subject = resolveDisplayTitle(receipt);
        const sender = receipt._sender || '';
        const outcome = receipt.outcome || 'Unknown';
        const confidence = receipt.confidence != null ? Math.round(receipt.confidence * 100) : 0;
        const reasoning = receipt.reasoning || '';
        const files = receipt.linked_files || [];
        const time = receipt.classified_at ? new Date(receipt.classified_at).toLocaleString('en-AU') : '';

        const confClass = confidence >= 80 ? 'high' : confidence >= 60 ? 'medium' : 'low';

        title.textContent = subject;
        body.innerHTML = `<div class="event-detail">
            <div class="event-detail-grid">
                <span class="event-detail-label">Filed to</span>
                <span class="event-detail-value">${escapeHtml(outcome)}</span>

                <span class="event-detail-label">Confidence</span>
                <span class="event-detail-value">
                    <span class="event-detail-confidence">
                        <span class="confidence-bar"><span class="confidence-fill ${confClass}" style="width:${confidence}%"></span></span>
                        ${confidence}%
                    </span>
                </span>

                ${sender ? `<span class="event-detail-label">From</span><span class="event-detail-value">${escapeHtml(sender)}</span>` : ''}

                ${time ? `<span class="event-detail-label">Processed</span><span class="event-detail-value">${time}</span>` : ''}

                <span class="event-detail-label">Documents</span>
                <span class="event-detail-value">${files.length} file${files.length !== 1 ? 's' : ''}</span>
            </div>

            ${reasoning ? `<div class="event-detail-reasoning">"${escapeHtml(reasoning)}"</div>` : ''}

            <div class="event-detail-files">
                <div class="event-detail-files-title">Attached Files</div>
                ${files.filter(f => !f.startsWith('_')).map(f => {
                    const icon = f.endsWith('.pdf') ? '📄' : f.endsWith('.png') || f.endsWith('.jpg') ? '🖼' : '📎';
                    return `<div class="event-file-item"><span class="event-file-icon">${icon}</span>${escapeHtml(f)}</div>`;
                }).join('')}
            </div>

            <div class="event-detail-actions">
                <button class="btn btn-primary" onclick="draftReply('${escapeHtml(receipt.event_id || '')}')">✉ Draft Reply</button>
                <button class="btn btn-skip" onclick="closeModal()">Close</button>
            </div>
        </div>`;
        body.style.whiteSpace = 'normal';
    }

    modal.classList.add('open');
}

function draftReply(eventId) {
    // Find the folder key from the current panel context
    const panelTitle = document.getElementById('panelTitle').textContent;
    const folderKey = findFolderKeyByName(panelTitle);

    if (!folderKey) {
        alert('Cannot determine department for this event.');
        return;
    }

    // Show loading in the modal
    const modal = document.getElementById('modalOverlay');
    const body = document.getElementById('modalBody');
    const title = document.getElementById('modalTitle');

    title.textContent = '✉ Drafting Reply...';
    body.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted)"><div class="activity-spinner" style="width:24px;height:24px;border-width:3px;margin:0 auto 12px"></div>Analysing with department skill...<br>This may take 30-60 seconds.</div>';
    body.style.whiteSpace = 'normal';
    modal.classList.add('open');

    fetch(`/api/event/${folderKey}/${eventId}/draft-reply`, { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                const analysis = data.analysis || {};
                title.textContent = `✉ Draft Reply — ${analysis.request_type || 'Response'}`;

                body.innerHTML = `<div class="event-detail">
                    <div class="draft-section">
                        <div class="draft-section-title">📊 Skill Analysis</div>
                        <div class="draft-analysis-grid">
                            <span class="event-detail-label">Request Type</span>
                            <span class="event-detail-value">${escapeHtml(analysis.request_type || 'unknown')}</span>
                            <span class="event-detail-label">Outcome</span>
                            <span class="event-detail-value">${escapeHtml(analysis.outcome || 'unknown')}</span>
                            <span class="event-detail-label">Template</span>
                            <span class="event-detail-value">${escapeHtml(analysis.response_template || 'General')}</span>
                        </div>
                        ${analysis.analysis ? `<div class="event-detail-reasoning">"${escapeHtml(analysis.analysis)}"</div>` : ''}
                        ${analysis.missing_info && analysis.missing_info.length > 0 ? `<div class="draft-missing">⚠ Missing: ${analysis.missing_info.map(m => escapeHtml(m)).join(', ')}</div>` : ''}
                    </div>
                    <div class="draft-section">
                        <div class="draft-section-title">✉ Draft Reply</div>
                        <textarea class="draft-textarea" id="draftReplyText" rows="12">${escapeHtml(data.draft_reply)}</textarea>
                    </div>
                    <div class="event-detail-actions">
                        <button class="btn btn-primary" onclick="copyDraft()">📋 Copy to Clipboard</button>
                        <button class="btn btn-skip" onclick="closeModal()">Close</button>
                    </div>
                </div>`;
            } else {
                title.textContent = '✉ Draft Failed';
                body.innerHTML = `<div style="padding:20px;color:var(--red)">${escapeHtml(data.error)}</div>`;
            }
        })
        .catch(err => {
            title.textContent = '✉ Draft Error';
            body.innerHTML = `<div style="padding:20px;color:var(--red)">${escapeHtml(err.message)}</div>`;
        });
}

function copyDraft() {
    const textarea = document.getElementById('draftReplyText');
    if (textarea) {
        textarea.select();
        navigator.clipboard.writeText(textarea.value).then(() => {
            const btn = event.target;
            btn.textContent = '✓ Copied';
            setTimeout(() => { btn.textContent = '📋 Copy to Clipboard'; }, 2000);
        });
    }
}

function toggleDevMode() {
    devMode = document.getElementById('settingsDevMode').checked;
    localStorage.setItem('devMode', devMode);
}

/* ── Event Display Settings (per-staff, localStorage) ── */

let eventDisplayMode = localStorage.getItem('eventDisplayMode') || 'raw';
let redactPii = localStorage.getItem('redactPii') === 'true';

function toggleEventDisplayMode() {
    eventDisplayMode = document.getElementById('settingsEventDisplay').value;
    localStorage.setItem('eventDisplayMode', eventDisplayMode);
    const redactRow = document.getElementById('redactPiiRow');
    if (redactRow) redactRow.style.display = eventDisplayMode === 'agent_title' ? '' : 'none';
}

function toggleRedactPii() {
    redactPii = document.getElementById('settingsRedactPii').checked;
    localStorage.setItem('redactPii', redactPii);
}

function resolveDisplayTitle(data) {
    if (devMode) return data.event_id || '';
    if (eventDisplayMode === 'agent_title') {
        if (redactPii && data.display_title_redacted) return data.display_title_redacted;
        if (data.display_title) return data.display_title;
    }
    return data._subject || data.event_id || '';
}

function setupModalClose() {
    document.getElementById('modalClose').onclick = closeModal;
    document.getElementById('modalOverlay').onclick = (e) => {
        if (e.target === document.getElementById('modalOverlay')) closeModal();
    };
}

function closeModal() {
    document.getElementById('modalOverlay').classList.remove('open');
}

/* ── Stats ── */

function updateStats(state) {
    document.getElementById('totalClassified').textContent = `${state.total_classified} classified`;
}

/* ── Utilities ── */

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function escapeAttr(str) {
    return str.replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

function formatTime(isoString) {
    try {
        const d = new Date(isoString);
        return d.toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit' });
    } catch {
        return '';
    }
}


/* ── Email Connection (Multi-Provider) ── */

async function checkEmailStatus() {
    try {
        const data = await fetch('/api/email/status').then(r => r.json());

        // Hide all panels
        hideAllEmailPanels();

        if (data.authenticated && data.username) {
            document.getElementById('emailConnected').style.display = 'block';
            document.getElementById('emailUserDisplay').textContent = data.username;
            document.getElementById('emailProviderSelect').style.display = 'none';
            updateEmailIndicator(true, data.username);
        } else if (data.configured && data.provider === 'microsoft' && !data.authenticated) {
            // Microsoft OAuth configured but not signed in
            document.getElementById('emailMsAuthenticated').style.display = 'block';
            document.getElementById('emailProviderSelect').style.display = 'none';
            updateEmailIndicator(false, '');
        } else {
            document.getElementById('emailProviderSelect').style.display = 'block';
            updateEmailIndicator(false, '');
        }
    } catch (err) {
        console.error('Failed to check email status:', err);
    }
}

function hideAllEmailPanels() {
    ['emailMicrosoft', 'emailMsAuthenticated', 'emailImap', 'emailConnected'].forEach(id => {
        document.getElementById(id).style.display = 'none';
    });
}

function selectProvider(provider) {
    hideAllEmailPanels();
    document.getElementById('emailProviderSelect').style.display = provider ? 'none' : 'block';

    if (provider === 'microsoft') {
        document.getElementById('emailMicrosoft').style.display = 'block';
    } else if (provider === 'gmail') {
        document.getElementById('emailImap').style.display = 'block';
        document.getElementById('imapProviderLabel').textContent = 'Gmail (IMAP)';
        document.getElementById('imapHost').value = 'imap.gmail.com';
        document.getElementById('imapPort').value = '993';
        document.getElementById('imapPasswordHint').style.display = 'block';
    } else if (provider === 'imap') {
        document.getElementById('emailImap').style.display = 'block';
        document.getElementById('imapProviderLabel').textContent = 'Custom IMAP';
        document.getElementById('imapHost').value = '';
        document.getElementById('imapPort').value = '993';
        document.getElementById('imapPasswordHint').style.display = 'none';
    }
}

function updateEmailIndicator(connected, username) {
    const dot = document.getElementById('emailDot');
    const text = document.getElementById('emailStatusText');
    if (connected) {
        dot.className = 'email-dot connected';
        text.textContent = `Mail: ${username}`;
    } else {
        dot.className = 'email-dot disconnected';
        text.textContent = 'Mail: Not Connected';
    }
}

function toggleEmailPanel() {
    const overlay = document.getElementById('emailPanelOverlay');
    overlay.classList.toggle('open');
    if (overlay.classList.contains('open')) {
        checkEmailStatus();
    }
}

async function saveOAuthConfig() {
    const status = document.getElementById('configFormStatus');
    status.className = 'form-status loading';
    status.textContent = 'Saving...';

    const payload = {
        client_id: document.getElementById('oauthClientId').value.trim(),
        tenant_id: document.getElementById('oauthTenantId').value.trim(),
        client_secret: document.getElementById('oauthClientSecret').value.trim(),
    };

    if (!payload.client_id || !payload.tenant_id || !payload.client_secret) {
        status.className = 'form-status error';
        status.textContent = 'All three fields are required.';
        return;
    }

    try {
        const resp = await fetch('/api/email/save-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();

        if (data.success) {
            status.className = 'form-status success';
            status.textContent = '✓ Saved.';
            setTimeout(() => checkEmailStatus(), 500);
        } else {
            status.className = 'form-status error';
            status.textContent = `✗ ${data.error}`;
        }
    } catch (err) {
        status.className = 'form-status error';
        status.textContent = `✗ ${err.message}`;
    }
}

async function signInMicrosoft() {
    document.getElementById('emailDot').className = 'email-dot connecting';
    try {
        const resp = await fetch('/api/email/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
        const data = await resp.json();
        if (data.success && data.auth_url) {
            window.location.href = data.auth_url;
        } else {
            alert(data.error || 'Failed to start sign-in');
            document.getElementById('emailDot').className = 'email-dot disconnected';
        }
    } catch (err) {
        alert('Connection error: ' + err.message);
        document.getElementById('emailDot').className = 'email-dot disconnected';
    }
}

async function connectImap() {
    const status = document.getElementById('imapFormStatus');
    status.className = 'form-status loading';
    status.textContent = 'Testing connection...';
    document.getElementById('emailDot').className = 'email-dot connecting';

    const provider = document.getElementById('imapProviderLabel').textContent.includes('Gmail') ? 'gmail' : 'imap';

    const payload = {
        imap_host: document.getElementById('imapHost').value,
        imap_port: parseInt(document.getElementById('imapPort').value),
        use_ssl: document.getElementById('useSsl').value === 'true',
        username: document.getElementById('imapUsername').value,
        password: document.getElementById('imapPassword').value,
        provider: provider,
    };

    try {
        const resp = await fetch('/api/email/imap-connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();

        if (data.success) {
            status.className = 'form-status success';
            status.textContent = `✓ ${data.message} — ${data.unread_count} unread email(s)`;
            setTimeout(() => {
                checkEmailStatus();
                toggleEmailPanel();
            }, 1500);
        } else {
            status.className = 'form-status error';
            status.textContent = `✗ ${data.error}`;
            document.getElementById('emailDot').className = 'email-dot disconnected';
        }
    } catch (err) {
        status.className = 'form-status error';
        status.textContent = `✗ ${err.message}`;
        document.getElementById('emailDot').className = 'email-dot disconnected';
    }
}

async function disconnectEmail() {
    try {
        await fetch('/api/email/disconnect', { method: 'POST' });
        updateEmailIndicator(false, '');
        checkEmailStatus();
        updateReceiverServices();
    } catch (err) {
        console.error('Failed to disconnect:', err);
    }
}

checkEmailStatus();

/* ── Receiver Service Icons ── */

const SERVICE_ICONS = {
    gmail: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 16.64l-6.545-4.91v9.273H1.636A1.636 1.636 0 0 1 0 19.366V5.457c0-2.023 2.309-3.178 3.927-1.964L5.455 4.64 12 9.548l6.545-4.91 1.528-1.145C21.69 2.28 24 3.434 24 5.457z"/></svg>`,
    microsoft: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M0 0h11.377v11.372H0zm12.623 0H24v11.372H12.623zM0 12.623h11.377V24H0zm12.623 0H24V24H12.623z"/></svg>`,
    imap: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>`,
};

function updateReceiverServices() {
    const container = document.getElementById('receiverServices');
    if (!container) return;

    fetch('/api/email/status')
        .then(r => r.json())
        .then(data => {
            container.innerHTML = '';
            if (data.authenticated && data.provider) {
                const provider = data.provider;
                const icon = SERVICE_ICONS[provider] || SERVICE_ICONS.imap;
                const label = provider === 'gmail' ? 'Gmail' : provider === 'microsoft' ? 'Outlook' : 'IMAP';

                const badge = document.createElement('div');
                badge.className = 'service-icon-badge connected';
                badge.innerHTML = `${icon}<span>${label}</span>`;
                container.appendChild(badge);
            }
        })
        .catch(() => {});
}

updateReceiverServices();

/* ── Inbox Poll & Classify ── */

let autoPollTimer = null;
let autoClassifyEnabled = false;
let isPolling = false;
let isClassifying = false;

function toggleAutoPoll() {
    const on = document.getElementById('autoPollToggle').checked;
    // Always clear any existing timer first
    if (autoPollTimer !== null) {
        clearInterval(autoPollTimer);
        autoPollTimer = null;
    }

    if (on) {
        updateServiceStatus('polling');
        pollInbox();
        autoPollTimer = setInterval(() => {
            if (!isPolling) pollInbox();
        }, 30000);
    } else {
        updateServiceStatus('stopped');
    }
}

function toggleAutoClassify() {
    autoClassifyEnabled = document.getElementById('autoClassifyToggle').checked;
    updateServiceStatus(autoPollTimer !== null ? 'polling' : 'stopped');

    // If toggled on and there are pending items, classify them now
    if (autoClassifyEnabled) {
        classifyAll(true);
    }
}

function stopAllServices() {
    // Stop poll timer
    if (autoPollTimer !== null) {
        clearInterval(autoPollTimer);
        autoPollTimer = null;
    }
    // Uncheck both toggles
    document.getElementById('autoPollToggle').checked = false;
    document.getElementById('autoClassifyToggle').checked = false;
    autoClassifyEnabled = false;
    updateServiceStatus('stopped');
}

function updateServiceStatus(state) {
    const el = document.getElementById('serviceStatus');
    if (state === 'polling' && autoClassifyEnabled) {
        el.className = 'service-badge active';
        el.textContent = '● Auto: Poll + Classify';
    } else if (state === 'polling') {
        el.className = 'service-badge active';
        el.textContent = '● Auto: Poll Only';
    } else {
        el.className = 'service-badge stopped';
        el.textContent = '○ Manual Mode';
    }
}

async function pollInbox() {
    if (isPolling) return;
    isPolling = true;

    const btn = document.getElementById('pollBtn');
    const status = document.getElementById('sidebarStatus');

    btn.disabled = true;
    btn.textContent = '📧 Polling...';
    status.className = 'sidebar-status loading';
    status.textContent = 'Checking inbox...';

    addPollLog('📡 Polling inbox...', 'info');
    setPollStatus('working', 'Connecting to mail server...');

    try {
        const resp = await fetch('/api/email/poll', { method: 'POST' });
        const data = await resp.json();

        if (data.success) {
            if (data.events_created > 0) {
                status.className = 'sidebar-status success';
                status.textContent = `✓ ${data.events_created} email(s) ingested`;

                addPollLog(`✓ ${data.events_created} email(s) ingested`, 'success');
                if (data.event_details && !devMode) {
                    data.event_details.forEach(d => addPollLog(`  → ${d.subject || d.event_id}`, ''));
                } else {
                    data.event_ids.forEach(id => addPollLog(`  → ${id}`, ''));
                }
                setPollStatus('active', `Last: ${data.events_created} email(s) ingested`);

                pulseReceiver();
                loadState();

                if (autoClassifyEnabled) {
                    await classifyAll(true);
                }
            } else {
                status.className = 'sidebar-status';
                status.textContent = 'No new emails';
                addPollLog('No new emails', '');
                setPollStatus('active', 'No new emails');

                if (autoClassifyEnabled) {
                    const pendingNow = parseInt(document.getElementById('pendingCount').textContent);
                    if (pendingNow > 0) {
                        await classifyAll(true);
                    }
                }
            }
        } else {
            status.className = 'sidebar-status error';
            status.textContent = `✗ ${data.error}`;
            addPollLog(`✗ ${data.error}`, 'error');
            setPollStatus('error', 'Error');
        }
    } catch (err) {
        status.className = 'sidebar-status error';
        status.textContent = `✗ ${err.message}`;
        addPollLog(`✗ ${err.message}`, 'error');
        setPollStatus('error', 'Error');
    }

    btn.disabled = false;
    btn.textContent = '📧 Poll Inbox';
    isPolling = false;
    setTimeout(() => { if (!isPolling && !isClassifying) status.textContent = ''; }, 5000);
}

async function classifyAll(skipPendingCheck) {
    if (isClassifying) return;
    isClassifying = true;

    const btn = document.getElementById('classifyBtn');
    const status = document.getElementById('sidebarStatus');

    if (!skipPendingCheck) {
        const pending = parseInt(document.getElementById('pendingCount').textContent);
        if (pending === 0) {
            status.className = 'sidebar-status';
            status.textContent = 'Nothing to classify';
            isClassifying = false;
            setTimeout(() => { status.textContent = ''; }, 3000);
            return;
        }
    }

    btn.disabled = true;
    btn.textContent = '🤖 Classifying...';
    status.className = 'sidebar-status loading';
    status.textContent = 'Classifying events...';

    addClassifyLog('🧠 Pipeline started...', 'info');
    setClassifyStatus('working', 'Processing...');
    setPipelineStatus('working', 'Running...');

    // Refresh state to get fresh pending list with subjects
    try {
        currentState = await API.state();
    } catch (e) {}

    // Get pending events from current state
    clearPipelineEvents();
    const pendingIds = [];
    const pendingSubjects = {};
    if (currentState && currentState.pending) {
        currentState.pending.forEach(p => {
            pendingIds.push(p.event_id);
            pendingSubjects[p.event_id] = p._subject || '';
            addPipelineEvent(p.event_id, 'matching', p._subject);
        });
    }

    // Check if skills are enabled
    let skillsEnabled = false;
    try {
        const settingsResp = await fetch('/api/settings').then(r => r.json());
        skillsEnabled = settingsResp.skills_enabled;
    } catch (e) {}

    const allResults = [];

    try {
        for (const eventId of pendingIds) {
            let skillMatched = null;
            let skillResult = null;
            let eventTitles = {};

            // Call 1: Skill Match (if enabled)
            if (skillsEnabled) {
                updatePipelineStage(eventId, 'matching', 'active');
                try {
                    const matchResp = await fetch(`/api/skill-match/${eventId}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ generate_title: eventDisplayMode === 'agent_title' }),
                    });
                    const matchData = await matchResp.json();
                    const sid = matchData.skill_id;
                    const conf = matchData.confidence || 0;

                    // Capture agent-generated titles from Call 1
                    if (matchData.display_title) {
                        eventTitles = {
                            display_title: matchData.display_title,
                            display_title_redacted: matchData.display_title_redacted || matchData.display_title,
                        };
                    }

                    const titleLabel = resolveDisplayTitle({
                        event_id: eventId,
                        _subject: pendingSubjects[eventId],
                        ...eventTitles,
                    });

                    // Immediately update pipeline card title after Call 1
                    const pipeCard = document.getElementById(`pipeline-${eventId}`);
                    if (pipeCard) {
                        const pipeTitle = pipeCard.querySelector('.pipeline-event-title');
                        if (pipeTitle && titleLabel !== eventId) {
                            pipeTitle.textContent = titleLabel;
                            pipeTitle.dataset.subject = titleLabel;
                        }
                    }

                    if (sid && sid !== 'none' && sid !== 'N/A' && conf >= 0.8) {
                        skillMatched = sid;
                        updatePipelineStage(eventId, 'matching', 'done', `✓ ${sid}`);
                        addClassifyLog(`🎯 ${titleLabel}: skill "${sid}" (${conf.toFixed(2)})`, 'info');

                        // Call 2: Scroll Execution
                        updatePipelineStage(eventId, 'scroll', 'active', `${sid}_scroll...`);
                        try {
                            const scrollResp = await fetch(`/api/skill-execute/${eventId}`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ skill_id: sid }),
                            });
                            skillResult = await scrollResp.json();
                            updatePipelineStage(eventId, 'scroll', 'done', `✓ ${sid}_scroll`);
                            const scrollLabel = resolveDisplayTitle({ event_id: eventId, _subject: pendingSubjects[eventId], ...eventTitles });
                            addClassifyLog(`📜 ${scrollLabel}: ${skillResult.request_type || '?'} → ${skillResult.outcome || '?'}`, 'info');
                        } catch (e) {
                            updatePipelineStage(eventId, 'scroll', 'done', '✗ error');
                        }
                    } else {
                        updatePipelineStage(eventId, 'matching', 'skipped', 'no match');
                        updatePipelineStage(eventId, 'scroll', 'skipped');
                    }
                } catch (e) {
                    updatePipelineStage(eventId, 'matching', 'skipped', 'error');
                    updatePipelineStage(eventId, 'scroll', 'skipped');
                }
            } else {
                updatePipelineStage(eventId, 'matching', 'skipped', 'off');
                updatePipelineStage(eventId, 'scroll', 'skipped', 'off');
            }

            // Call 3: Classification
            updatePipelineStage(eventId, 'classify', 'active');
            try {
                const classResp = await fetch(`/api/classify-single/${eventId}`, { method: 'POST' });
                const classData = await classResp.json();

                classData.skill_matched = skillMatched;
                classData.skill_request_type = skillResult ? skillResult.request_type : null;
                classData.skill_outcome = skillResult ? skillResult.outcome : null;
                classData.skill_analysis = skillResult ? skillResult.analysis : null;
                classData.skill_metadata = skillResult ? skillResult.metadata : null;
                classData.skill_confidence = skillResult ? skillResult.confidence : null;
                classData.skill_response_key = skillResult ? skillResult.response_template_key : null;
                classData._subject = pendingSubjects[eventId] || classData._subject || eventId;
                classData.display_title = eventTitles.display_title || '';
                classData.display_title_redacted = eventTitles.display_title_redacted || '';

                // Resolve the friendly label from all available sources
                const friendlyLabel = resolveDisplayTitle(classData);

                updatePipelineStage(eventId, 'classify', 'done');
                updatePipelineStage(eventId, 'dispatch', 'done');

                // Update pipeline title with subject
                const card = document.getElementById(`pipeline-${eventId}`);
                if (card) {
                    const title = card.querySelector('.pipeline-event-title');
                    if (title) {
                        // Update stored subject for future use
                        title.dataset.subject = friendlyLabel;
                        title.innerHTML = `${escapeHtml(friendlyLabel)} <span class="pipeline-arrow">→</span> <span style="color:var(--green)">${escapeHtml(classData.outcome)}</span>`;
                    }
                }
                pipelineResults[eventId] = classData;

                if (classData.outcome === 'Undetermined') {
                    addClassifyLog(`❓ ${friendlyLabel} → Undetermined`, 'error');
                    activateRoute('undetermined');
                } else {
                    addClassifyLog(`✓ ${friendlyLabel} → ${classData.outcome} (${classData.confidence})`, 'success');
                    const folderKey = findFolderKeyByName(classData.outcome);
                    if (folderKey) activateRoute(folderKey);
                }

                allResults.push(classData);

                // Persist agent titles into the classification receipt (fire-and-forget)
                if (eventTitles.display_title && classData.moved) {
                    const folderKey = findFolderKeyByName(classData.outcome);
                    fetch(`/api/save-titles/${eventId}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            folder_key: folderKey || 'undetermined',
                            display_title: eventTitles.display_title,
                            display_title_redacted: eventTitles.display_title_redacted,
                        }),
                    }).catch(() => {});
                }
            } catch (e) {
                updatePipelineStage(eventId, 'classify', 'done');
                const errLabel = resolveDisplayTitle({ event_id: eventId, _subject: pendingSubjects[eventId], ...eventTitles });
                addClassifyLog(`✗ ${errLabel}: ${e.message}`, 'error');
            }
        }

        const classified = allResults.filter(r => r.outcome !== 'Undetermined').length;
        const undetermined = allResults.length - classified;
        status.className = 'sidebar-status success';
        status.textContent = `✓ ${classified} classified, ${undetermined} undetermined`;
        setClassifyStatus('active', `Done: ${classified} classified`);
        setPipelineStatus('active', 'Complete');
        loadState();

    } catch (err) {
        status.className = 'sidebar-status error';
        status.textContent = `✗ ${err.message}`;
        setClassifyStatus('error', 'Error');
        setPipelineStatus('error', 'Failed');
    }

    btn.disabled = false;
    btn.textContent = '🤖 Classify';
    isClassifying = false;
    setTimeout(() => { if (!isPolling && !isClassifying) status.textContent = ''; }, 8000);
}

/* ── Settings ── */

function toggleSettingsPanel() {
    const overlay = document.getElementById('settingsPanelOverlay');
    overlay.classList.toggle('open');
    if (overlay.classList.contains('open')) {
        loadSettings();
    }
}

async function loadSettings() {
    try {
        const data = await fetch('/api/settings').then(r => r.json());
        document.getElementById('settingsOnboardDate').value = data.since_date || '';
        document.getElementById('settingsDevMode').checked = devMode;
        document.getElementById('settingsSkillsToggle').checked = data.skills_enabled || false;
        document.getElementById('settingsEventDisplay').value = eventDisplayMode;
        document.getElementById('settingsRedactPii').checked = redactPii;
        const redactRow = document.getElementById('redactPiiRow');
        if (redactRow) redactRow.style.display = eventDisplayMode === 'agent_title' ? '' : 'none';
    } catch (err) {
        console.error('Failed to load settings:', err);
    }
}

async function saveSettings() {
    const status = document.getElementById('settingsFormStatus');
    const sinceDate = document.getElementById('settingsOnboardDate').value;
    const skillsEnabled = document.getElementById('settingsSkillsToggle').checked;

    status.className = 'form-status loading';
    status.textContent = 'Saving...';

    try {
        const resp = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ since_date: sinceDate, skills_enabled: skillsEnabled }),
        });
        const data = await resp.json();

        if (data.success) {
            status.className = 'form-status success';
            status.textContent = `✓ Settings saved`;
            setTimeout(() => toggleSettingsPanel(), 1500);
        } else {
            status.className = 'form-status error';
            status.textContent = `✗ ${data.error}`;
        }
    } catch (err) {
        status.className = 'form-status error';
        status.textContent = `✗ ${err.message}`;
    }
}

/* ── Service Logs ── */

function timeNow() {
    return new Date().toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function addServiceLog(logId, msg, cls) {
    const log = document.getElementById(logId);
    const empty = log.querySelector('.activity-empty');
    if (empty) empty.remove();

    const item = document.createElement('div');
    item.className = 'service-log-item';
    item.innerHTML = `<span class="log-time">${timeNow()}</span><span class="log-msg ${cls || ''}">${msg}</span>`;
    log.insertBefore(item, log.firstChild);

    while (log.children.length > 30) log.removeChild(log.lastChild);
}

function addPollLog(msg, cls) { addServiceLog('pollLog', msg, cls); }
function addClassifyLog(msg, cls) { addServiceLog('classifyLog', msg, cls); }

function setPollStatus(state, text) {
    const bar = document.getElementById('pollStatusBar');
    const dot = document.getElementById('pollServiceDot');
    bar.textContent = text;
    bar.className = `service-status-bar ${state}`;
    dot.className = `service-status-dot ${state === 'active' ? 'running' : state === 'working' ? 'working' : ''}`;
}

function setClassifyStatus(state, text) {
    const bar = document.getElementById('classifyStatusBar');
    const dot = document.getElementById('classifyServiceDot');
    bar.textContent = text;
    bar.className = `service-status-bar ${state}`;
    dot.className = `service-status-dot ${state === 'active' ? 'running' : state === 'working' ? 'working' : ''}`;
}

/* ── Route Lines (SVG) ── */

function drawRouteLines() {
    const svg = document.getElementById('routeLines');
    const receiver = document.getElementById('receiverBox');
    const map = document.getElementById('folderMap');

    if (!svg || !receiver || !map) return;

    svg.innerHTML = '';

    // Set SVG to match container size
    const mapRect = map.getBoundingClientRect();
    svg.setAttribute('width', mapRect.width);
    svg.setAttribute('height', mapRect.height);

    const recvRect = receiver.getBoundingClientRect();
    const startX = recvRect.left + recvRect.width / 2 - mapRect.left;
    const startY = recvRect.top + recvRect.height / 2 - mapRect.top;

    document.querySelectorAll('.folder-card').forEach(card => {
        const cardRect = card.getBoundingClientRect();
        const endX = cardRect.left + cardRect.width / 2 - mapRect.left;
        const endY = cardRect.top + cardRect.height / 2 - mapRect.top;

        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', startX);
        line.setAttribute('y1', startY);
        line.setAttribute('x2', endX);
        line.setAttribute('y2', endY);
        line.setAttribute('class', 'route-line');
        line.setAttribute('data-folder', card.dataset.key);
        svg.appendChild(line);
    });
}

function activateRoute(folderKey) {
    const line = document.querySelector(`.route-line[data-folder="${folderKey}"]`);
    const card = document.querySelector(`.folder-card[data-key="${folderKey}"]`);
    const isUndetermined = folderKey === 'undetermined';

    // Activate the line
    if (line) {
        line.classList.add(isUndetermined ? 'active-amber' : 'active');
        setTimeout(() => line.classList.remove('active', 'active-amber'), 7000);
    }

    // Glow the folder card
    if (card) {
        card.classList.add(isUndetermined ? 'glow-amber' : 'glow-green');
        setTimeout(() => card.classList.remove('glow-green', 'glow-amber'), 7000);
    }
}

function findFolderKeyByName(name) {
    // Search all folder cards for one whose name matches
    const cards = document.querySelectorAll('.folder-card');
    for (const card of cards) {
        const nameEl = card.querySelector('.folder-name');
        if (nameEl) {
            const cardName = nameEl.textContent.replace('❓ ', '').trim();
            if (cardName === name) return card.dataset.key;
        }
    }
    return null;
}

function pulseReceiver() {
    const box = document.getElementById('receiverBox');
    if (!box) return;
    box.classList.add('pulse-green');
    setTimeout(() => box.classList.remove('pulse-green'), 5000);
}

let resizeTimer = null;
window.addEventListener('resize', () => {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(drawRouteLines, 50);
});

const _originalLoadState = loadState;
loadState = async function() {
    await _originalLoadState();
    setTimeout(drawRouteLines, 100);
};

/* ── Correction Wizard ── */

let pendingAction = null;
let wizardReason = null;
let selectedAiFailure = null;

function openCorrectionModal(actionType, folderKey, eventId) {
    pendingAction = { type: actionType, folderKey, eventId };
    wizardReason = null;
    selectedAiFailure = null;

    document.getElementById('correctionTitle').textContent = `Move: ${eventId}`;

    // Hide ALL wizard steps including dynamic ones
    ['wizStep1', 'wizStep2AI', 'wizStep2Other', 'wizStepRequeue', 'wizStepJunk'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });

    // Show step 1
    document.getElementById('wizStep1').style.display = 'block';

    document.getElementById('correctionOverlay').classList.add('open');
}

function closeCorrectionModal() {
    document.getElementById('correctionOverlay').classList.remove('open');
    pendingAction = null;
}

function selectReason(reason) {
    wizardReason = reason;
    document.getElementById('wizStep1').style.display = 'none';

    if (reason === 'ai_wrong') {
        // Show AI error step
        document.getElementById('wizStep2AI').style.display = 'block';
        document.querySelectorAll('.wizard-chip').forEach(c => c.classList.remove('selected'));
        selectedAiFailure = null;
        document.getElementById('wizExplanation').value = '';
        document.getElementById('wizStatus').textContent = '';
        populateWizFolders('wizRedirectFolder', pendingAction.folderKey);
    } else {
        // Show simple redirect/reverse step
        document.getElementById('wizStep2Other').style.display = 'block';
        document.getElementById('wizStatus2').textContent = '';
        populateWizFolders('wizRedirectFolder2', pendingAction.folderKey);

        // Show undo option if this is a reverse action or has history
        const showUndo = pendingAction.type === 'reverse';
        document.getElementById('wizReverseOption').style.display = showUndo ? 'flex' : 'none';
        document.getElementById('wizUndoBtn').style.display = showUndo ? 'block' : 'none';
    }
}

function wizardBack() {
    document.getElementById('wizStep2AI').style.display = 'none';
    document.getElementById('wizStep2Other').style.display = 'none';
    document.getElementById('wizStep1').style.display = 'block';
}

function selectChip(el) {
    document.querySelectorAll('.wizard-chip').forEach(c => c.classList.remove('selected'));
    el.classList.add('selected');
    selectedAiFailure = el.dataset.val;
}

function populateWizFolders(selectId, currentKey) {
    const select = document.getElementById(selectId);
    select.innerHTML = '<option value="">Select folder...</option>';
    if (currentState && currentState.folders) {
        currentState.folders.forEach(f => {
            if (f.key !== currentKey) {
                const opt = document.createElement('option');
                opt.value = f.key;
                opt.textContent = f.name;
                select.appendChild(opt);
            }
        });
    }
}

async function wizardSubmit() {
    // AI Error path
    const status = document.getElementById('wizStatus');
    const targetFolder = document.getElementById('wizRedirectFolder').value;

    if (!selectedAiFailure) {
        status.className = 'form-status error';
        status.textContent = 'Select what went wrong.';
        return;
    }
    if (!targetFolder) {
        status.className = 'form-status error';
        status.textContent = 'Select the correct folder.';
        return;
    }

    const correction = {
        correction_type: 'ai_wrong',
        ai_failure_reason: selectedAiFailure,
        correct_folder: targetFolder,
        explanation: document.getElementById('wizExplanation').value.trim(),
    };

    await doMove('redirect', targetFolder, correction, status);
}

async function wizardSubmitOther() {
    const status = document.getElementById('wizStatus2');
    const targetFolder = document.getElementById('wizRedirectFolder2').value;

    if (!targetFolder) {
        status.className = 'form-status error';
        status.textContent = 'Select a destination.';
        return;
    }

    const correction = { correction_type: wizardReason };
    await doMove('redirect', targetFolder, correction, status);
}

async function wizardSubmitReverse() {
    const status = document.getElementById('wizStatus2');
    const correction = { correction_type: wizardReason };
    await doMove('reverse', null, correction, status);
}

async function doMove(action, targetFolder, correction, statusEl) {
    statusEl.className = 'form-status loading';
    statusEl.textContent = 'Moving...';

    const { folderKey, eventId } = pendingAction;
    const url = `/api/event/${folderKey}/${eventId}/${action}`;
    const body = { correction, staff_name: 'admin' };
    if (targetFolder) body.target_folder = targetFolder;

    try {
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();

        if (data.success) {
            statusEl.className = 'form-status success';
            statusEl.textContent = `✓ Moved to ${data.to}`;
            activateRoute(data.to);
            setTimeout(() => {
                closeCorrectionModal();
                closePanel();
                loadState();
            }, 1200);
        } else {
            statusEl.className = 'form-status error';
            statusEl.textContent = `✗ ${data.error}`;
        }
    } catch (err) {
        statusEl.className = 'form-status error';
        statusEl.textContent = `✗ ${err.message}`;
    }
}

async function requeueEvent(folderKey, eventId) {
    pendingAction = { type: 'reverse', folderKey, eventId };

    document.getElementById('correctionTitle').textContent = 'Requeue for Review';

    // Hide ALL wizard steps
    ['wizStep1', 'wizStep2AI', 'wizStep2Other', 'wizStepRequeue', 'wizStepJunk'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });

    let requeueStep = document.getElementById('wizStepRequeue');
    if (!requeueStep) {
        requeueStep = document.createElement('div');
        requeueStep.id = 'wizStepRequeue';
        requeueStep.className = 'wizard-step';
        requeueStep.innerHTML = `
            <div class="wizard-prompt">Return to receive queue?</div>
            <p style="font-size:12px;color:var(--text-muted);margin-bottom:20px">
                This event will be sent back for re-processing or manual review.
            </p>
            <div class="wizard-actions">
                <button class="btn btn-skip" onclick="closeCorrectionModal()">Cancel</button>
                <button class="btn btn-primary" onclick="doRequeue()">Confirm Requeue</button>
            </div>
            <div class="form-status" id="wizStatusRequeue"></div>
        `;
        document.querySelector('.correction-modal').appendChild(requeueStep);
    }
    requeueStep.style.display = 'block';
    const statusEl = document.getElementById('wizStatusRequeue');
    if (statusEl) { statusEl.textContent = ''; statusEl.className = 'form-status'; }

    document.getElementById('correctionOverlay').classList.add('open');
}

async function doRequeue() {
    const statusEl = document.getElementById('wizStatusRequeue');
    statusEl.className = 'form-status loading';
    statusEl.textContent = 'Requeuing...';

    const { folderKey, eventId } = pendingAction;

    try {
        const resp = await fetch(`/api/event/${folderKey}/${eventId}/reverse`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                correction: { correction_type: 'requeued' },
                staff_name: 'admin',
            }),
        });
        const data = await resp.json();

        if (data.success) {
            statusEl.className = 'form-status success';
            statusEl.textContent = '✓ Returned to queue';
            pulseReceiver();
            setTimeout(() => {
                closeCorrectionModal();
                closePanel();
                loadState();
            }, 1000);
        } else {
            statusEl.className = 'form-status error';
            statusEl.textContent = `✗ ${data.error}`;
        }
    } catch (err) {
        statusEl.className = 'form-status error';
        statusEl.textContent = `✗ ${err.message}`;
    }
}

async function showEventHistory(folderKey, eventId) {
    try {
        const data = await fetch(`/api/event/${folderKey}/${eventId}/history`).then(r => r.json());

        const modal = document.getElementById('modalOverlay');
        const body = document.getElementById('modalBody');
        const title = document.getElementById('modalTitle');

        title.textContent = `Movement History — ${eventId}`;
        body.textContent = JSON.stringify(data, null, 2);
        modal.classList.add('open');
    } catch (err) {
        console.error('Failed to load history:', err);
    }
}

/* ── Junk Confirmation ── */

function openJunkConfirm(folderKey, eventId) {
    pendingAction = { type: 'junk', folderKey, eventId };

    document.getElementById('correctionTitle').textContent = 'Confirm Junk';

    // Hide all wizard steps
    document.getElementById('wizStep1').style.display = 'none';
    document.getElementById('wizStep2AI').style.display = 'none';
    document.getElementById('wizStep2Other').style.display = 'none';
    const requeueStep = document.getElementById('wizStepRequeue');
    if (requeueStep) requeueStep.style.display = 'none';

    // Create or show junk step
    let junkStep = document.getElementById('wizStepJunk');
    if (!junkStep) {
        junkStep = document.createElement('div');
        junkStep.id = 'wizStepJunk';
        junkStep.className = 'wizard-step';
        junkStep.innerHTML = `
            <div class="wizard-prompt">What type of junk is this?</div>
            <div class="wizard-chips" id="junkTypeChips">
                <button class="wizard-chip" data-val="marketing" onclick="selectChip(this)">📢 Marketing</button>
                <button class="wizard-chip" data-val="automated" onclick="selectChip(this)">🔔 Automated</button>
                <button class="wizard-chip" data-val="spam" onclick="selectChip(this)">🚫 Spam</button>
                <button class="wizard-chip" data-val="internal" onclick="selectChip(this)">🔄 Internal</button>
                <button class="wizard-chip" data-val="irrelevant" onclick="selectChip(this)">❌ Irrelevant</button>
            </div>
            <div class="wizard-field" style="margin-top:16px">
                <label class="junk-checkbox-row">
                    <input type="checkbox" id="junkNeverShow">
                    <div class="junk-checkbox-content">
                        <span class="junk-checkbox-label">Never show emails like this again</span>
                        <span class="junk-checkbox-hint">Creates a fingerprint to auto-filter similar emails in the future</span>
                    </div>
                </label>
            </div>
            <div class="wizard-actions">
                <button class="btn btn-skip" onclick="closeCorrectionModal()">Cancel</button>
                <button class="btn btn-primary" onclick="submitJunkConfirm()">Confirm Junk</button>
            </div>
            <div class="form-status" id="wizStatusJunk"></div>
        `;
        document.querySelector('.correction-modal').appendChild(junkStep);
    }

    // Reset
    junkStep.style.display = 'block';
    junkStep.querySelectorAll('.wizard-chip').forEach(c => c.classList.remove('selected'));
    document.getElementById('junkNeverShow').checked = false;
    const statusEl = document.getElementById('wizStatusJunk');
    if (statusEl) statusEl.textContent = '';

    document.getElementById('correctionOverlay').classList.add('open');
}

async function submitJunkConfirm() {
    const status = document.getElementById('wizStatusJunk');
    const selectedChip = document.querySelector('#wizStepJunk .wizard-chip.selected');

    if (!selectedChip) {
        status.className = 'form-status error';
        status.textContent = 'Select a junk type.';
        return;
    }

    status.className = 'form-status loading';
    status.textContent = 'Processing...';

    const { folderKey, eventId } = pendingAction;
    const junkType = selectedChip.dataset.val;
    const neverShow = document.getElementById('junkNeverShow').checked;

    try {
        const resp = await fetch(`/api/event/${folderKey}/${eventId}/confirm-junk`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                junk_type: junkType,
                never_show_again: neverShow,
                staff_name: 'admin',
            }),
        });
        const data = await resp.json();

        if (data.success) {
            status.className = 'form-status success';
            status.textContent = neverShow
                ? `✓ Junked & fingerprinted (${data.fingerprint_id})`
                : '✓ Confirmed as junk';
            setTimeout(() => {
                closeCorrectionModal();
                closePanel();
                loadState();
            }, 1200);
        } else {
            status.className = 'form-status error';
            status.textContent = `✗ ${data.error}`;
        }
    } catch (err) {
        status.className = 'form-status error';
        status.textContent = `✗ ${err.message}`;
    }
}

/* ── Drag and Drop ── */

let dragSourceKey = null;

function setupDragHandlers(card) {
    card.addEventListener('dragstart', (e) => {
        dragSourceKey = card.dataset.key;
        card.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
    });

    card.addEventListener('dragend', () => {
        card.classList.remove('dragging');
        document.querySelectorAll('.folder-card').forEach(c => c.classList.remove('drag-over'));
        dragSourceKey = null;
    });

    card.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (card.dataset.key !== dragSourceKey) {
            card.classList.add('drag-over');
        }
    });

    card.addEventListener('dragleave', () => {
        card.classList.remove('drag-over');
    });

    card.addEventListener('drop', (e) => {
        e.preventDefault();
        card.classList.remove('drag-over');
        const targetKey = card.dataset.key;

        if (dragSourceKey && targetKey && dragSourceKey !== targetKey) {
            swapFolderPositions(dragSourceKey, targetKey);
        }
    });
}

function swapFolderPositions(keyA, keyB) {
    const idxA = currentFolderOrder.indexOf(keyA);
    const idxB = currentFolderOrder.indexOf(keyB);
    if (idxA === -1 || idxB === -1) return;

    currentFolderOrder[idxA] = keyB;
    currentFolderOrder[idxB] = keyA;

    // Check if order matches the saved snapshot BEFORE re-rendering
    const changed = currentFolderOrder.some((key, i) => key !== savedFolderOrder[i]);
    layoutDirty = changed;

    // Re-render
    if (currentState) {
        renderFolderGrid(currentState.folders);
    }

    if (changed) {
        showSaveBar();
    } else {
        document.getElementById('saveTemplateBar').style.display = 'none';
    }
}

function showSaveBar() {
    const bar = document.getElementById('saveTemplateBar');
    const newBtn = document.getElementById('saveNewBtn');
    const overrideBtn = document.getElementById('saveOverrideBtn');
    const label = document.getElementById('saveTemplateLabel');

    bar.style.display = 'flex';

    if (activeTemplateName === 'alphabetical') {
        // No template saved yet
        label.textContent = 'Layout changed';
        newBtn.textContent = 'Complete Template Setup';
        newBtn.style.display = 'inline-block';
        overrideBtn.style.display = 'none';
    } else {
        // Has an active template
        label.textContent = 'Layout changed';
        newBtn.textContent = 'Save as new';
        newBtn.style.display = 'inline-block';
        overrideBtn.textContent = `Override "${activeTemplateName}"`;
        overrideBtn.style.display = 'inline-block';
    }
}

/* ── Layout Templates ── */

async function loadLayoutTemplates() {
    try {
        const data = await fetch('/api/layout/templates').then(r => r.json());
        activeTemplateName = data.active || 'alphabetical';

        // If active template has a folder order, apply it
        if (activeTemplateName !== 'alphabetical' && data.templates && data.templates[activeTemplateName]) {
            currentFolderOrder = data.templates[activeTemplateName].folder_order || [];
        }

        // Populate settings dropdown
        const select = document.getElementById('settingsTemplateSelect');
        if (select) {
            select.innerHTML = '<option value="alphabetical">Alphabetical (Default)</option>';
            for (const name of Object.keys(data.templates || {})) {
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name;
                if (name === activeTemplateName) opt.selected = true;
                select.appendChild(opt);
            }
        }
    } catch (err) {
        console.error('Failed to load templates:', err);
    }
}

async function applyTemplate() {
    const select = document.getElementById('settingsTemplateSelect');
    const name = select.value;

    try {
        const resp = await fetch('/api/layout/apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        const data = await resp.json();

        if (data.success) {
            activeTemplateName = data.active;
            currentFolderOrder = data.folder_order || [];
            layoutDirty = false;
            document.getElementById('saveTemplateBar').style.display = 'none';
            if (currentState) renderFolderGrid(currentState.folders);
            toggleSettingsPanel();
        }
    } catch (err) {
        console.error('Failed to apply template:', err);
    }
}

async function resetAlphabetical() {
    currentFolderOrder = [];
    savedFolderOrder = [];
    activeTemplateName = 'alphabetical';
    layoutDirty = false;
    document.getElementById('saveTemplateBar').style.display = 'none';

    try {
        await fetch('/api/layout/apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: 'alphabetical' }),
        });
    } catch (err) {
        console.error('Failed to reset:', err);
    }

    if (currentState) renderFolderGrid(currentState.folders);
    toggleSettingsPanel();
}

function saveTemplateAs() {
    // Replace the save bar with an inline name input
    const bar = document.getElementById('saveTemplateBar');
    bar.innerHTML = `
        <input type="text" id="templateNameInput" placeholder="Template name" 
               style="padding:4px 10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:12px;width:160px">
        <button class="btn btn-sm save-btn-new" onclick="confirmSaveNew()">Save</button>
        <button class="btn btn-sm" onclick="cancelSaveNew()">Cancel</button>
    `;
    document.getElementById('templateNameInput').focus();
}

function confirmSaveNew() {
    const input = document.getElementById('templateNameInput');
    const name = input ? input.value.trim() : '';
    if (!name) {
        input.style.borderColor = 'var(--red)';
        return;
    }
    saveTemplate(name, false);
}

function cancelSaveNew() {
    showSaveBar();
}

function saveTemplateOverride() {
    if (activeTemplateName === 'alphabetical') {
        saveTemplateAs();
        return;
    }
    saveTemplate(activeTemplateName, true);
}

async function saveTemplate(name, override) {
    try {
        const resp = await fetch('/api/layout/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name,
                folder_order: currentFolderOrder,
                override,
            }),
        });
        const data = await resp.json();

        if (data.success) {
            activeTemplateName = data.active;
            layoutDirty = false;
            savedFolderOrder = [...currentFolderOrder];
            document.getElementById('saveTemplateBar').style.display = 'none';
            loadLayoutTemplates();
        } else {
            alert(data.error || 'Failed to save');
        }
    } catch (err) {
        alert('Error: ' + err.message);
    }
}

// Load templates on startup
loadLayoutTemplates();

/* ── AI Pipeline Display ── */

let pipelineResults = {};

function setPipelineStatus(state, text) {
    const bar = document.getElementById('pipelineStatusBar');
    const dot = document.getElementById('pipelineServiceDot');
    if (!bar || !dot) return;
    bar.textContent = text;
    bar.className = `service-status-bar ${state}`;
    dot.className = `service-status-dot ${state === 'active' ? 'running' : state === 'working' ? 'working' : ''}`;
}

function clearPipelineEvents() {
    const container = document.getElementById('pipelineEvents');
    if (container) container.innerHTML = '';
    pipelineResults = {};
}

function addPipelineEvent(eventId, initialStage, subject) {
    const container = document.getElementById('pipelineEvents');
    if (!container) return;

    const empty = container.querySelector('.activity-empty');
    if (empty) empty.remove();

    const displayName = devMode ? eventId : (subject || eventId);

    const card = document.createElement('div');
    card.className = 'pipeline-event';
    card.id = `pipeline-${eventId}`;
    card.style.cursor = 'pointer';
    card.onclick = () => showPipelineDetail(eventId);
    card.innerHTML = `
        <div class="pipeline-event-title" data-event-id="${escapeAttr(eventId)}" data-subject="${escapeAttr(subject || '')}">${escapeHtml(displayName)}</div>
        <div class="pipeline-stages">
            <span class="pipeline-stage active" data-stage="matching">
                <span class="stage-spinner"></span>Skill Match
            </span>
            <span class="pipeline-arrow">→</span>
            <span class="pipeline-stage" data-stage="scroll">
                <span class="stage-spinner"></span>Scroll
            </span>
            <span class="pipeline-arrow">→</span>
            <span class="pipeline-stage" data-stage="classify">
                <span class="stage-spinner"></span>Classify
            </span>
            <span class="pipeline-arrow">→</span>
            <span class="pipeline-stage" data-stage="dispatch">
                <span class="stage-spinner"></span>Route
            </span>
        </div>
    `;
    container.insertBefore(card, container.firstChild);
}

function completePipelineEvent(eventId, outcome, resultData) {
    // Store full result for click-to-view
    pipelineResults[eventId] = resultData;

    const card = document.getElementById(`pipeline-${eventId}`);
    if (!card) return;

    const skillMatched = resultData.skill_matched;
    const stages = card.querySelectorAll('.pipeline-stage');
    stages.forEach(s => {
        s.classList.remove('active');
        const stage = s.dataset.stage;

        if (stage === 'matching') {
            s.classList.add(skillMatched ? 'done' : 'skipped');
            if (skillMatched) s.textContent = `✓ ${skillMatched}`;
        } else if (stage === 'scroll') {
            s.classList.add(skillMatched ? 'done' : 'skipped');
            if (skillMatched) s.textContent = `✓ ${skillMatched}_scroll`;
        } else {
            s.classList.add('done');
        }
    });

    const title = card.querySelector('.pipeline-event-title');
    if (title) {
        const subject = title.dataset.subject;
        const displayName = devMode ? eventId : (subject || eventId);
        title.innerHTML = `${escapeHtml(displayName)} <span class="pipeline-arrow">→</span> <span style="color:var(--green)">${escapeHtml(outcome)}</span>`;
    }
}

function updatePipelineStage(eventId, stageName, state, label) {
    const card = document.getElementById(`pipeline-${eventId}`);
    if (!card) return;
    const stage = card.querySelector(`.pipeline-stage[data-stage="${stageName}"]`);
    if (!stage) return;

    stage.classList.remove('active', 'done', 'skipped');
    stage.classList.add(state);

    if (label) {
        stage.innerHTML = label;
    }
}

function showPipelineDetail(eventId) {
    const data = pipelineResults[eventId];
    if (!data) return;

    const modal = document.getElementById('modalOverlay');
    const body = document.getElementById('modalBody');
    const title = document.getElementById('modalTitle');

    if (devMode) {
        title.textContent = `Pipeline Result — ${eventId}`;
        body.innerHTML = '';
        body.textContent = JSON.stringify(data, null, 2);
        body.style.whiteSpace = 'pre-wrap';
    } else {
        const pipelineSubject = resolveDisplayTitle(data);
        title.textContent = `Pipeline Summary — ${pipelineSubject}`;

        const skill = data.skill_matched;
        const reqType = data.skill_request_type;
        const skillOutcome = data.skill_outcome;
        const skillAnalysis = data.skill_analysis;
        const classification = data.outcome;
        const confidence = data.confidence ? Math.round(data.confidence * 100) : 0;
        const confClass = confidence >= 80 ? 'high' : confidence >= 60 ? 'medium' : 'low';

        let html = '<div class="event-detail">';

        // Step 1: Skill Match
        html += '<div class="pipeline-detail-step">';
        html += '<div class="pipeline-detail-step-num">1</div>';
        html += '<div class="pipeline-detail-step-content">';
        html += '<div class="pipeline-detail-step-title">Skill Matching</div>';
        if (skill) {
            html += `<div class="pipeline-detail-step-result success">Matched: <strong>${escapeHtml(skill)}</strong></div>`;
        } else {
            html += '<div class="pipeline-detail-step-result dimmed">No skill matched — classification only</div>';
        }
        html += '</div></div>';

        // Step 2: Scroll Execution
        if (skill) {
            html += '<div class="pipeline-detail-step">';
            html += '<div class="pipeline-detail-step-num">2</div>';
            html += '<div class="pipeline-detail-step-content">';
            html += `<div class="pipeline-detail-step-title">${escapeHtml(skill)}_scroll executed</div>`;
            if (reqType) html += `<div>Request Type: <strong>${escapeHtml(reqType)}</strong></div>`;
            if (skillOutcome) html += `<div>Outcome: <strong>${escapeHtml(skillOutcome)}</strong></div>`;
            if (skillAnalysis) html += `<div class="event-detail-reasoning">"${escapeHtml(skillAnalysis)}"</div>`;
            html += '</div></div>';
        }

        // Step 3: Classification
        html += '<div class="pipeline-detail-step">';
        html += `<div class="pipeline-detail-step-num">${skill ? '3' : '2'}</div>`;
        html += '<div class="pipeline-detail-step-content">';
        html += '<div class="pipeline-detail-step-title">Classification</div>';
        html += `<div>Destination: <strong>${escapeHtml(classification)}</strong></div>`;
        html += `<div>Confidence: <span class="event-detail-confidence"><span class="confidence-bar"><span class="confidence-fill ${confClass}" style="width:${confidence}%"></span></span> ${confidence}%</span></div>`;
        if (data.reasoning) html += `<div class="event-detail-reasoning">"${escapeHtml(data.reasoning)}"</div>`;
        html += '</div></div>';

        html += '</div>';
        body.innerHTML = html;
        body.style.whiteSpace = 'normal';
    }

    modal.classList.add('open');
}
