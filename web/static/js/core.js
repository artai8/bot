(function () {
    const state = {
        token: localStorage.getItem('fsb_token') || '',
        platformToken: localStorage.getItem('fsb_platform_token') || '',
        session: null,
        currentPage: ''
    };

    function esc(value) {
        const replacements = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
        return (value == null ? '' : String(value)).replace(/[&<>"']/g, character => replacements[character]);
    }

    async function api(url, options = {}) {
        const response = await fetch(url, {
            ...options,
            headers: {
                Authorization: 'Bearer ' + state.token,
                'Content-Type': 'application/json',
                ...(options.headers || {})
            }
        });
        let data = {};
        try { data = await response.json(); } catch (_) { data = { error: 'Invalid server response' }; }
        if (response.status === 401) {
            window.FSB.logout(false);
            throw new Error('登录已过期');
        }
        if (!response.ok) throw new Error(data.error || '请求失败');
        return data;
    }

    function toast(message, type = 'info') {
        const element = document.createElement('div');
        element.className = `toast toast-${type}`;
        element.textContent = message;
        document.getElementById('toast-container').appendChild(element);
        setTimeout(() => element.remove(), 3500);
    }

    function modal(title, body, actions = '') {
        document.getElementById('modal-title').textContent = title;
        document.getElementById('modal-body').innerHTML = body;
        document.getElementById('modal-actions').innerHTML = actions;
        document.getElementById('modal-overlay').classList.remove('hidden');
    }

    function closeModal() { document.getElementById('modal-overlay').classList.add('hidden'); }

    function pagination(page, pages, callback) {
        if (pages <= 1) return '';
        return `<div class="pagination">
            <button class="btn btn-ghost btn-sm" ${page <= 1 ? 'disabled' : ''} onclick="${callback}(${page - 1})">上一页</button>
            <span>${page} / ${pages}</span>
            <button class="btn btn-ghost btn-sm" ${page >= pages ? 'disabled' : ''} onclick="${callback}(${page + 1})">下一页</button>
        </div>`;
    }

    async function pollJob(jobId, onProgress, prefix = '/api/jobs/') {
        for (;;) {
            const job = await api(prefix + encodeURIComponent(jobId));
            if (onProgress) onProgress(job);
            if (['completed', 'failed', 'cancelled'].includes(job.state)) return job;
            await new Promise(resolve => setTimeout(resolve, 1500));
        }
    }

    window.FSB = { state, esc, api, toast, modal, closeModal, pagination, pollJob };
})();
