'use strict';
const recommendationZh = JSON.parse(document.getElementById('recommendationBootstrap').textContent).zh;
async function recommendationFeedback(payload, control) {
    if (control.disabled) return;
    if (payload.action === 'clear_history' && !confirm(recommendationZh ? '清除已记录的浏览与搜索？简历和收藏会保留。' : 'Clear recorded browsing and searches? Resumes and bookmarks remain.')) return;
    control.disabled = true;
    try {
        const response = await fetch('/api/recommendations/feedback', {method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content}, body: JSON.stringify(payload)});
        const data = await response.json();
        if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : (recommendationZh ? '操作失败，请刷新后重试。' : 'Could not update. Refresh and retry.'));
        location.reload();
    } catch (error) {
        if (payload.action === 'set_learning') control.checked = !payload.enabled;
        document.getElementById('recommendationStatus').textContent = error.message;
        control.disabled = false;
    }
}
document.querySelectorAll('[data-recommendation-action]').forEach(button => button.addEventListener('click', () => {
    const payload = {action: button.dataset.recommendationAction};
    if (button.dataset.opportunity) payload.opportunity_id = button.dataset.opportunity;
    recommendationFeedback(payload, button);
}));
document.getElementById('recommendationLearning').addEventListener('change', event => {
    recommendationFeedback({action: 'set_learning', enabled: event.target.checked}, event.target);
});
