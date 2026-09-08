'use strict';
const resumeBoot = JSON.parse(document.getElementById('resumeBootstrap').textContent);
const el = id => document.getElementById(id);
const tr = (zh, en) => resumeBoot.zh ? zh : en;
let savedResume = null, filename = '', busyResume = false, dirtyResume = false;
function statusResume(text, error = false) { el('resumeStatus').textContent = text; el('resumeStatus').className = `text-sm whitespace-pre-wrap ${error ? 'text-red-700' : 'text-green-700'}`; }
async function resumeApi(path, method = 'GET', body) {
    const response = await fetch(path, {method, headers: {'X-CSRF-Token': resumeBoot.csrf, 'Content-Type': body instanceof Blob ? 'application/octet-stream' : 'application/json'}, ...(body === undefined ? {} : {body: body instanceof Blob ? body : JSON.stringify(body)})});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : tr('请检查输入内容；登录过期时请先复制编辑内容再登录。', 'Check your inputs. If your session expired, copy your edits before signing in.'));
    return data;
}
async function resumeAction(fn) {
    if (busyResume) return;
    busyResume = true;
    for (const node of document.querySelectorAll('#resumeForm input, #resumeForm textarea, #resumeForm button, #resumeFile')) node.disabled = true;
    try { await fn(); } catch (error) { statusResume(error.message, true); }
    finally { busyResume = false; for (const node of document.querySelectorAll('#resumeForm input, #resumeForm textarea, #resumeForm button, #resumeFile')) node.disabled = false; el('deleteResume').disabled = !savedResume; }
}
async function loadResume() {
    savedResume = (await resumeApi('/api/resume')).resume;
    filename = savedResume?.filename || '';
    el('resumeFilename').textContent = filename;
    el('resumeText').value = savedResume?.text || '';
    el('resumeKeywords').value = (savedResume?.keywords || []).join(', ');
    el('resumeEnabled').checked = savedResume ? savedResume.enabled : true;
    dirtyResume = false;
    statusResume(savedResume ? tr('已载入保存的简历信息。', 'Saved resume data loaded.') : tr('尚未保存简历。上传文件或粘贴文字后核对并确认。', 'No saved resume. Upload a file or paste text, then review and confirm.'));
}
el('resumeForm').addEventListener('input', () => { dirtyResume = true; });
el('resumeFile').addEventListener('change', () => {
    const file = el('resumeFile').files[0]; if (!file) return;
    resumeAction(async () => {
        try {
            if (dirtyResume && !confirm(tr('解析新文件将替换当前未保存的编辑，是否继续？', 'Replace your unsaved edits with the new file?'))) return;
            if (file.size > 2 * 1024 * 1024) throw Error(tr('文件最大 2 MiB。', 'File limit: 2 MiB.'));
            statusResume(tr('正在本地解析…', 'Parsing locally…'));
            const data = await resumeApi('/api/resume/parse?filename=' + encodeURIComponent(file.name), 'POST', file);
            filename = data.filename; el('resumeFilename').textContent = filename;
            el('resumeText').value = data.text; el('resumeKeywords').value = data.keywords.join(', ');
            dirtyResume = true;
            statusResume(tr('解析完成，请核对文字与匹配词；确认保存后才用于匹配。', 'Parsed. Review the text and keywords; matching changes only after confirmation.'));
        } finally { el('resumeFile').value = ''; }
    });
});
el('resumeForm').addEventListener('submit', event => {
    event.preventDefault();
    resumeAction(async () => {
        const data = await resumeApi('/api/resume', 'PUT', {filename: filename || tr('粘贴的简历', 'Pasted resume'), text: el('resumeText').value,
            keywords: el('resumeKeywords').value.split(/[,，\n]/).map(word => word.trim()).filter(Boolean), enabled: el('resumeEnabled').checked, updated_at: savedResume?.updated_at || null});
        savedResume = data.resume; dirtyResume = false;
        statusResume(savedResume.enabled ? tr('已保存并启用，可以查看匹配机会。', 'Saved and enabled. You can now view matching opportunities.') : tr('已保存并关闭匹配。', 'Saved with matching disabled.'));
    });
});
el('reloadResume').addEventListener('click', () => { if (!dirtyResume || confirm(tr('放弃未保存的编辑并重新载入？', 'Discard unsaved edits and reload?'))) resumeAction(loadResume); });
el('deleteResume').addEventListener('click', () => resumeAction(async () => {
    if (!confirm(tr('删除已保存的简历文字和匹配词？此操作无法撤销。', 'Delete saved resume text and keywords? This cannot be undone.'))) return;
    await resumeApi('/api/resume', 'DELETE'); await loadResume();
    statusResume(tr('简历信息已删除，已停止用于匹配。', 'Resume data deleted and no longer used for matching.'));
}));
window.addEventListener('beforeunload', event => { if (dirtyResume) { event.preventDefault(); event.returnValue = ''; } });
resumeAction(loadResume);
