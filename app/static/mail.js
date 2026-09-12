'use strict';
const boot = JSON.parse(document.getElementById('mailBootstrap').textContent);
const $ = id => document.getElementById(id);
const t = (zh, en) => boot.zh ? zh : en;
let draft = null, opportunity = boot.opportunity, accounts = [], dirty = false, busy = false, mailConfigured = false;
const editable = () => !draft || ['draft', 'failed'].includes(draft.status);
const statusNames = {draft: t('草稿', 'Draft'), sending: t('发送中', 'Sending'), sent: t('已提交发送', 'Sent to provider'), failed: t('发送失败', 'Failed'), unknown: t('发送结果待核实', 'Delivery uncertain')};
function message(text, error = false) {
    $('mailStatus').textContent = text;
    $('mailStatus').className = `mb-4 rounded-lg p-3 text-sm ${error ? 'bg-amber-50 text-amber-900' : 'bg-green-50 text-green-800'}`;
}
async function api(path, method = 'GET', body, headers = {}) {
    const response = await fetch(path, {method, headers: {'Content-Type': body instanceof Blob ? 'application/octet-stream' : 'application/json', 'X-CSRF-Token': boot.csrf, ...headers}, ...(body === undefined ? {} : {body: body instanceof Blob ? body : JSON.stringify(body)})});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        if (response.status === 401) throw Error(t('登录已过期；请保留正文并重新登录。', 'Your session expired. Copy your text before signing in again.'));
        throw Error(typeof data.detail === 'string' ? data.detail : t('请检查填写内容后重试。', 'Check the fields and retry.'));
    }
    return data;
}
function controls() {
    $('saveDraft').disabled = busy || !editable();
    $('generateDraft').disabled = busy || !editable() || !opportunity;
    $('reviewSend').disabled = busy || !editable() || !$('senderAccount').value;
    $('deleteDraft').disabled = busy || !draft || !editable();
    $('newDraft').disabled = busy;
    $('confirmSend').disabled = busy;
    $('connectAccount').disabled = busy || !mailConfigured || accounts.length >= 5;
    $('attachmentFiles').disabled = busy || !editable();
    renderAttachments();
    $('draftState').textContent = dirty ? t('有未保存的更改', 'Unsaved changes') : draft ? statusNames[draft.status] || draft.status : t('新草稿', 'New draft');
    for (const id of ['recipient', 'messageSubject', 'messageBody', 'senderAccount', 'draftLanguage']) $(id).disabled = busy || !editable();
}
function renderAttachments() {
    const items = draft?.attachments || [];
    $('attachmentList').replaceChildren();
    if (!items.length) $('attachmentList').textContent = t('尚未添加附件', 'No attachments');
    for (const item of items) {
        const row = document.createElement('li'); row.className = 'flex items-center justify-between gap-3 text-sm';
        const link = document.createElement('a'); link.className = 'text-indigo-700 underline break-all';
        link.href = `/api/mail/drafts/${draft.id}/attachments/${item.id}`;
        link.textContent = `${item.filename} (${(item.size / 1024).toFixed(1)} KiB)`;
        row.append(link);
        if (editable()) {
            const remove = document.createElement('button'); remove.type = 'button'; remove.disabled = busy;
            remove.className = 'text-red-700 shrink-0 disabled:opacity-50'; remove.textContent = t('移除', 'Remove');
            remove.setAttribute('aria-label', t('移除附件 ', 'Remove attachment ') + item.filename);
            remove.addEventListener('click', () => action(async () => {
                if (dirty) await persist();
                draft = (await api(`/api/mail/drafts/${draft.id}/attachments/${item.id}`, 'DELETE', undefined, {'X-Draft-Version': draft.updated_at})).draft;
                await refreshDrafts(); message(t('附件已移除。', 'Attachment removed.'));
            }));
            row.append(remove);
        }
        $('attachmentList').append(row);
    }
}
async function action(fn) {
    if (busy) return;
    busy = true; controls();
    try { await fn(); } catch (error) { message(error.message, true); }
    finally { busy = false; controls(); }
}
function hasUnsavedChanges() {
    return dirty && !window.confirm(t('尚有未保存的更改，仍要离开当前草稿吗？', 'Leave this draft and discard unsaved changes?'));
}
function setContext(item) {
    opportunity = item;
    $('opportunityContext').classList.toggle('hidden', !item);
    $('opportunityLink').textContent = item?.title || '';
    $('opportunityLink').href = item ? (item.is_external ? `/applications#application-${encodeURIComponent(item.id)}` : `/opportunity/${encodeURIComponent(item.id)}`) : '#';
    $('applyReminder').classList.toggle('hidden', !item?.has_direct_apply);
    $('generationNote').textContent = item ? '' : t('从项目详情进入可使用模板，也可直接在下方写信。', 'Open a project to use a template, or write directly below.');
    controls();
}
function draftPayload() {
    return {recipient: $('recipient').value.trim(), subject: $('messageSubject').value, body: $('messageBody').value,
        account_id: Number($('senderAccount').value) || null, opportunity_id: opportunity?.id || draft?.opportunity_id || '',
        language: $('draftLanguage').value, updated_at: draft?.updated_at || null};
}
async function persist() {
    const data = await api(draft ? `/api/mail/drafts/${draft.id}` : '/api/mail/drafts', draft ? 'PUT' : 'POST', draftPayload());
    draft = data.draft; dirty = false;
    history.replaceState(null, '', `/mail?draft=${draft.id}`);
    await refreshDrafts(); controls(); return draft;
}
async function refreshDrafts() {
    const data = await api('/api/mail/drafts');
    $('draftList').replaceChildren();
    if (!data.drafts.length) $('draftList').textContent = t('还没有保存的草稿。', 'No saved drafts yet.');
    for (const row of data.drafts) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'block w-full text-left border rounded-lg p-3 hover:border-indigo-400';
        const title = document.createElement('div'); title.className = 'text-sm font-medium truncate'; title.textContent = row.subject || t('未命名草稿', 'Untitled draft');
        const status = document.createElement('div'); status.className = 'text-sm text-gray-500 mt-1'; status.textContent = statusNames[row.status] || row.status;
        button.append(title, status); button.addEventListener('click', () => { if (!hasUnsavedChanges()) action(() => openDraft(row.id)); });
        $('draftList').append(button);
    }
}
async function openDraft(id) {
    const data = await api(`/api/mail/drafts/${id}`); draft = data.draft;
    for (const [field, key] of [['recipient', 'recipient'], ['messageSubject', 'subject'], ['messageBody', 'body'], ['draftLanguage', 'language']]) $(field).value = draft[key];
    $('senderAccount').value = draft.account_id || '';
    dirty = false;
    let item = null;
    if (draft.opportunity_id) {
        try { item = (await api(`/api/mail/context/${encodeURIComponent(draft.opportunity_id)}`)).opportunity; } catch { /* Saved text remains usable if the posting was removed. */ }
    }
    setContext(item);
    history.replaceState(null, '', `/mail?draft=${id}`);
    if (draft.last_error) message(draft.last_error, true);
    controls();
}
async function refreshAccounts() {
    const chosen = $('senderAccount').value;
    const providerChoice = $('accountProvider').value;
    const data = await api('/api/mail/accounts'); accounts = data.accounts; mailConfigured = data.configured;
    $('senderAccount').replaceChildren(new Option(t('仅保存草稿，暂不连接邮箱', 'Save only; no mailbox selected'), ''));
    $('accountList').replaceChildren();
    for (const account of accounts) {
        $('senderAccount').add(new Option(`${account.label} · ${account.email}`, account.id));
        const row = document.createElement('div'); row.className = 'flex flex-wrap justify-between items-center gap-2 border-b pb-2';
        const label = document.createElement('span'); label.className = 'text-sm break-all'; label.textContent = `${account.label} · ${account.email}`;
        const button = document.createElement('button'); button.type = 'button'; button.className = 'text-red-700 text-sm'; button.textContent = t('断开', 'Disconnect');
        button.addEventListener('click', () => action(async () => {
            if (!window.confirm(t(`断开 ${account.email}？已保存草稿会保留。`, `Disconnect ${account.email}? Saved drafts remain.`))) return;
            await api(`/api/mail/accounts/${account.id}`, 'DELETE');
            let conflict = false;
            if (draft?.account_id === account.id) {
                const latest = (await api(`/api/mail/drafts/${draft.id}`)).draft;
                // Disconnect changes the saved version; preserve local edits without adopting another tab's content.
                if (['recipient', 'subject', 'body', 'opportunity_id', 'language', 'status'].every(key => latest[key] === draft[key]) && JSON.stringify(latest.attachments) === JSON.stringify(draft.attachments)) draft = latest;
                else conflict = true;
            }
            await refreshAccounts();
            message(conflict ? t('邮箱已断开，但草稿也在其他页面修改过。请先复制当前正文，再重新打开草稿。', 'Mailbox disconnected, but another page changed this draft. Copy your text, then reopen the draft.') : t('邮箱已断开，当前编辑内容已保留。', 'Mailbox disconnected. Your current edits are preserved.'), conflict);
        }));
        row.append(label, button); $('accountList').append(row);
    }
    if (!accounts.length) $('accountList').textContent = t('暂未连接邮箱。你仍然可以保存和复制草稿。', 'No mailbox connected. You can still save and copy drafts.');
    $('senderAccount').value = chosen;
    $('accountProvider').replaceChildren(...data.providers.map(item => new Option(item.label, item.id)));
    if (data.providers.some(item => item.id === providerChoice)) $('accountProvider').value = providerChoice;
    $('customSmtp').classList.toggle('hidden', $('accountProvider').value !== 'custom');
    $('accountReason').textContent = data.configured ? '' : t('网站尚未完成邮箱连接配置，管理员启用后即可连接。草稿功能不受影响。', 'Mailbox connection needs administrator setup. Drafting remains available.');
    $('oauthProviders').replaceChildren();
    let oauth;
    try { oauth = await api('/api/mail/oauth/providers'); }
    catch { oauth = {providers: []}; $('oauthProviders').textContent = t('Google / Microsoft 授权暂不可用，仍可编辑草稿或使用其他邮箱连接。', 'Google / Microsoft authorization is unavailable. Drafts and other mailbox connections remain usable.'); }
    for (const provider of oauth.providers) {
        const box = document.createElement('div');
        const button = document.createElement('button'); button.type = 'button'; button.textContent = provider.label; button.disabled = !provider.available || !data.configured;
        button.className = 'px-4 py-2 border rounded-md text-indigo-700 disabled:opacity-40';
        button.addEventListener('click', () => action(async () => {
            if (dirty) await persist();
            const result = await api(`/api/mail/oauth/${provider.id}/start`, 'POST', {});
            const target = new URL(result.authorization_url);
            if (target.protocol !== 'https:' || !['accounts.google.com', 'login.microsoftonline.com'].includes(target.hostname)) throw Error(t('授权地址无效。', 'Invalid authorization destination.'));
            location.assign(target.href);
        }));
        box.append(button);
        if (!provider.available) { const note = document.createElement('p'); note.className = 'text-sm text-gray-500 mt-1'; note.textContent = t('等待网站配置授权应用', 'Authorization setup required'); box.append(note); }
        $('oauthProviders').append(box);
    }
    controls();
}
$('messageForm').addEventListener('submit', event => { event.preventDefault(); action(async () => { await persist(); message(t('草稿已保存，仅你可见。', 'Private draft saved.')); }); });
for (const id of ['messageForm', 'draftLanguage']) $(id).addEventListener('input', () => { dirty = true; controls(); });
$('senderAccount').addEventListener('change', controls);
$('newDraft').addEventListener('click', () => {
    if (busy || hasUnsavedChanges()) return;
    draft = null; dirty = false; for (const id of ['recipient', 'messageSubject', 'messageBody']) $(id).value = '';
    $('senderAccount').value = ''; setContext(null); history.replaceState(null, '', '/mail'); controls();
});
$('generateDraft').addEventListener('click', () => action(async () => {
    if ($('messageBody').value.trim() && !window.confirm(t('生成新内容将替换当前正文，是否继续？', 'Replace the current text with a new draft?'))) return;
    const data = await api('/api/mail/generate', 'POST', {opportunity_id: opportunity.id, student_name: $('studentName').value, school: $('studentSchool').value,
        interest: $('studentInterest').value, focus_area: $('draftFocus').value, resume_text: $('draftResume').value,
        language: $('draftLanguage').value, tone: $('draftTone').value, use_ai: document.querySelector('[name=draftMode]:checked').value === 'ai'});
    $('messageSubject').value = data.draft.subject; $('messageBody').value = data.draft.body;
    if (!$('recipient').value) $('recipient').value = data.recipient || '';
    dirty = true;
    message(data.draft.warning || (data.draft.mode === 'ai' ? t('AI 草稿已生成，请核对后保存。', 'AI draft generated. Review before saving.') : t('免费模板已生成，可以继续编辑。', 'Free template generated. You can edit it now.')), !!data.draft.warning);
}));
$('reviewSend').addEventListener('click', () => action(async () => {
    if (!$('recipient').value || !$('recipient').checkValidity() || !$('messageSubject').value.trim() || !$('messageBody').value.trim()) throw Error(t('请先填写有效收件人、主题和正文。', 'Enter a valid recipient, subject and message.'));
    const saved = await persist();
    $('reviewFrom').textContent = accounts.find(account => account.id === saved.account_id)?.email || '';
    $('reviewTo').textContent = saved.recipient; $('reviewSubject').textContent = saved.subject; $('reviewBody').textContent = saved.body;
    $('reviewAttachments').textContent = (saved.attachments || []).length ? t('附件：\n', 'Attachments:\n') + saved.attachments.map(item => `${item.filename} (${(item.size / 1024).toFixed(1)} KiB)`).join('\n') : t('无附件', 'No attachments');
    $('sendReview').showModal();
}));
$('cancelSend').addEventListener('click', () => $('sendReview').close());
$('confirmSend').addEventListener('click', () => action(async () => {
    $('sendReview').close();
    try {
        const data = await api(`/api/mail/drafts/${draft.id}/send`, 'POST', {confirm: true, updated_at: draft.updated_at});
        draft = data.draft; dirty = false;
        message(draft.status === 'sent' ? t('邮件已交给邮箱服务发送。', 'Email accepted by your mail provider.') : draft.last_error || t('发送结果待核实，请勿重复发送。', 'Delivery is uncertain. Do not resend.'), draft.status !== 'sent');
    } catch (error) {
        // A lost HTTP response is not evidence that the email was not sent.
        try { await openDraft(draft.id); } catch { draft.status = 'unknown'; }
        message(error.message + ' ' + t('请先查看发送状态，不要重复点击发送。', 'Check the status before attempting another send.'), true);
    }
    await refreshDrafts();
}));
$('copyMessage').addEventListener('click', () => action(async () => { await navigator.clipboard.writeText(`Subject: ${$('messageSubject').value}\n\n${$('messageBody').value}`); message(t('已复制。', 'Copied.')); }));
$('deleteDraft').addEventListener('click', () => action(async () => {
    if (!draft || !window.confirm(t('删除这份草稿？', 'Delete this draft?'))) return;
    await api(`/api/mail/drafts/${draft.id}`, 'DELETE'); draft = null; dirty = false;
    for (const id of ['recipient', 'messageSubject', 'messageBody', 'senderAccount']) $(id).value = '';
    setContext(null); await refreshDrafts(); history.replaceState(null, '', '/mail'); message(t('草稿已删除。', 'Draft deleted.'));
}));
$('accountProvider').addEventListener('change', () => $('customSmtp').classList.toggle('hidden', $('accountProvider').value !== 'custom'));
$('connectAccountForm').addEventListener('submit', event => {
    event.preventDefault(); action(async () => {
        const payload = {provider: $('accountProvider').value, email: $('accountEmail').value, label: $('accountLabel').value, password: $('accountPassword').value};
        if (payload.provider === 'custom') Object.assign(payload, {smtp_host: $('smtpHost').value, smtp_security: $('smtpSecurity').value, smtp_port: $('smtpSecurity').value === 'ssl' ? 465 : 587, smtp_username: $('smtpUsername').value});
        $('connectAccount').disabled = true;
        try { await api('/api/mail/accounts', 'POST', payload); $('accountPassword').value = ''; await refreshAccounts(); message(t('邮箱已连接，没有发送测试邮件。', 'Mailbox connected. No test email was sent.')); }
        finally { controls(); }
    });
});
window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
$('attachmentFiles').addEventListener('change', () => {
    const files = Array.from($('attachmentFiles').files); if (!files.length) return;
    action(async () => {
        try {
            const existing = draft?.attachments || [];
            if (existing.length + files.length > 5 || existing.reduce((sum, item) => sum + item.size, 0) + files.reduce((sum, item) => sum + item.size, 0) > 2 * 1024 * 1024) throw Error(t('每封邮件最多 5 个附件，合计 2 MiB。', 'Up to 5 attachments and 2 MiB total per email.'));
            await persist();
            for (const file of files) {
                draft = (await api(`/api/mail/drafts/${draft.id}/attachments?filename=${encodeURIComponent(file.name)}`, 'POST', file, {'X-Draft-Version': draft.updated_at})).draft;
            }
            message(t('附件已保存到草稿。', 'Attachments saved to the draft.'));
        } catch (error) {
            if (draft && !dirty) { try { await openDraft(draft.id); } catch { /* Keep the last known version; server rejects stale mutations. */ } }
            message(error.message + ' ' + t('请核对下方已保存的附件，未成功的文件可重新选择。', 'Check the saved files below and select any missing files again.'), true);
        }
        finally { $('attachmentFiles').value = ''; await refreshDrafts(); }
    });
});
(async () => {
    setContext(opportunity);
    await action(async () => { await refreshAccounts(); await refreshDrafts(); if (boot.draftId) await openDraft(boot.draftId); });
    const query = new URLSearchParams(location.search);
    if (query.has('connected')) message(t('邮箱授权连接成功。', 'Mailbox authorization completed.'));
    if (query.has('oauth_error')) message(t('邮箱授权未完成。请检查网站配置或重新授权。', 'Mailbox authorization was not completed. Check configuration or try again.'), true);
})();
