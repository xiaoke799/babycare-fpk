/**
 * 育儿宝 (BabyCare) - 飞牛 fnOS 原生育儿软件前端
 */

// ==================== 应用状态 ====================
const App = {
    currentBaby: null,
    babies: [],
    currentPage: 'dashboard',
    currentCareTab: 'feeding-list',
    currentKnowledgeCategory: 'all',
    apiBase: (window.GATEWAY_PREFIX || '')  // 网关前缀（fnOS 平台转发时自动检测）
};

// ==================== 工具函数 ====================

/**
 * HTML转义函数 - 防止XSS攻击
 * 将特殊字符转换为HTML实体
 */
function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// API 请求配置
const _apiConfig = {
    timeout: 30000,           // 默认超时 30 秒
    retryOn503: true,         // 503（初始化中）自动重试
    maxRetries503: 5,         // 最多重试 5 次
    retryOn429: true,         // 429（限流）按 Retry-After 等待
};

/**
 * 安全解析 JSON：当响应是 HTML（如 404/500）时不抛错
 * 用于 .then(r => r.json()) 的替代
 */
async function safeJson(res) {
    const contentType = res.headers?.get?.('content-type') || '';
    if (contentType.includes('application/json')) {
        try { return await res.json(); } catch (e) { return { success: false, message: 'JSON parse error' }; }
    }
    // 非 JSON 响应（HTML 404/500 等）
    const text = await res.text().catch(() => '');
    return { success: false, message: `HTTP ${res.status}: ${text.substring(0, 100)}` };
}

function api(url, options = {}) {
    // 拼接网关前缀（App.apiBase 由 window.GATEWAY_PREFIX 驱动）
    const fullUrl = /^https?:\/\//.test(url) ? url : (App.apiBase + url);
    const defaultOptions = {
        headers: { 'Content-Type': 'application/json' }
    };
    const mergedOpts = { ...defaultOptions, ...options };

    // 包装超时逻辑
    const fetchWithTimeout = (fetchUrl, fetchOpts, timeoutMs) => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        return fetch(fetchUrl, { ...fetchOpts, signal: controller.signal })
            .finally(() => clearTimeout(timer));
    };

    const doFetch = (retryCount = 0) =>
        fetchWithTimeout(fullUrl, mergedOpts, _apiConfig.timeout)
            .then(async res => {
                let data = null;
                const contentType = res.headers?.get?.('content-type') || '';
                // 仅当响应是 JSON 时才解析，避免 404 返回 HTML 时 parse 报错
                if (contentType.includes('application/json')) {
                    try { data = await res.json(); } catch (e) { data = null; }
                }

                // 503 初始化中：自动重试
                if (res.status === 503 && _apiConfig.retryOn503 && retryCount < _apiConfig.maxRetries503) {
                    const retryAfter = parseInt(res.headers?.get?.('Retry-After') || '3', 10);
                    return new Promise(resolve => {
                        setTimeout(() => resolve(doFetch(retryCount + 1)), retryAfter * 1000);
                    });
                }

                // 429 限流：按 Retry-After 等待后重试一次
                if (res.status === 429 && _apiConfig.retryOn429 && retryCount < 1) {
                    const retryAfter = parseInt(res.headers?.get?.('Retry-After') || '30', 10);
                    showToast(`操作频繁，${retryAfter}秒后重试...`);
                    return new Promise(resolve => {
                        setTimeout(() => resolve(doFetch(retryCount + 1)), retryAfter * 1000);
                    });
                }

                // 未认证：弹出登录遮罩，并记录触发请求以便登录后重放
                if (res.status === 401 && data && data.needAuth) {
                    _authPendingRequest = { url, options };
                    const st = await fetch(App.apiBase + '/api/auth/status').then(r => r.json()).catch(() => null);
                    _authPasswordless = !!(st && st.passwordless);
                    showAuthOverlay();
                    return { success: false, message: '请先登录', needAuth: true };
                }
                if (res.status === 401) {
                    return { success: false, message: (data && data.message) || '未授权' };
                }
                if (!res.ok) {
                    // 非 JSON 响应（如 404 HTML）时 data 为 null，使用通用错误信息
                    const msg = (data && data.message) || (contentType.includes('application/json') ? '请求失败' : '服务暂时不可用');
                    return { success: false, message: msg + '(' + res.status + ')' };
                }
                return data;
            })
            .catch(err => {
                if (err.name === 'AbortError') {
                    console.error('API Timeout:', fullUrl);
                    showToast('请求超时，请检查网络后重试');
                    return { success: false, message: '请求超时' };
                }
                console.error('API Error:', err);
                showToast.error('网络错误，请稍后重试');
                return { success: false, message: '网络错误' };
            });

    return doFetch(0);
}

// ==================== 登录认证 ====================
let _authPendingRequest = null;   // 触发登录的那次请求（登录成功后重放）
let _authOverlayForceShow = false; // 标志：强制显示遮罩（登录前不允许关闭）
let _authPasswordless = false;    // 后端免密模式：无需用户操作，自动进应用

function showAuthOverlay() {
    const overlay = document.getElementById('authOverlay');
    if (!overlay) return;

    // 免密模式：后端不设密码，/login 直接放行，不需要用户点任何东西
    if (_authPasswordless) {
        console.log('[Auth] 免密模式，自动登录');
        autoLogin();
        return;
    }

    // 免密改造后不再有「创建管理密码」这个分支，这里统一按登录态渲染。
    // 元素都做判空：万一 DOM 结构变动也不至于整页脚本崩掉。
    _authOverlayForceShow = true;
    const loginFields = document.getElementById('authLoginFields');
    if (loginFields) loginFields.style.display = 'block';
    const titleEl = document.getElementById('authTitle');
    if (titleEl) titleEl.textContent = ' 登录育儿宝';
    const errEl = document.getElementById('authError');
    if (errEl) errEl.textContent = '';
    overlay.classList.add('show');
    // 添加 auth-pending class 隐藏主内容
    document.body.classList.add('auth-pending');
    console.log('[Auth] showAuthOverlay: login, forceShow=', _authOverlayForceShow);
}

// 免密登录：直接调 /login（后端开发模式下直接放行），成功后关遮罩并重放挂起的请求
async function autoLogin() {
    try {
        const res = await fetch(window.GATEWAY_PREFIX + '/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ remember: true })
        });
        const data = await res.json().catch(() => null);
        if (!data || !data.success) {
            console.error('[Auth] 免密登录失败:', data);
            return;
        }
        console.log('[Auth] 免密登录成功');
        hideAuthOverlayAfterLogin();
        if (_authPendingRequest) {
            const pending = _authPendingRequest;
            _authPendingRequest = null;
            api(pending.url, pending.options);
        }
    } catch (e) {
        console.error('[Auth] 免密登录异常:', e);
    }
}

function hideAuthOverlay() {
    // 只有在非强制显示模式下才允许关闭（防止意外关闭）
    if (_authOverlayForceShow) {
        console.log('[Auth] hideAuthOverlay 被拦截（强制显示中）');
        return;
    }
    const overlay = document.getElementById('authOverlay');
    if (overlay) overlay.classList.remove('show');
    // 移除 auth-pending class 显示主内容
    document.body.classList.remove('auth-pending');
    console.log('[Auth] hideAuthOverlay 执行');
}

// 登录成功后调用此函数关闭遮罩
function hideAuthOverlayAfterLogin() {
    _authOverlayForceShow = false; // 解除强制显示
    const overlay = document.getElementById('authOverlay');
    if (overlay) overlay.classList.remove('show');
    document.body.classList.remove('auth-pending');
    console.log('[Auth] 登录成功，遮罩已关闭');
}


async function authSubmit() {
    const errEl = document.getElementById('authError');
    errEl.textContent = '';
    const remember = localStorage.getItem('babycare_keep_login') === '1';
    try {
        // 免密模式：后端已移除密码机制（/api/auth/setup 不再存在），
        // 无论当前显示的是哪个表单，统一走 /login。
        console.log('[Auth] 发送 login 请求到:', window.GATEWAY_PREFIX + '/api/auth/login');
        const res = await fetch(window.GATEWAY_PREFIX + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ remember }) });
        const data = await safeJson(res);
        console.log('[Auth] login 响应:', data, 'status:', res.status, 'ok:', res.ok);
        if (!res.ok || !data.success) { errEl.textContent = (data && data.message) || '登录失败'; return; }
        hideAuthOverlayAfterLogin();
        showToast.success('登录成功');
        if (_authPendingRequest) {
            const pending = _authPendingRequest;
            _authPendingRequest = null;
            api(pending.url, pending.options);
        }
    } catch (e) {
        errEl.textContent = '网络错误，请重试';
        console.error('[Auth] 登录异常:', e);
    }
}

// 退出登录。设置页的「账号安全」卡片已移除，当前**没有 UI 入口**：
// 该卡片文案与实际认证方式不符（本应用不设独立密码，认证由 fnOS 网关完成），
// 且网关模式下这里退不掉（清掉的是应用本地 session，刷新后网关头仍使其保持登录）。
// 函数保留供本地开发模式（BABYCARE_DEV_AUTH=1）调试使用。
async function authLogout() {
    try { await fetch(window.GATEWAY_PREFIX + '/api/auth/logout', { method: 'POST' }); } catch (e) {}
    location.reload();
}

// 立即显示认证遮罩（防止内容闪现），然后异步检查认证状态
function showAuthOverlayImmediate() {
    _authOverlayForceShow = true; // 标记为强制显示
    document.body.classList.add('auth-pending');
    const overlay = document.getElementById('authOverlay');
    if (overlay) {
        overlay.classList.add('show');
        const setupEl = document.getElementById('authSetupFields');
        if (setupEl) setupEl.style.display = 'none';
        document.getElementById('authLoginFields').style.display = 'none';
        document.getElementById('authTitle').textContent = '正在验证...';
    }
    console.log('[Auth] showAuthOverlayImmediate, forceShow=', _authOverlayForceShow);
}

// 点击背景不关闭登录弹窗（必须登录才能进入）
(function() {
    // 立即执行，不等待 DOMContentLoaded
    function attachAuthOverlayHandler() {
        const overlay = document.getElementById('authOverlay');
        if (overlay) {
            overlay.addEventListener('click', function(e) {
                // 只有点击遮罩背景时才触发（点击弹窗内部不触发）
                if (e.target !== overlay) return;
                // 阻止事件冒泡和默认行为
                e.stopImmediatePropagation();
                e.preventDefault();
                // 给弹窗一个轻微抖动动画，提示用户必须先登录
                const modal = overlay.querySelector('.modal');
                if (modal) {
                    modal.style.animation = 'none';
                    modal.offsetHeight; // 触发重绘
                    modal.style.animation = 'shake 0.3s ease';
                }
                console.log('[Auth] 点击背景被拦截，弹窗保持显示');
            });
            console.log('[Auth] 背景点击拦截已启用');
        } else {
            // 如果 overlay 还不存在，稍后重试
            setTimeout(attachAuthOverlayHandler, 50);
        }
    }
    attachAuthOverlayHandler();
})();

async function authCheckOnBoot() {
    // 先立即显示遮罩，防止内容闪现
    showAuthOverlayImmediate();
    try {
        const res = await fetch(window.GATEWAY_PREFIX + '/api/auth/status');
        const data = await safeJson(res);
        console.log('[Auth] status 响应:', data);
        if (data.success && data.authenticated) {
            // 已登录且 session 有效，直接进入首页
            console.log('[Auth] 已登录（session 有效），跳过登录界面');
            // 直接关闭遮罩（绕过强制显示检查，因为这是启动时检测到已登录）
            _authOverlayForceShow = false;
            hideAuthOverlay();
            return;
        }
        if (data.success && !data.authenticated) {
            _authPasswordless = !!data.passwordless;
            showAuthOverlay();
        }
    } catch (e) {
        // 网络异常时显示登录界面
        console.error('[Auth] status 请求异常:', e);
        showAuthOverlay();
    }
}

// 页面就绪后立即检查认证状态（在数据加载前弹出登录）
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', authCheckOnBoot);
} else {
    authCheckOnBoot();
}

/* ==================== 小萌宠物交互 ==================== */
function initMascot() {
    const avatar = document.getElementById('xiaomengAvatar');
    const bubble = document.getElementById('xiaomengBubble');
    const chat = document.getElementById('xiaomengChat');
    const chatBody = document.getElementById('xiaomengChatBody');
    const chatInput = document.getElementById('xiaomengInput');
    const chatSend = document.getElementById('xiaomengSend');
    const chatClose = document.getElementById('xiaomengChatClose');
    const modelSelect = document.getElementById('xiaomengModelSelect');
    if (!avatar) return;

    // ---- 动态暖心话语库（按场景分类） ----
    // 不使用 emoji（项目严禁），用文字 + 表情状态表达情感。
    const warmWords = {
        morning: [
            '早安呀～今天也要好好照顾宝宝哦',
            '新的一天开始啦，宝宝今天想做什么？',
            '记得给宝宝做晨间抚触，促进发育～',
            '早餐时间到，妈妈也要记得吃东西哦'
        ],
        noon: [
            '中午啦，宝宝该午睡了嘛？',
            '要不要趁宝宝睡着休息一下？',
            '午后阳光好，带宝宝晒晒太阳补钙～',
            '宝宝今天吃奶情况怎么样？'
        ],
        evening: [
            '辛苦一天啦，爸爸妈妈最棒！',
            '晚上记得给宝宝洗澡，放松入睡～',
            '今天宝宝的成长记录了吗？',
            '育儿路上不孤单，小萌一直陪着你'
        ],
        night: [
            '夜深了，宝宝睡得好吗？',
            '你也早点休息，别太累了～',
            '夜醒记得先观察，别急着喂奶哦',
            '晚安，明天又是美好的一天'
        ],
        care: [
            '记得给宝宝做抚触按摩哦～',
            '宝宝最近有没有新的进步？要不要记下来',
            '喂奶后记得拍嗝，防止吐奶～',
            '观察宝宝的情绪，他/她在和你交流呢',
            '定期量身高体重，记录成长曲线'
        ],
        encourage: [
            '每个宝宝节奏不同，不用焦虑～',
            '你已经做得很好了，相信自己！',
            '宝宝的一点小进步都是大成就',
            '育儿没有标准答案，你和宝宝最棒'
        ],
        longGap: [   // 距上次喂奶较长时
            '宝宝好像有一阵子没吃奶了，留意下饥饿信号哦',
            '距离上次喂奶有点久了，看看宝宝要不要吃奶～',
            '该喂奶了吗？小萌帮你记着呢'
        ],
        noRecord: [  // 今天还没任何记录
            '今天还没给宝宝做记录呢，随手记一笔吧～',
            '小萌发现今天还没记录哦，养成记录好习惯'
        ],
        sleepNight: [  // 夜间活跃提示
            '夜深了，妈妈早点休息，别太累啦',
            '宝宝睡了吗？你也抓紧时间休息哦'
        ]
    };

    function getTimePeriod() {
        const h = new Date().getHours();
        if (h >= 6 && h < 11) return 'morning';
        if (h >= 11 && h < 14) return 'noon';
        if (h >= 14 && h < 20) return 'evening';
        return 'night';
    }

    // ---- 数据快照：一次并行拉取，驱动上下文感知与「点击查询」卡片 ----
    // 原先只拉「今天」，导致按钮写着"最近7天"却只能显示今天；现统一拉近 7 天窗口（含今天），
    // 今日数据由窗口内按本地日期前缀派生，保证「今天」和「近7天」两套口径同源。
    let mascotSnapshot = null;
    let mascotSnapshotAt = 0;
    const SNAPSHOT_DAYS = 7;
    async function refreshMascotSnapshot() {
        const baby = App.babies?.find(b => b.id === App.currentBaby);
        if (!baby) { mascotSnapshot = null; return; }
        const today = getToday();
        const weekStart = formatDate(new Date(Date.now() - (SNAPSHOT_DAYS - 1) * 86400000));
        const [feedRes, feedWeekRes, sleepRes, diaperRes, growthRes] = await Promise.allSettled([
            api(`/api/babies/${baby.id}/feeding/stats`),
            api(`/api/babies/${baby.id}/feeding?start=${weekStart}&end=${today}`),
            api(`/api/babies/${baby.id}/sleep?start=${weekStart}&end=${today}`),
            api(`/api/babies/${baby.id}/diaper?start=${weekStart}&end=${today}`),
            api(`/api/babies/${baby.id}/growth`),
        ]);
        const pick = r => (r.status === 'fulfilled' && r.value?.success) ? (r.value.data ?? null) : null;
        const feeding = pick(feedRes) || {};
        const feedWeek = pick(feedWeekRes) || [];
        const sleepsWeek = pick(sleepRes) || [];
        const diapersWeek = pick(diaperRes) || [];
        const growthList = pick(growthRes) || [];
        const dayOf = t => String(t || '').slice(0, 10);
        const sleeps = sleepsWeek.filter(r => dayOf(r.start_time) === today);
        const diapers = diapersWeek.filter(r => dayOf(r.change_time) === today);
        const todayFed = feeding.today || {};
        let feedCount = 0, feedAmount = 0;
        Object.keys(todayFed).forEach(k => { feedCount += todayFed[k].count || 0; feedAmount += todayFed[k].amount || 0; });
        mascotSnapshot = {
            baby, today, days: SNAPSHOT_DAYS,
            // 任一请求失败就标记出来：否则会把"没拉到"误报成"没有记录"
            fetchFailed: [feedRes, feedWeekRes, sleepRes, diaperRes, growthRes].some(r => r.status !== 'fulfilled'),
            feeding, feedCount, feedAmount,
            feedWeek, feedWeekCount: feedWeek.length,
            feedWeekAmount: feedWeek.reduce((s, r) => s + (r.amount || 0), 0),
            sleeps, sleepMinToday: sleeps.reduce((s, r) => s + (r.duration_minutes || 0), 0),
            sleepsWeek, sleepMinWeek: sleepsWeek.reduce((s, r) => s + (r.duration_minutes || 0), 0),
            diapers, diapersWeek,
            growth: growthList[0] || null,
            growthPrev: growthList[1] || null,
            fetchedAt: Date.now(),
        };
        mascotSnapshotAt = mascotSnapshot.fetchedAt;
        return mascotSnapshot;
    }
    function snapshotAgeMs() { return Date.now() - (mascotSnapshotAt || 0); }

    // ---- 上下文感知问候：优先数据驱动，兜底时间分类 ----
    async function getContextualWarmWord() {
        if (!mascotSnapshot || snapshotAgeMs() > 5 * 60 * 1000) {
            try { await refreshMascotSnapshot(); } catch (e) { /* 网络失败不影响问候 */ }
        }
        const snap = mascotSnapshot;
        const period = getTimePeriod();
        const pool = [];
        if (snap) {
            if (snap.feedCount === 0 && period !== 'night') pool.push(...warmWords.noRecord);
            if (period === 'night') pool.push(...warmWords.sleepNight);
        }
        if (pool.length === 0) {
            pool.push(...(warmWords[period] || []), ...warmWords.care, ...warmWords.encourage);
        }
        return pool[Math.floor(Math.random() * pool.length)] || warmWords.encourage[0];
    }

    function getQuickQuestions() {
        const baby = App.babies?.find(b => b.id === App.currentBaby);
        // 「查询」类放最前：点击即由本地快照出数据卡片，不经过 AI / LLM。
        // 文案必须与 QUERY_CARDS 里的卡片标题一一对应，不要再出现"写着7天、只给今天"的情况。
        const queries = [
            '查询今日概览',
            '查询近7天喂养',
            '查询近7天睡眠',
            '查询尿布情况',
            '查询最新生长数据',
        ];
        const asks = ['宝宝发育正常吗？', '辅食怎么添加？', '睡眠不好怎么办？'];
        if (baby) {
            const age = baby.age_months ?? 0;
            if (age < 3) asks.unshift('新生儿怎么护理？');
            else if (age < 6) asks.unshift('什么时候可以翻身？');
            else if (age < 12) asks.unshift('辅食可以吃什么？');
            else asks.unshift('宝宝学走路要注意什么？');
        }
        asks.push('宝宝发烧了怎么办？', '维生素D要补到什么时候？');
        return queries.concat(asks).slice(0, 9);
    }

    function pickWarmWord() {
        // 同步回退（首次快照未完成时使用），异步场景走 getContextualWarmWord
        const period = getTimePeriod();
        const pool = [].concat(warmWords[period], warmWords.care, warmWords.encourage);
        return pool[Math.floor(Math.random() * pool.length)] || warmWords.encourage[0];
    }

    let bubbleTimer = null;
    function setMascotState(state) {
        avatar.parentElement.classList.remove('happy', 'thinking', 'alert');
        if (state) avatar.parentElement.classList.add(state);
    }

    function popBubble(text, state) {
        bubble.textContent = text || pickWarmWord();
        bubble.classList.add('show');
        avatar.classList.add('shake');
        setMascotState(state || 'happy');
        setTimeout(() => avatar.classList.remove('shake'), 500);
        clearTimeout(bubbleTimer);
        bubbleTimer = setTimeout(() => {
            bubble.classList.remove('show');
            setMascotState(null);
        }, 3500);
        if (!text) {
            getContextualWarmWord().then(ctxTxt => { if (ctxTxt) bubble.textContent = ctxTxt; }).catch(() => {});
        }
    }

    // ---- 数据查询卡片（本地快照直接渲染，不走 AI 绕路）----
    let activeTypingEl = null;
    function showTyping() {
        const el = document.createElement('div');
        el.className = 'chat-msg bot chat-typing';
        el.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
        chatBody.appendChild(el);
        chatBody.scrollTop = chatBody.scrollHeight;
        activeTypingEl = el;
        return el;
    }
    function typingElRemove() { if (activeTypingEl) { activeTypingEl.remove(); activeTypingEl = null; } }
    function renderMascotCard(title, html, state) {
        typingElRemove();
        const card = document.createElement('div');
        card.className = 'chat-msg bot chat-card';
        card.innerHTML = `<div class="chat-card-title">${escapeHtml(title)}</div><div class="chat-card-body">${html}</div>`;
        chatBody.appendChild(card);
        chatBody.scrollTop = chatBody.scrollHeight;
        setMascotState(state || 'happy');
        return card;
    }

    // ---- 点击查询：卡片全部由本地快照渲染，不经过 AI / LLM ----
    // 标签取值与后端 validators 保持一致：喂养 breast/bottle/solid/mixed、尿布 wet/dirty/both/dry
    const FEEDING_LABELS = { breast: '母乳', bottle: '配方奶', solid: '辅食', mixed: '混合' };
    const DIAPER_LABELS = { wet: '小便', dirty: '大便', both: '小便+大便', dry: '干爽' };

    function cardRow(label, value, note) {
        const tail = (note === undefined || note === null || note === '')
            ? '' : '<span>' + escapeHtml(String(note)) + '</span>';
        return '<div class="card-row"><span>' + escapeHtml(String(label)) + '</span><b>' +
            escapeHtml(String(value)) + '</b>' + tail + '</div>';
    }
    function cardGrid(rows) { return '<div class="card-grid">' + rows.join('') + '</div>'; }
    function emptyHtml(snap, text) {
        return '<p>' + escapeHtml(snap.baby?.name || '宝宝') + ' ' + escapeHtml(text) + '</p>';
    }
    function fmtDuration(min) {
        const m = Math.max(0, Math.round(Number(min) || 0));
        const h = Math.floor(m / 60), r = m % 60;
        return h ? (r ? h + 'h' + r + 'm' : h + 'h') : r + 'm';
    }
    // 睡眠时长参考区间（按月龄，与「今日睡眠」卡片口径一致）。
    // 注意：这是「宽区间」，用于首页/对话卡片给个大致范围；
    // 睡眠分析页的「睡眠目标」用的是后端 ai_engine.SLEEP_GUIDELINES 的具体建议值 + ±2 小时容差，
    // 两边语义不同（区间 vs 单值），但建议值都落在这些区间内 —— 改这边要确认没把后端值甩到区间外。
    function sleepRefHours(age) { return age <= 4 ? '12-16h' : age <= 12 ? '11-14h' : '10-13h'; }
    // 生长项与上次测量对比：显示成 "7.20 kg（+0.30）"
    function growthCompare(cur, prev, unit, digits) {
        if (cur === undefined || cur === null || cur === '') return '—';
        const base = Number(cur).toFixed(digits) + ' ' + unit;
        if (prev === undefined || prev === null || prev === '') return base;
        const d = Number(cur) - Number(prev);
        if (!isFinite(d) || Math.abs(d) < Math.pow(10, -digits) / 2) return base;
        return base + '（' + (d > 0 ? '+' : '') + d.toFixed(digits) + '）';
    }
    function retryHintHtml() {
        return '<p>部分数据没加载出来。</p>' +
            '<button type="button" class="chat-quick-btn chat-quick-btn-query" data-retry="1">重新查询</button>';
    }
    // 数据没拉回来时绑定一个重试按钮（存在 cardEl 就精确绑定，否则退回最后一个气泡）
    function bindRetry(cardEl, kind) {
        const scope = cardEl || chatBody.lastElementChild;
        const btn = scope && scope.querySelector && scope.querySelector('[data-retry]');
        if (!btn) return;
        btn.addEventListener('click', () => { btn.disabled = true; handleMascotQuery(kind); });
    }

    // 每种查询：快照 → { title, html }
    const QUERY_CARDS = {
        summary(s) {
            const rows = [
                cardRow('喂养', s.feedCount + ' 次', s.feedAmount ? s.feedAmount + ' ml' : ''),
                cardRow('睡眠', s.sleepMinToday ? fmtDuration(s.sleepMinToday) : '暂无', (s.sleeps || []).length + ' 次'),
                cardRow('换尿布', (s.diapers || []).length + ' 次'),
            ];
            if (s.growth?.weight) rows.push(cardRow('最新体重', s.growth.weight + ' kg', s.growth.record_date || ''));
            return { title: '今日概览 · ' + (s.baby?.name || '宝宝'), html: cardGrid(rows) };
        },
        feeding(s) {
            const today = s.feeding?.today || {};
            const keys = Object.keys(today);
            if (!keys.length) {
                return { title: '今日喂养', html: emptyHtml(s, '今天还没有喂养记录，点右上角＋记一笔吧。') };
            }
            const rows = keys.map(k => cardRow(FEEDING_LABELS[k] || k, today[k].count + ' 次',
                today[k].amount ? today[k].amount + ' ml' : ''));
            rows.push(cardRow('合计', s.feedCount + ' 次', s.feedAmount ? s.feedAmount + ' ml' : ''));
            return { title: '今日喂养 · 共 ' + s.feedCount + ' 次', html: cardGrid(rows) };
        },
        feeding7(s) {
            const list = s.feedWeek || [];
            if (!list.length) return { title: '近 7 天喂养', html: emptyHtml(s, '近 7 天还没有喂养记录。') };
            const byType = {};
            list.forEach(r => {
                const k = r.feeding_type || 'other';
                byType[k] = byType[k] || { count: 0, amount: 0 };
                byType[k].count += 1;
                byType[k].amount += r.amount || 0;
            });
            const rows = [cardRow('总次数', s.feedWeekCount + ' 次')];
            if (s.feedWeekAmount) rows.push(cardRow('总奶量', s.feedWeekAmount + ' ml'));
            Object.keys(byType).forEach(k => rows.push(cardRow(
                FEEDING_LABELS[k] || k, byType[k].count + ' 次',
                byType[k].amount ? byType[k].amount + ' ml' : '')));
            rows.push(cardRow('日均', (s.feedWeekCount / s.days).toFixed(1) + ' 次',
                s.feedWeekAmount ? Math.round(s.feedWeekAmount / s.days) + ' ml/天' : ''));
            return { title: '近 7 天喂养 · 共 ' + s.feedWeekCount + ' 次', html: cardGrid(rows) };
        },
        sleep(s) {
            const rows = [
                cardRow('总时长', s.sleepMinToday ? fmtDuration(s.sleepMinToday) : '暂无'),
                cardRow('次数', (s.sleeps || []).length + ' 次'),
                cardRow('参考', sleepRefHours(s.baby?.age_months ?? 0)),
            ];
            return {
                title: '今日睡眠' + (s.sleepMinToday ? ' · ' + fmtDuration(s.sleepMinToday) : ''),
                html: cardGrid(rows),
            };
        },
        sleep7(s) {
            const list = s.sleepsWeek || [];
            if (!list.length) return { title: '近 7 天睡眠', html: emptyHtml(s, '近 7 天还没有睡眠记录。') };
            const rows = [
                cardRow('日均时长', fmtDuration(s.sleepMinWeek / s.days)),
                cardRow('累计时长', fmtDuration(s.sleepMinWeek)),
                cardRow('记录数', list.length + ' 次'),
                cardRow('参考', sleepRefHours(s.baby?.age_months ?? 0) + '/天'),
            ];
            return { title: '近 7 天睡眠 · 日均 ' + fmtDuration(s.sleepMinWeek / s.days), html: cardGrid(rows) };
        },
        diaper(s) {
            const today = s.diapers || [], week = s.diapersWeek || [];
            // 与「记录」页口径一致：both 同时计入小便和大便
            const cnt = (list, t) => list.filter(r => r.diaper_type === t || r.diaper_type === 'both').length;
            if (!today.length && !week.length) {
                return { title: '尿布情况', html: emptyHtml(s, '近 7 天还没有换尿布记录。') };
            }
            const rows = [
                cardRow('今日总计', today.length + ' 次'),
                cardRow('小便', cnt(today, 'wet') + ' 次'),
                cardRow('大便', cnt(today, 'dirty') + ' 次'),
            ];
            if (week.length) {
                rows.push(cardRow('近 7 天', week.length + ' 次', (week.length / s.days).toFixed(1) + ' 次/天'));
            }
            return { title: '尿布情况 · 今日 ' + today.length + ' 次', html: cardGrid(rows) };
        },
        growth(s) {
            const g = s.growth;
            if (!g) {
                return { title: '最新生长', html: emptyHtml(s, '还没有身高体重记录，建议定期测量并记录成长曲线。') };
            }
            const p = s.growthPrev || null;
            const rows = [];
            if (g.weight) rows.push(cardRow('体重', growthCompare(g.weight, p?.weight, 'kg', 2)));
            if (g.height) rows.push(cardRow('身高', growthCompare(g.height, p?.height, 'cm', 1)));
            if (g.head_circumference) {
                rows.push(cardRow('头围', growthCompare(g.head_circumference, p?.head_circumference, 'cm', 1)));
            }
            if (g.bmi) rows.push(cardRow('BMI', g.bmi));
            if (g.record_date) rows.push(cardRow('测量日期', g.record_date, p?.record_date ? '上次 ' + p.record_date : ''));
            return {
                title: '最新生长记录',
                html: cardGrid(rows) + (p ? '<p>括号内为与上次测量的变化</p>' : ''),
            };
        },
    };

    async function handleMascotQuery(kind) {
        const build = QUERY_CARDS[kind];
        if (!build) return;
        const show = showTyping();
        setMascotState('thinking');
        try {
            if (!mascotSnapshot || snapshotAgeMs() > 5 * 60 * 1000) await refreshMascotSnapshot();
        } catch (e) { /* 没拉到数据，下面按失败态提示 */ }
        const snap = mascotSnapshot;
        setTimeout(() => {
            show.remove(); activeTypingEl = null;
            if (!snap) {
                // 区分「一个宝宝都没有」和「有宝宝但数据没拉回来」，不要一律说成"请先添加宝宝"
                const noBaby = !(App.babies && App.babies.length);
                const card = renderMascotCard(noBaby ? '还没有宝宝' : '数据没加载出来',
                    '<p>' + (noBaby ? '先添加一个宝宝，小萌就能帮你解读成长数据啦。'
                                    : '网络好像不太顺畅，再试一次吧。') + '</p>');
                if (!noBaby) bindRetry(card, kind);
                return;
            }
            const card = build(snap);
            const el = renderMascotCard(card.title, card.html + (snap.fetchFailed ? retryHintHtml() : ''));
            if (snap.fetchFailed) bindRetry(el, kind);
            setTimeout(() => setMascotState(null), 1500);
        }, 280);
    }

    function openChat() {
        chat.classList.add('show');
        setTimeout(() => chatInput.focus(), 50);
        loadChatHistory();
        renderQuickQuestions();
        // 每次打开刷新快照（帮助问候 + 卡片都是最新数据）
        refreshMascotSnapshot().catch(() => {});
        // 每次打开刷新模型列表（用户可能刚在设置页配置了大模型）
        fillChatModelSelect(modelSelect);
    }
    function closeChat() {
        chat.classList.remove('show');
    }

    // 250ms 延迟区分单击（冒话）与双击（进聊天）
    let clickTimer = null;
    avatar.addEventListener('click', () => {
        if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
        clickTimer = setTimeout(() => { popBubble(); clickTimer = null; }, 250);
    });
    avatar.addEventListener('dblclick', () => {
        clearTimeout(clickTimer);
        clickTimer = null;
        openChat();
    });

    chatClose.addEventListener('click', closeChat);
    chat.addEventListener('click', (e) => { if (e.target === chat) closeChat(); });

    // ---- 快捷问题按钮 ----
    function renderQuickQuestions() {
        let container = document.getElementById('xiaomengQuickQs');
        if (!container) {
            container = document.createElement('div');
            container.id = 'xiaomengQuickQs';
            container.className = 'chat-quick-qs';
            chatBody.appendChild(container);
        }
        container.innerHTML = '';
        // 查询类按钮 → QUERY_CARDS 的 key；文案必须与 getQuickQuestions() 里的完全一致
        const queryMap = {
            '查询今日概览': 'summary',
            '查询近7天喂养': 'feeding7',
            '查询近7天睡眠': 'sleep7',
            '查询尿布情况': 'diaper',
            '查询最新生长数据': 'growth',
            '查询今日喂养': 'feeding',
            '查询今日睡眠': 'sleep',
        };
        getQuickQuestions().forEach(q => {
            const btn = document.createElement('button');
            // 查询类问题用主题色实心变体，便于区分"点击查询"
            btn.className = 'chat-quick-btn' + (q.startsWith('查询') ? ' chat-quick-btn-query' : '');
            btn.textContent = q;
            btn.type = 'button';
            btn.addEventListener('click', () => {
                const kind = queryMap[q];
                if (kind) {
                    // 数据查询：发一条用户气泡作为提示，随后渲染数据卡片
                    const userMsg = document.createElement('div');
                    userMsg.className = 'chat-msg user';
                    userMsg.textContent = q;
                    chatBody.appendChild(userMsg);
                    chatBody.scrollTop = chatBody.scrollHeight;
                    handleMascotQuery(kind);
                } else {
                    chatInput.value = q;
                    sendMsg();
                }
            });
            container.appendChild(btn);
        });
    }

    // ---- 发送消息（接通后端 API） ----
    function sendMsg() {
        const val = chatInput.value.trim();
        if (!val) return;
        // 用户消息
        const userMsg = document.createElement('div');
        userMsg.className = 'chat-msg user';
        userMsg.textContent = val;
        chatBody.appendChild(userMsg);
        chatInput.value = '';
        chatBody.scrollTop = chatBody.scrollHeight;

        // 显示打字动画 + 思考态
        const typingEl = showTyping();
        setMascotState('thinking');

        // 调用后端 AI API（携带对话内选择的回答模型 + 工具调用开关）
        // 注意：api() 返回的是已解析的 JSON 对象，不能链式再调 res.json()
        api('/api/ai/chat', {
            method: 'POST',
            body: JSON.stringify({
                message: val,
                baby_id: App.currentBaby || null,
                model: modelSelect ? modelSelect.value : '',
                enable_tools: !!App.currentBaby,
                max_tool_rounds: 5
            })
        })
        .then(data => {
            typingEl.remove();
            activeTypingEl = null;
            setMascotState('happy');
            // 渲染 AI 工具调用过程卡片（如有）
            if (Array.isArray(data.tool_events) && data.tool_events.length > 0) {
                data.tool_events.forEach(ev => appendToolEventCard(ev));
            }
            appendBotMsg(data.answer || '抱歉，我暂时回答不了这个问题，换个方式问问？', data.llm_model || '');
            setTimeout(() => setMascotState(null), 2000);
        })
        .catch(() => {
            typingEl.remove();
            activeTypingEl = null;
            setMascotState(null);
            appendBotMsg('网络好像有问题，等一下再试试吧～', '');
        });
    }

    // 安全渲染 bot 回复：转义 HTML 后把换行转为 <br>、**加粗**转为 <b>
    function renderBotText(raw) {
        let safe = escapeHtml(String(raw));
        safe = safe.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
        safe = safe.replace(/\n/g, '<br>');
        return safe;
    }
    function appendBotMsg(text, model) {
        const wrap = document.createElement('div');
        wrap.className = 'chat-msg bot';
        const body = document.createElement('div');
        body.className = 'chat-msg-body';
        body.innerHTML = renderBotText(text);
        wrap.appendChild(body);
        if (model && model !== '规则引擎') {
            const tag = document.createElement('span');
            tag.className = 'chat-msg-model';
            tag.textContent = 'via ' + model;
            wrap.appendChild(tag);
        }
        const ts = document.createElement('span');
        ts.className = 'chat-msg-time';
        ts.textContent = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
        wrap.appendChild(ts);
        chatBody.appendChild(wrap);
        chatBody.scrollTop = chatBody.scrollHeight;
    }

    // 渲染 AI 工具调用事件卡片（在小萌对话中显示 AI 执行了哪些操作）
    const _TOOL_LABELS = {
        query_baby_info: '查询宝宝信息',
        query_records: '查询记录',
        create_feeding_record: '创建喂养记录',
        create_sleep_record: '创建睡眠记录',
        create_diaper_record: '创建如厕记录',
        create_growth_record: '创建生长记录',
        create_health_record: '创建健康档案',
        delete_record: '删除记录',
        update_record: '修改记录',
        analyze_baby_data: '分析宝宝数据',
        search_knowledge: '检索育儿知识',
        assess_growth: '评估生长发育',
    };
    // 每个工具的「一句话结果摘要」，让卡片有信息量而不是只有个工具名
    function _toolSummary(ev) {
        const r = ev && ev.result;
        if (!r) return '';
        if (!ev.success) return r.error || '执行失败';

        // 优先级：工具专属摘要 > 后端 message > 空
        // （update_record 的 message 只有「已修改 X 记录 ID=n」，看不出改了什么，
        //   所以它的「旧 → 新」对照必须排在 message 之前）
        if (ev.tool === 'update_record') {
            const diff = r.changed || {};
            const keys = Object.keys(diff);
            if (keys.length) {
                const fieldLabel = {
                    amount: '奶量', start_time: '开始时间', end_time: '结束时间', feeding_type: '喂养类型',
                    side: '哺乳侧', left_duration: '左侧时长', right_duration: '右侧时长',
                    duration_minutes: '睡眠时长', sleep_quality: '睡眠质量', is_nap: '白天小憩',
                    change_time: '时间', diaper_type: '如厕类型', color: '便便颜色',
                    skin_condition: '皮肤状况', brand: '品牌', record_date: '日期',
                    weight: '体重', height: '身高', head_circumference: '头围', bmi: 'BMI',
                    record_type: '类型', title: '标题', temperature: '体温',
                    symptom: '症状', medication: '用药', diagnosis: '诊断', advice: '医嘱',
                };
                const fmt = v => (v === null || v === undefined || v === '') ? '—' : v;
                const parts = keys.slice(0, 3).map(k => {
                    const d = diff[k] || {};
                    return (fieldLabel[k] || k) + ' ' + fmt(d['旧']) + ' → ' + fmt(d['新']);
                });
                return parts.join('；') + (keys.length > 3 ? ' 等' : '');
            }
        }

        if (r.message) return String(r.message);

        switch (ev.tool) {
            case 'search_knowledge': {
                const hits = Array.isArray(r.results) ? r.results : [];
                if (!hits.length) return r.hint || '站内没有找到相关内容';
                return '找到 ' + r.count + ' 条：' + hits.map(h => h.title).join('、');
            }
            case 'assess_growth': {
                const p = r.percentiles || {};
                const metricLabel = { weight: '体重', height: '身高', head_circumference: '头围' };
                const parts = Object.keys(p).map(k => (metricLabel[k] || k) + ' ' + (p[k].classification || ''));
                if (!parts.length) return '';
                const v = r.velocity || {};
                const extra = v.weight_verdict ? '；近期体重增速' + v.weight_verdict : '';
                return parts.join('，') + extra;
            }
            default:
                return '';
        }
    }
    function appendToolEventCard(ev) {
        if (!ev || !ev.tool) return;
        const wrap = document.createElement('div');
        const ok = !!ev.success;
        wrap.className = 'chat-msg bot tool-event' + (ok ? '' : ' tool-event-fail');
        const title = document.createElement('div');
        title.className = 'tool-event-title';
        const icon = document.createElement('span');
        icon.className = 'tool-event-icon';
        icon.textContent = ok ? '[OK]' : '[X]';
        title.appendChild(icon);
        const label = document.createElement('span');
        label.textContent = _TOOL_LABELS[ev.tool] || ev.tool;
        title.appendChild(label);
        wrap.appendChild(title);

        // 摘要：优先用工具专属摘要，其次 message/error
        const msg = _toolSummary(ev);
        if (msg) {
            const body = document.createElement('div');
            body.className = 'tool-event-body';
            body.textContent = String(msg);
            wrap.appendChild(body);
        }
        chatBody.appendChild(wrap);
        chatBody.scrollTop = chatBody.scrollHeight;
    }

    chatSend.addEventListener('click', sendMsg);
    chatInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendMsg(); });

    // 对话内模型选择器（与 AI 页共享选择状态）
    fillChatModelSelect(modelSelect);

    // ---- 加载聊天历史 ----
    function loadChatHistory() {
        if (!App.currentBaby) return;
        api(`/api/ai/chat/history/${App.currentBaby}`)
            .then(data => {
                if (data.success && data.history?.length > 0) {
                    chatBody.innerHTML = '';
                    data.history.forEach(msg => {
                        const el = document.createElement('div');
                        el.className = `chat-msg ${msg.role === 'user' ? 'user' : 'bot'}`;
                        el.textContent = msg.content;
                        chatBody.appendChild(el);
                    });
                    renderQuickQuestions();
                    chatBody.scrollTop = chatBody.scrollHeight;
                }
            })
            .catch(() => { /* 静默失败，保留默认欢迎语 */ });
    }

    // ---- 主动关怀定时器 ----
    // 每 60 秒补一次数据快照；每 180 秒基于真实状态决定是否给一条提醒：
    //    - 今天还没记录（10 点后）→ 提醒记录
    //    - 夜间活跃（22 点后）→ 提醒休息
    //    - 其他 → 小概率随机暖心问候，避免骚扰
    let careTicker = 0;
    setInterval(() => {
        // 每次都补快照，保证卡片和问候基于最新数据
        if (mascotSnapshot && snapshotAgeMs() > 45 * 1000) refreshMascotSnapshot().catch(() => {});
        careTicker++;
        if (careTicker % 3 === 0 && !chat.classList.contains('show')) {
            const snap = mascotSnapshot;
            const period = getTimePeriod();
            const h = new Date().getHours();
            if (snap && snap.feedCount === 0 && h >= 10 && period !== 'night') {
                getContextualWarmWord().then(t => popBubble(t, 'alert')).catch(() => {});
            } else if (period === 'night' && h >= 22 && Math.random() < 0.5) {
                popBubble(warmWords.sleepNight[Math.floor(Math.random() * warmWords.sleepNight.length)] || pickWarmWord(), 'alert');
            } else if (Math.random() < 0.08) {
                getContextualWarmWord().then(t => popBubble(t, 'happy')).catch(() => {});
            }
        }
    }, 60000);

    // 启动后先刷一次快照，给出第一条上下文问候（延迟等宝宝加载完成）
    setTimeout(() => refreshMascotSnapshot().catch(() => {}), 1200);
}

// 页面就绪后初始化小萌宠物
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initMascot);
} else {
    initMascot();
}

// ==================== 消息提示 ====================

function showToast(message) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = 'toast show';  // 重置为默认样式
    setTimeout(() => toast.classList.remove('show'), 2500);
}

// 分级提示方法
showToast.success = function(msg) { showToast._show(msg, 'success'); };
showToast.error = function(msg) { showToast._show(msg, 'error'); };
showToast.warning = function(msg) { showToast._show(msg, 'warning'); };
showToast.info = function(msg) { showToast._show(msg, 'info'); };
showToast._show = function(msg, type) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = msg;
    toast.className = 'toast show toast-' + type;
    setTimeout(() => toast.classList.remove('show'), type === 'error' ? 3500 : 2500);
};

function formatDate(date) {
    if (!date) return '';
    const d = new Date(date);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/**
 * 安全解析时间字符串。
 * 库里存的是 'YYYY-MM-DD HH:MM:SS'（空格分隔），iOS Safari 不认这种格式，
 * 直接 new Date() 会得到 Invalid Date，页面上的「X 分钟前」就变成 NaN。
 */
function parseDateSafe(value) {
    if (!value) return null;
    const s = String(value).trim().replace(' ', 'T');
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
}

function formatDateTime(datetime) {
    if (!datetime) return '';
    const d = parseDateSafe(datetime);
    if (!d) return String(datetime).slice(0, 16);   // 解析不了就原样展示，别显示 Invalid Date
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** 秒 → 「12分30秒」（喂奶/吸奶时长展示） */
function formatDurationCN(seconds) {
    const s = Number(seconds) || 0;
    if (s <= 0) return '';
    const m = Math.floor(s / 60);
    const rest = s % 60;
    return rest ? `${m}分${rest}秒` : `${m}分`;
}

/** 分钟 → 「8小时30分」/「45分钟」。睡眠时长用这个（formatDurationCN 是秒口径，别混） */
function formatMinutesCN(mins) {
    const m = Math.round(Number(mins) || 0);
    if (m <= 0) return '';
    const h = Math.floor(m / 60);
    const rest = m % 60;
    if (!h) return `${rest}分钟`;
    return rest ? `${h}小时${rest}分` : `${h}小时`;
}

/** 分钟 → 「2小时10分」/「45分钟」 */
function formatAgoCN(minutes) {
    const m = Number(minutes);
    if (!isFinite(m) || m < 0) return '--';
    if (m < 1) return '刚刚';
    if (m < 60) return `${m}分钟前`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}小时${m % 60 ? (m % 60) + '分' : ''}前`;
    return `${Math.floor(h / 24)}天前`;
}

function getToday() {
    return formatDate(new Date());
}

/**
 * 本地时间戳字符串："YYYY-MM-DD HH:MM:SS"。
 *
 * 不要直接用 nowLocalStr() 提交记录时间——它返回的是 UTC 时间，
 * 东八区在每天 00:00–08:00 之间 UTC 仍是前一天，存进去的日期会落后一天，
 * 于是「今日统计」（按本地日期前缀匹配）会把刚记的一条漏算成 0。
 * 统一用本函数，保证与 datetime-local 输入框、后端既有数据格式一致。
 */
function nowLocalStr() {
    const d = new Date();
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
           `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 把 Date 对象转成本地时间戳字符串（用于计算过去某刻，如睡眠开始时间） */
function toLocalStr(date) {
    const d = new Date(date);
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
           `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function showModal(arg) {
    // 兼容两种调用：传入元素 id（原行为），或传入 HTML 字符串（动态注入通用弹窗）。
    // 此前多处编辑弹窗用 showModal(整段HTML) 打开，而本函数只认 id 导致弹窗永远打不开。
    if (typeof arg === 'string' && arg.trim().startsWith('<')) {
        let gen = document.getElementById('genericModal');
        if (!gen) {
            gen = document.createElement('div');
            gen.id = 'genericModal';
            gen.className = 'modal-overlay';
        gen.innerHTML = '<div class="modal"></div>';
        document.body.appendChild(gen);
        }
        gen.querySelector('.modal').innerHTML = arg;
        gen.classList.add('show');
        return;
    }
    const el = typeof arg === 'string' ? document.getElementById(arg) : arg;
    if (el) el.classList.add('show');
}
function hideModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove('show');
}
// 全局兜底：多处弹窗的"取消/保存"按钮通过 closeModal() 关闭当前打开的弹窗。
// 原代码大量调用 closeModal() 但该函数从未定义，导致弹窗无法关闭（含编辑喂奶记录的保存后关闭）。
function closeModal() {
    document.querySelectorAll('.modal-overlay.show').forEach(el => el.classList.remove('show'));
}

// 统一兜底：所有弹窗关闭按钮通过事件委托关闭所在弹窗
// （防止个别按钮漏绑事件，hideModal 幂等，与已有 inline onclick / 监听器不冲突）
document.addEventListener('click', (e) => {
    const btn = e.target.closest('.modal-close');
    if (!btn) return;
    const overlay = btn.closest('.modal-overlay');
    if (overlay && overlay.id) hideModal(overlay.id);
});

// ==================== 宝宝管理 ====================

function initBabySelector() {
    const selector = document.getElementById('babySelector');
    const addBtn = document.getElementById('addBabyBtn');

    selector.addEventListener('click', () => {
        if (App.babies.length === 0) {
            showAddBabyModal();
        } else {
            // 切换宝宝功能
            const currentIndex = App.babies.findIndex(b => b.id === App.currentBaby);
            const nextIndex = (currentIndex + 1) % App.babies.length;
            selectBaby(App.babies[nextIndex].id);
        }
    });

    addBtn.addEventListener('click', showAddBabyModal);
    document.getElementById('closeBabyModal').addEventListener('click', () => hideModal('addBabyModal'));
    document.getElementById('cancelBabyForm').addEventListener('click', () => hideModal('addBabyModal'));
    document.getElementById('babyModalTitle').textContent = '添加宝宝';

    document.getElementById('babyForm').addEventListener('submit', function(e) {
        e.preventDefault();
        const name = document.getElementById('babyName').value.trim();
        const birthday = document.getElementById('babyBirthday').value;
        const gender = document.getElementById('babyGender').value;
        const dueDate = document.getElementById('babyDueDate')?.value || null;

        if (!name || !birthday) {
            showToast.warning('请填写完整信息');
            return;
        }

        api('/api/babies', {
            method: 'POST',
            body: JSON.stringify({ name, birthday, gender, due_date: dueDate })
        }).then(res => {
            if (res.success) {
                hideModal('addBabyModal');
                showToast.success('宝宝添加成功');
                this.reset();
                loadBabies();
            } else {
                showToast.error(res.message || '添加失败');
            }
        });
    });
}

function showAddBabyModal() {
    document.getElementById('babyForm').reset();
    document.getElementById('babyModalTitle').textContent = '添加宝宝';
    showModal('addBabyModal');
}

function loadBabies() {
    api('/api/babies').then(res => {
        if (res.success) {
            App.babies = res.data;

            if (App.babies.length > 0) {
                if (!App.currentBaby || !App.babies.find(b => b.id === App.currentBaby)) {
                    App.currentBaby = App.babies[0].id;
                }
                const baby = App.babies.find(b => b.id === App.currentBaby);
                document.getElementById('currentBabyName').textContent = `${baby.name} (${baby.age})`;
                if (App.currentPage) { refreshCurrentPage(); } else { loadDashboard(); }
            } else {
                document.getElementById('currentBabyName').textContent = '请选择宝宝';
            }
        }
    });
}

function selectBaby(babyId) {
    App.currentBaby = babyId;
    const baby = App.babies.find(b => b.id === babyId);
    document.getElementById('currentBabyName').textContent = `${baby.name} (${baby.age})`;
    if (App.currentPage) { refreshCurrentPage(); } else { loadDashboard(); }
}

// ==================== 导航 ====================


function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const page = item.dataset.page;
            switchPage(page);
        });
    });
}

// ==================== 底部 Tab + 宫格导航 ====================
// 5 个一级 tab（首页/记录/成长/知识/系统），每个非首页 tab 承载一个宫格页面；
// 点击宫格 tile 进入对应详情页（含图表）。首页以数据展示为主。
const NAV_HUBS = [
    { hub: 'home', label: '首页', icon: 'icon-home', page: 'dashboard' },
    {
        hub: 'record', label: '记录', icon: 'icon-clipboard', page: 'record',
        items: [
            { page: 'timeline', label: '时间线', icon: 'icon-clock' },
            { page: 'feeding', label: '喂奶', icon: 'icon-bottle' },
            { page: 'pumping', label: '吸奶', icon: 'icon-pumping' },
            { page: 'sleep-record', label: '睡眠记录', icon: 'icon-moon' },
            { page: 'diaper-record', label: '换尿布', icon: 'icon-diaper' },
            // 体温 / 用药提醒 / 疫苗 / 过敏测试已并入「健康档案」的 Tab，不再单列入口
            { page: 'solidfood', label: '辅食', icon: 'icon-bowl' },
            { page: 'tummytime', label: '趴睡训练', icon: 'icon-activity' },
            { page: 'diaper-price', label: '比价记账', icon: 'icon-tag' },
            { page: 'diary', label: '日记', icon: 'icon-book' },
            { page: 'photos', label: '相册', icon: 'icon-image' },
            { page: 'leap', label: '飞跃期', icon: 'icon-rocket' },
            { page: 'fontanelle', label: '囟门', icon: 'icon-circle' },
            { page: 'teeth', label: '出牙记录', icon: 'icon-tooth' },
            { page: 'health', label: '健康档案', icon: 'icon-heart2' },
            { page: 'sounds', label: '安抚', icon: 'icon-wave' }
        ]
    },
    {
        hub: 'growth', label: '成长', icon: 'icon-chart', page: 'growth-hub',
        items: [
            { page: 'growth', label: '成长曲线', icon: 'icon-chart' },
            { page: 'sleep-analysis', label: '睡眠分析', icon: 'icon-wave' },
            { page: 'milestones', label: '里程碑', icon: 'icon-trophy' },
            { page: 'firsts', label: '第一次', icon: 'icon-star' },
            { page: 'bmi', label: 'BMI', icon: 'icon-scale' },
            { page: 'asq', label: '发育筛查', icon: 'icon-list' },
            { page: 'pattern', label: '作息节律', icon: 'icon-bar' },
            { page: 'reports', label: '阶段报告', icon: 'icon-doc' }
        ]
    },
    {
        hub: 'knowledge', label: '知识', icon: 'icon-book-open', page: 'knowledge-hub',
        items: [
            { page: 'knowledge', label: '知识库', icon: 'icon-book-open' },
            { page: 'wiki', label: '健康百科', icon: 'icon-heart2' },
            { page: 'recipes', label: '食谱', icon: 'icon-chef' },
            { page: 'activities', label: '活动', icon: 'icon-game' }
        ]
    },
    {
        hub: 'system', label: '系统', icon: 'icon-gear', page: 'system',
        items: [
            { page: 'settings', label: '设置', icon: 'icon-gear' },
            { page: 'clinic', label: '看诊摘要', icon: 'icon-stethoscope' }
            // AI 助手由小萌浮窗承担，不单列页面
        ]
    }
];

// 首页快捷入口（常用记录）
// pageId -> 底部 tab（用于高亮）
const HUB_OF = { dashboard: 'home' };
NAV_HUBS.forEach(g => {
    HUB_OF[g.page] = g.hub;
    (g.items || []).forEach(it => { HUB_OF[it.page] = g.hub; });
});
HUB_OF['ai'] = 'system'; // 兜底

function makeNavTile(it) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'nav-item';
    b.dataset.page = it.page;
    b.innerHTML =
        '<span class="nav-icon"><svg class="ic-svg" viewBox="0 0 24 24" aria-hidden="true"><use href="#' + it.icon + '"/></svg></span>' +
        '<span class="nav-label">' + it.label + '</span>';
    return b;
}

function buildHubPages() {
    // 各 hub 宫格（首页以原有数据展示为主，不再重复加快捷入口）
    NAV_HUBS.forEach(g => {
        if (g.hub === 'home') return;
        const c = document.getElementById('hub-' + g.hub);
        if (!c) return;
        c.innerHTML = '';
        (g.items || []).forEach(it => c.appendChild(makeNavTile(it)));
    });
}

function initBottomTabs() {
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const g = NAV_HUBS.find(x => x.hub === tab.dataset.hub);
            if (g) switchPage(g.page);
        });
    });
}

// ==================== 页面导航历史 ====================
// 栈里存的是「来时的页面」：switchPage 进入新页前，把旧页压栈。
// 这样 goBack() pop 出来的就是真正的上一页。
// （旧实现压的是新页面，导致 pop 出来是当前页自己，点一次返回像没反应。）
const _pageHistory = [];  // 页面历史栈
const _MAX_HISTORY = 50;  // 最大历史记录数
let _skipHistoryPush = false;  // 标记跳过历史入栈（返回时使用）

// 一级页面：底部 tab 直接到达的宫格页和首页。这些页面不显示返回按钮，
// 只有从宫格/链接点进去的子页面才有「返回」。
const TOP_LEVEL_PAGES = ['dashboard', 'record', 'growth-hub', 'knowledge-hub', 'system'];

// 返回上一页
function goBack() {
    if (_pageHistory.length === 0) {
        console.log('[goBack] 没有历史记录，返回首页');
        _skipHistoryPush = true;  // 回首页不该把当前页压进栈，否则按钮又会冒出来
        switchPage('dashboard');
        updateBackButton();
        return;
    }
    const prevPage = _pageHistory.pop();
    console.log('[goBack] 返回:', prevPage, '历史剩余:', _pageHistory.length);
    _skipHistoryPush = true;  // 标记跳过入栈
    switchPage(prevPage);
    updateBackButton();
}

// 更新返回按钮显示状态
function updateBackButton() {
    const backBtn = document.getElementById('backBtn');
    if (!backBtn) return;
    // 一级页面不给返回（它们是入口，不是「进来的」）
    const onTopLevel = TOP_LEVEL_PAGES.indexOf(App.currentPage) !== -1;
    if (_pageHistory.length > 0 && !onTopLevel) {
        backBtn.classList.add('show');
    } else {
        backBtn.classList.remove('show');
    }
}

function switchPage(page) {
    console.log('[switchPage] 切换到:', page);
    const prevPage = App.currentPage;
    App.currentPage = page;

    // 「睡眠分析」这类页面已并进成长页的 Tab；疫苗/体温/用药提醒/过敏测试
    // 并进了健康档案的 Tab。它们都没有独立 section 了，
    // 这里先切到宿主页面再激活对应 Tab，否则 #page-xxx 找不到会白屏。
    const tabTarget = GROWTH_TAB_PAGES[page] || HEALTH_TAB_PAGES[page];
    if (tabTarget) {
        switchPage(tabTarget.host);
        activatePageTab(tabTarget.host, tabTarget.tab);
        // switchPage(宿主) 把 currentPage 改成了宿主，这里改回来，
        // 保证底部导航、相关功能、切宝宝刷新都还认得合并前的 page id
        App.currentPage = page;
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        document.querySelectorAll('.nav-item[data-page="' + page + '"]').forEach(t => t.classList.add('active'));
        renderRelatedLinks(page);
        return;
    }

    // 更新页面显示
    const pageEl = document.getElementById('page-' + page);
    if (!pageEl) { console.warn('[switchPage] 未找到页面:', page); return; }
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    pageEl.classList.add('active');

    // 记录「来时的页面」（跳过返回时的入栈）
    if (!_skipHistoryPush) {
        // 同页刷新不记；来时页已在栈顶也不重复记
        if (prevPage !== page &&
            (_pageHistory.length === 0 || _pageHistory[_pageHistory.length - 1] !== prevPage)) {
            _pageHistory.push(prevPage);
            // 限制历史记录数量
            if (_pageHistory.length > _MAX_HISTORY) {
                _pageHistory.shift();
            }
        }
    }
    _skipHistoryPush = false;
    updateBackButton();

    // 底部 tab 高亮
    const hub = HUB_OF[page] || 'home';
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.toggle('active', t.dataset.hub === hub));

    // 宫格 tile 高亮（隐藏态不可见，但保持状态一致）
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.nav-item[data-page="' + page + '"]').forEach(t => t.classList.add('active'));

    // 加载页面数据
    loadPageData(page);

    // 渲染底部「相关功能」跳转条
    renderRelatedLinks(page);
}

// 各页面的数据加载分支。抽成独立函数，供切换页面与切换宝宝时复用，
// 避免「换了个宝宝但页面上还是上一个宝宝的数据」。
function loadPageData(page) {
    switch (page) {
        case 'dashboard':
            loadDashboard();
            break;
        case 'timeline':
            loadTimeline();
            break;
        case 'feeding':
            loadFeedingPage();
            break;
        case 'pumping':
            loadPumpingPage();
            break;
        case 'sleep-record':
            loadSleepPage();
            break;
        case 'diaper-record':
            loadDiaperPage();
            break;
        case 'sleep-analysis': {
            // canvas 在 display:none 时宽高是 0，画了也是空白，只在视图可见时才画。
            // 首次进入由 activateGrowthTab 触发（先显示再画），这里负责后续刷新。
            const view = document.getElementById('growthViewSleepAnalysis');
            if (view && view.style.display !== 'none') {
                loadSleepAnalysis();
                loadSleepPrediction();
            }
            break;
        }
        case 'temperature':
            loadTemperaturePage();
            break;
        case 'med-reminder':
            loadMedReminders();
            break;
        case 'tummytime':
            loadTummyTimePage();
            break;
        case 'diaper-price':
            loadShoppingPage();
            break;
        case 'leap':
            loadLeapRecords();
            break;
        case 'fontanelle':
            loadFontanelleRecords();
            break;
        case 'asq':
            loadAsqScreenings();
            break;
        case 'pattern':
            initPattern();
            break;
        case 'growth':
            // 直接进「成长曲线」时回到「记录」Tab，避免上次停在睡眠分析、
            // 点进来却看到别的页。深链到睡眠分析时 currentPage 是 'sleep-analysis'，
            // 不在这里重置，随后由 activateGrowthTab 切过去，顺序不受影响。
            if (App.currentPage === 'growth') activateGrowthTab('records');
            loadGrowthPage();
            break;
        case 'milestones':
            loadMilestonesPage();
            break;
        case 'photos':
            loadPhotosPage();
            break;
        case 'vaccines':
            loadVaccinesPage();
            break;
        case 'diary':
            loadDiaryList();
            break;
        case 'knowledge':
            if (!App.knowledgeInitialized) { initKnowledgeCategories(); App.knowledgeInitialized = true; }
            loadKnowledgeList();
            break;
        case 'wiki':
            initWikiPage();
            loadWikiList();
            break;
        case 'settings':
            loadSettings();
            break;
        case 'ai':
            initAIChat();
            break;
        case 'reports': {
            // 旧版 initReportPage/loadReport 渲染的是 reportContent/reportActions，
            // 这两个节点在 index.html 里已不存在；且它读的是 tab.dataset.type（现为 data-tab），
            // 每次切页都会重复绑定点击事件。这里改为直接按当前 tab 刷新数据。
            const reportTab = document.querySelector('.report-tab.active')?.dataset.tab || 'sleep';
            loadReportData(reportTab);
            break;
        }
        case 'clinic':
            loadClinicSummary();
            break;
        case 'sounds':
            break;
        case 'bmi':
            loadBmiData();
            break;
        case 'recipes':
            loadRecipes();
            break;
        case 'activities':
            loadActivities();
            break;
        case 'teeth':
            loadTeethRecords();
            break;
        case 'health':
            if (typeof loadOverview === 'function') loadOverview();
            break;
        case 'firsts':
            loadFirstsRecords();
            break;
        case 'allergy-detail':
            loadAllergyTests();
            break;
        case 'solidfood':
            loadSolidFoodRecords();
            loadSolidFoodStats();
            break;
    }
}

// 重新加载当前页面数据（切换宝宝后调用）
function refreshCurrentPage() {
    // 换宝宝时清掉「选中了哪几条记录」这类跟具体数据绑定的状态。
    // 不同宝宝的记录 id 可能撞号，不清会莫名选中另一个宝宝的照片。
    // 注意别清 currentMilestoneCategory 之类属于用户偏好的筛选状态。
    App.gpSelected = [];

    if (!App.currentPage) return;
    if (App.currentPage === 'growth-hub' || App.currentPage === 'knowledge-hub' || App.currentPage === 'system') return;
    loadPageData(App.currentPage);
}

// ==================== 相关功能互跳 ====================
// 哪些页面之间 Actually 有关联：大体分三类
//   1) 同一份数据的不同看法（成长曲线 ↔ BMI、里程碑 ↔ 第一次）
//   2) 上下游（体温 → 健康档案、睡眠记录 → 睡眠分析）
//   3) 同一场景的相邻动作（喂奶 ↔ 吸奶、疫苗 → 健康档案）
const PAGE_LINKS = {
    timeline:       ['feeding', 'sleep-record', 'diaper-record', 'growth'],
    feeding:        ['pumping', 'sleep-analysis', 'pattern', 'reports'],
    pumping:        ['feeding', 'reports'],
    'sleep-record': ['sleep-analysis', 'pattern', 'sounds'],
    'sleep-analysis': ['sleep-record', 'pattern', 'reports'],
    'diaper-record':['diaper-price', 'reports'],
    temperature:    ['med-reminder', 'health'],
    'med-reminder': ['temperature', 'health'],
    tummytime:      ['milestones', 'leap'],
    vaccines:       ['health'],
    'diaper-price': ['diaper-record'],
    diary:          ['photos', 'firsts'],
    leap:           ['milestones', 'sleep-record', 'tummytime'],
    fontanelle:     ['teeth', 'health', 'growth'],
    teeth:          ['fontanelle', 'milestones', 'health'],
    health:         ['temperature', 'med-reminder', 'vaccines', 'teeth', 'growth'],
    sounds:         ['sleep-record'],

    firsts:         ['milestones', 'diary', 'photos'],
    bmi:            ['growth'],
    asq:            ['milestones', 'leap'],
    pattern:        ['feeding', 'sleep-record', 'diaper-record', 'reports'],
    reports:        ['growth', 'feeding', 'sleep-record', 'diaper-record', 'pattern'],
};

// pageId -> 中文名，从导航配置里反查，避免两处维护
const PAGE_LABELS = {};
NAV_HUBS.forEach(g => {
    (g.items || []).forEach(it => { PAGE_LABELS[it.page] = it.label; });
});
PAGE_LABELS['clinic'] = '就诊小结';

function renderRelatedLinks(page) {
    const section = document.getElementById('page-' + page);
    if (!section) return;
    let bar = section.querySelector('.related-links');
    const targets = PAGE_LINKS[page] || [];

    if (targets.length === 0) {
        if (bar) bar.remove();
        return;
    }

    if (!bar) {
        bar = document.createElement('div');
        bar.className = 'related-links';
        section.appendChild(bar);
    }

    bar.innerHTML =
        '<span class="related-links-title">相关功能</span>' +
        targets.map(p => `<button type="button" class="related-link-btn" data-goto="${p}">${escapeHtml(PAGE_LABELS[p] || p)}</button>`).join('');

    bar.querySelectorAll('.related-link-btn').forEach(btn => {
        btn.addEventListener('click', () => switchPage(btn.dataset.goto));
    });
}

// ==================== 仪表盘 ====================

// 文本赋值助手：元素不存在时静默跳过
function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = (val == null ? '' : String(val));
}

// 分钟 -> 简短时长（2h30m / 45m / 0m）
function formatHomeDuration(min) {
    min = Number(min) || 0;
    if (min <= 0) return '0m';
    const h = Math.floor(min / 60);
    const m = min % 60;
    if (h > 0 && m > 0) return h + 'h' + m + 'm';
    if (h > 0) return h + 'h';
    return m + 'm';
}

// 最近记录类型 -> 图标符号 id
const HOME_RECENT_ICONS = {
    feeding: 'icon-bottle',
    sleep: 'icon-moon',
    diaper: 'icon-diaper',
    growth: 'icon-chart',
    milestone: 'icon-heart'
};

function renderHomeRecent(records) {
    const list = document.getElementById('homeRecentList');
    if (!list) return;
    if (!records || records.length === 0) {
        list.innerHTML = '<p class="empty-tip">暂无记录</p>';
        return;
    }
    list.innerHTML = records.map(r => {
        const icon = HOME_RECENT_ICONS[r.type] || 'icon-clock';
        const title = escapeHtml(r.title || r.type_label || '记录');
        const summary = escapeHtml(r.summary || '');
        const time = escapeHtml(r.time || '');
        return '<div class="home-recent-item ' + escapeHtml(r.type || '') + '">' +
            '<span class="home-recent-icon"><svg class="ic-svg" viewBox="0 0 24 24" aria-hidden="true"><use href="#' + icon + '"/></svg></span>' +
            '<div class="home-recent-main">' +
                '<div class="home-recent-title">' + title + '</div>' +
                '<div class="home-recent-summary">' + summary + '</div>' +
            '</div>' +
            '<div class="home-recent-time">' + time + '</div>' +
        '</div>';
    }).join('');
}

function loadDashboard() {
    if (!App.currentBaby) return;

    api(`/api/babies/${App.currentBaby}/dashboard`).then(res => {
        if (!res.success) return;
        const data = res.data || {};

        // 问候语：按当前时段
        const hr = new Date().getHours();
        let greet = '早安，';
        if (hr >= 11 && hr < 13) greet = '午安，';
        else if (hr >= 13 && hr < 18) greet = '下午好，';
        else if (hr >= 18 || hr < 5) greet = '晚安，';
        setText('homeGreetingLabel', greet);

        // 宝宝档案卡
        const bp = data.baby_profile;
        if (bp) {
            const name = bp.name || '宝宝';
            setText('homeBabyName', name);
            setText('homeProfileName', name);
            setText('homeBabyInitial', (name || '宝').slice(0, 1));
            const meta = [bp.age, bp.gender, bp.weight ? bp.weight + 'kg' : ''].filter(Boolean);
            setText('homeBabyMeta', meta.join(' · ') || '--');
        }

        // 今日概览
        const feeding = data.today_feeding || {};
        const sleep = data.today_sleep || {};
        const diaper = data.today_diaper || {};
        setText('homeFeedingCount', feeding.count != null ? feeding.count : 0);
        setText('homeSleepTime', formatHomeDuration(sleep.total_minutes));
        setText('homeDiaperCount', diaper.count != null ? diaper.count : 0);

        // 最近记录
        renderHomeRecent(data.recent_records || []);
    });
}

function renderFeedingTrend(trendData) {
    const container = document.getElementById('feedingTrendChart');
    if (!trendData || trendData.length === 0) {
        container.innerHTML = '<p class="empty-tip">暂无数据</p>';
        return;
    }

    const maxValue = Math.max(...trendData.map(d => d.count), 1);
    container.innerHTML = trendData.map(d => {
        const height = Math.max((d.count / maxValue) * 80, 4);
        const dateParts = d.date.split('-');
        const label = `${parseInt(dateParts[1])}/${parseInt(dateParts[2])}`;
        return `
            <div class="trend-bar" style="height: ${height}px" title="${d.date}: ${d.count}次，共${d.total}ml">
                <span class="trend-bar-label">${label}</span>
            </div>
        `;
    }).join('');
}

// ==================== 快捷操作 ====================

function initQuickActions() {
    document.querySelectorAll('.action-btn, .home-quick-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if (!App.currentBaby) {
                showToast.warning('请先添加宝宝');
                return;
            }
            const action = btn.dataset.action;
            showRecordModal(action);
        });
    });
}

// ==================== 喂奶表单（新增 / 编辑共用同一套） ====================

// 后端 validators.VALID_FEEDING_TYPES 也是这四个，改这里记得同步
const FEEDING_TYPE_OPTIONS = [
    { value: 'breast', label: '母乳' },
    { value: 'bottle', label: '配方奶' },
    { value: 'mixed', label: '混合喂养' },
    { value: 'solid', label: '辅食' },
];

// 当前正在编辑的喂奶记录 id，null 表示新增
let editingFeedingId = null;
// 编辑态回填用的原始记录（新增时是空对象）
let feedingFormRecord = {};

/** 生成喂奶表单 HTML。传 r 就是编辑态（回填原值），不传就是新增。 */
function feedingFormHtml(r) {
    const rec = r || {};
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const defaultLocal = `${formatDate(now)}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
    const toLocal = v => (v ? String(v).trim().replace(' ', 'T').slice(0, 16) : '');
    const type = rec.feeding_type || 'breast';
    const side = rec.side || '';

    const typeOptions = FEEDING_TYPE_OPTIONS
        .map(o => `<option value="${o.value}"${type === o.value ? ' selected' : ''}>${o.label}</option>`)
        .join('');

    return `
        <form id="feedingForm">
            <div class="form-group">
                <label>喂养类型</label>
                <select id="feedingType">${typeOptions}</select>
            </div>
            <div class="form-group">
                <label>开始时间</label>
                <input type="datetime-local" id="feedingStart" value="${toLocal(rec.start_time) || defaultLocal}" required>
            </div>
            <div class="form-group">
                <label>结束时间 <small>（可选，填了能算出本次时长）</small></label>
                <input type="datetime-local" id="feedingEnd" value="${toLocal(rec.end_time)}">
            </div>
            <div class="form-group" id="amountGroup">
                <label>奶量 (ml)</label>
                <input type="number" id="feedingAmount" value="${rec.amount != null ? rec.amount : ''}" placeholder="可选">
            </div>
            <div class="form-group" id="sideGroup">
                <label>哺乳侧</label>
                <select id="feedingSide">
                    <option value="">选择</option>
                    <option value="left"${side === 'left' ? ' selected' : ''}>左侧</option>
                    <option value="right"${side === 'right' ? ' selected' : ''}>右侧</option>
                    <option value="both"${side === 'both' ? ' selected' : ''}>双侧</option>
                </select>
            </div>
            <!-- 母乳左右侧计时器 -->
            <div class="form-group side-timer-group" id="sideTimerGroup">
                <label>⏱ 左右侧计时</label>
                <div class="side-timer-container">
                    <div class="side-timer-box" id="leftTimerBox">
                        <div class="side-timer-label">左侧</div>
                        <div class="side-timer-display" id="leftTimerDisplay">00:00</div>
                        <div class="side-timer-buttons">
                            <button type="button" class="side-timer-btn start" id="leftTimerStart">开始</button>
                            <button type="button" class="side-timer-btn stop" id="leftTimerStop" style="display:none">停止</button>
                        </div>
                    </div>
                    <div class="side-timer-box" id="rightTimerBox">
                        <div class="side-timer-label">右侧</div>
                        <div class="side-timer-display" id="rightTimerDisplay">00:00</div>
                        <div class="side-timer-buttons">
                            <button type="button" class="side-timer-btn start" id="rightTimerStart">开始</button>
                            <button type="button" class="side-timer-btn stop" id="rightTimerStop" style="display:none">停止</button>
                        </div>
                    </div>
                </div>
                <div class="side-timer-total">总时长: <span id="sideTimerTotal">00:00</span></div>
            </div>
            <div class="form-group">
                <label>备注</label>
                <input type="text" id="feedingNote" value="${escapeHtml(rec.note || '')}" placeholder="可选">
            </div>
            <div class="form-actions">
                <button type="button" class="btn btn-cancel" id="cancelRecordForm">取消</button>
                <button type="submit" class="btn btn-primary">保存</button>
            </div>
        </form>
    `;
}

/** 按喂养类型显示/隐藏字段：母乳不填奶量，瓶喂/辅食不选侧和计时，混合喂养都要 */
function applyFeedingTypeVisibility(type) {
    const amountGroup = document.getElementById('amountGroup');
    const sideGroup = document.getElementById('sideGroup');
    const sideTimerGroup = document.getElementById('sideTimerGroup');
    const showSide = (type === 'breast' || type === 'mixed');
    if (amountGroup) amountGroup.style.display = (type === 'breast') ? 'none' : 'block';
    if (sideGroup) sideGroup.style.display = showSide ? 'block' : 'none';
    if (sideTimerGroup) sideTimerGroup.style.display = showSide ? 'block' : 'none';
}

/** 初始化喂奶表单：绑事件、按类型显隐、回填计时器秒数 */
function setupFeedingForm(rec) {
    initSideTimers();
    // 编辑时把已有计时值填回去，否则一进编辑界面就「归零」，保存后时长被抹掉
    leftTimerSeconds = Number(rec.left_duration) || 0;
    rightTimerSeconds = Number(rec.right_duration) || 0;
    updateSideTimerDisplay('left', leftTimerSeconds);
    updateSideTimerDisplay('right', rightTimerSeconds);
    updateSideTimerTotal();

    const typeSelect = document.getElementById('feedingType');
    applyFeedingTypeVisibility(typeSelect ? typeSelect.value : 'breast');
    typeSelect?.addEventListener('change', function () {
        applyFeedingTypeVisibility(this.value);
    });

    document.getElementById('feedingForm')?.addEventListener('submit', function (e) {
        e.preventDefault();
        submitFeedingForm();
    });
}

/** 收集表单数据。时间统一成 'YYYY-MM-DD HH:MM:SS'，后端也只认这一种格式。 */
function collectFeedingForm() {
    const startEl = document.getElementById('feedingStart');
    const start = startEl?.value || '';
    if (!start) {
        showToast.error('请填写开始时间');
        return null;
    }
    const end = document.getElementById('feedingEnd')?.value || '';
    const amountRaw = document.getElementById('feedingAmount')?.value;

    return {
        start_time: start.replace('T', ' ') + ':00',
        end_time: end ? end.replace('T', ' ') + ':00' : null,
        feeding_type: document.getElementById('feedingType')?.value || '',
        amount: (amountRaw === '' || amountRaw == null) ? null : Number(amountRaw),
        side: document.getElementById('feedingSide')?.value || null,
        note: document.getElementById('feedingNote')?.value || '',
        left_duration: leftTimerSeconds > 0 ? leftTimerSeconds : null,
        right_duration: rightTimerSeconds > 0 ? rightTimerSeconds : null,
    };
}

function submitFeedingForm() {
    if (!App.currentBaby) return;
    const payload = collectFeedingForm();
    if (!payload) return;

    const isEdit = !!editingFeedingId;
    const url = isEdit
        ? `/api/babies/${App.currentBaby}/feeding/${editingFeedingId}`
        : `/api/babies/${App.currentBaby}/feeding`;

    api(url, {
        method: isEdit ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
    }).then(res => {
        if (res.success) {
            hideModal('recordModal');
            showToast.success(isEdit ? '已更新' : '记录已添加');
            resetSideTimers();
            editingFeedingId = null;
            loadFeedingPage();
            loadTimeline();
            loadDashboard();
        } else {
            showToast.error(res.message || '保存失败');
        }
    });
}

function showRecordModal(type) {
    const titles = {
        feeding: '喂奶记录',
        sleep: '睡眠记录',
        diaper: '换尿布记录',
        growth: '成长记录'
    };
    document.getElementById('recordModalTitle').textContent = titles[type] || '添加记录';

    if (type === 'growth') {
        showModal('growthModal');
        document.getElementById('growthDate').value = getToday();
        return;
    }

    const body = document.getElementById('recordModalBody');
    const now = new Date();
    const dateTimeLocal = `${formatDate(now)}T${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;

    let html = '';
    if (type === 'feeding') {
        editingFeedingId = null;
        feedingFormRecord = {};
        html = feedingFormHtml(null);
    } else if (type === 'sleep') {
        html = `
            <form id="sleepForm">
                <div class="form-group">
                    <label>开始时间</label>
                    <input type="datetime-local" id="sleepStart" value="${dateTimeLocal}" required>
                </div>
                <div class="form-group">
                    <label>结束时间</label>
                    <input type="datetime-local" id="sleepEnd" placeholder="可选，留空表示还在睡">
                </div>
                <div class="form-group">
                    <label>睡眠质量</label>
                    <select id="sleepQuality">
                        <option value="">选择</option>
                        <option value="good">好</option>
                        <option value="normal">一般</option>
                        <option value="poor">差</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>备注</label>
                    <input type="text" id="sleepNote" placeholder="可选">
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-cancel" id="cancelRecordForm">取消</button>
                    <button type="submit" class="btn btn-primary">保存</button>
                </div>
            </form>
        `;
    } else if (type === 'diaper') {
        html = `
            <form id="diaperForm">
                <div class="form-group">
                    <label>时间</label>
                    <input type="datetime-local" id="diaperTime" value="${dateTimeLocal}" required>
                </div>
                <div class="form-group">
                    <label>类型</label>
                    <select id="diaperType">
                        <option value="wet">💧 小便</option>
                        <option value="dirty">💩 大便</option>
                        <option value="both">💧💩 都有</option>
                        <option value="dry">✅ 干爽</option>
                    </select>
                </div>
                <div class="form-group" id="diaperColorGroup" style="display:none">
                    <label>大便颜色</label>
                    <select id="diaperColor">
                        <option value="">选择颜色</option>
                        <option value="black">黑色（胎便）</option>
                        <option value="brown">棕色（正常）</option>
                        <option value="green">绿色</option>
                        <option value="yellow">黄色</option>
                        <option value="other">其他</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>皮肤状况</label>
                    <select id="diaperSkin">
                        <option value="">正常</option>
                        <option value="normal">😊 正常</option>
                        <option value="slight_red">😐 轻微发红</option>
                        <option value="rash">😟 红臀</option>
                        <option value="severe_rash">😢 严重红臀</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>备注</label>
                    <input type="text" id="diaperNote" placeholder="可选">
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-cancel" id="cancelRecordForm">取消</button>
                    <button type="submit" class="btn btn-primary">保存</button>
                </div>
            </form>
        `;
    }

    body.innerHTML = html;
    showModal('recordModal');

    // 绑定表单提交事件
    bindRecordFormEvents(type);
}

function bindRecordFormEvents(type) {
    document.getElementById('cancelRecordForm')?.addEventListener('click', () => {
        if (type === 'feeding') {
            resetSideTimers();
            editingFeedingId = null;
            feedingFormRecord = {};
        }
        hideModal('recordModal');
    });

    if (type === 'feeding') {
        // 新增/编辑走同一套表单：setupFeedingForm 负责计时器回填、类型显隐和提交
        setupFeedingForm(feedingFormRecord);
    } else if (type === 'sleep') {
        document.getElementById('sleepForm')?.addEventListener('submit', function(e) {
            e.preventDefault();
            const data = {
                baby_id: App.currentBaby,
                start_time: document.getElementById('sleepStart')?.value || '',
                end_time: document.getElementById('sleepEnd')?.value || null,
                sleep_quality: document.getElementById('sleepQuality')?.value || null,
                note: document.getElementById('sleepNote')?.value || ''
            };

            api(`/api/babies/${App.currentBaby}/sleep`, {
                method: 'POST',
                body: JSON.stringify(data)
            }).then(res => {
                if (res.success) {
                    hideModal('recordModal');
                    showToast.success('记录已添加');
                    loadDashboard();
                } else {
                    showToast.error(res.message || '添加失败');
                }
            });
        });
    } else if (type === 'diaper') {
        // 根据换尿布类型显示/隐藏颜色选择
        document.getElementById('diaperType')?.addEventListener('change', function() {
            const colorGroup = document.getElementById('diaperColorGroup');
            if (this.value === 'dirty' || this.value === 'both') {
                colorGroup.style.display = 'block';
            } else {
                colorGroup.style.display = 'none';
            }
        });

        document.getElementById('diaperForm')?.addEventListener('submit', function(e) {
            e.preventDefault();
            const diaperType = document.getElementById('diaperType')?.value || '';
            const skinCondition = document.getElementById('diaperSkin')?.value || null;
            const data = {
                baby_id: App.currentBaby,
                change_time: document.getElementById('diaperTime')?.value || '',
                diaper_type: diaperType,
                color: (diaperType === 'dirty' || diaperType === 'both') ? document.getElementById('diaperColor')?.value || null : null,
                skin_condition: skinCondition || null,
                note: document.getElementById('diaperNote')?.value || ''
            };

            api(`/api/babies/${App.currentBaby}/diaper`, {
                method: 'POST',
                body: JSON.stringify(data)
            }).then(res => {
                if (res.success) {
                    hideModal('recordModal');
                    showToast.success('记录已添加');
                    loadDashboard();
                    loadDiaperPage();
                } else {
                    showToast.error(res.message || '添加失败');
                }
            });
        });
    }
}

// ==================== 成长记录 ====================

function initGrowthModal() {
    document.getElementById('closeGrowthModal').addEventListener('click', () => hideModal('growthModal'));
    document.getElementById('cancelGrowthForm').addEventListener('click', () => hideModal('growthModal'));

    // BMI 自动计算
    const heightInput = document.getElementById('growthHeight');
    const weightInput = document.getElementById('growthWeight');
    const bmiInput = document.getElementById('growthBmi');

    function autoCalcBmi() {
        const h = parseFloat(heightInput.value);
        const w = parseFloat(weightInput.value);
        if (h > 0 && w > 0) {
            const bmi = (w / ((h / 100) ** 2)).toFixed(1);
            bmiInput.value = bmi;
        }
    }

    heightInput.addEventListener('input', autoCalcBmi);
    weightInput.addEventListener('input', autoCalcBmi);

    document.getElementById('growthForm').addEventListener('submit', function(e) {
        e.preventDefault();
        const data = {
            baby_id: App.currentBaby,
            record_date: document.getElementById('growthDate').value,
            height: document.getElementById('growthHeight').value || null,
            weight: document.getElementById('growthWeight').value || null,
            bmi: document.getElementById('growthBmi').value || null,
            head_circumference: document.getElementById('growthHead').value || null,
            note: document.getElementById('growthNote').value
        };

        api(`/api/babies/${App.currentBaby}/growth`, {
            method: 'POST',
            body: JSON.stringify(data)
        }).then(res => {
            if (res.success) {
                hideModal('growthModal');
                showToast.success('成长记录已添加');
                this.reset();
                loadGrowthPage();
                // 如果当前在健康档案的生长曲线Tab，也刷新图表
                if (document.getElementById('tab-growth') && document.getElementById('tab-growth').classList.contains('active')) {
                    const activeMetric = document.querySelector('.growth-metric-btn.active');
                    if (activeMetric) loadGrowthChart(activeMetric.dataset.metric);
                }
            } else {
                showToast.error(res.message || '添加失败');
            }
        });
    });
}

// ==================== 各记录页面初始化 ====================

function initRecordPages() {
    // 吸奶计时器（左右独立、双侧可同时）
    initPumpSideTimers();

    // 吸奶弹窗关闭
    const closePumpingModal = document.getElementById('closePumpingModal');
    if (closePumpingModal) {
        closePumpingModal.addEventListener('click', () => hideModal('pumpingModal'));
    }
    const cancelPumpingForm = document.getElementById('cancelPumpingForm');
    if (cancelPumpingForm) {
        cancelPumpingForm.addEventListener('click', () => hideModal('pumpingModal'));
    }

    // 吸奶表单提交
    const pumpingForm = document.getElementById('pumpingForm');
    if (pumpingForm) {
        pumpingForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const mode = document.getElementById('pumpMode').value;
            const leftAmount = document.getElementById('pumpLeftAmount').value;
            const rightAmount = document.getElementById('pumpRightAmount').value;
            let totalAmount = document.getElementById('pumpTotalAmount').value;
            if (!totalAmount && (leftAmount || rightAmount)) {
                totalAmount = (parseFloat(leftAmount) || 0) + (parseFloat(rightAmount) || 0);
            }

            // 根据模式计算时长
            let durationMin = null;
            let leftDuration = null;
            let rightDuration = null;

            if (mode === 'left') {
                durationMin = Math.ceil(pumpSeconds / 60) || null;
                leftDuration = pumpSeconds > 0 ? pumpSeconds : null;
            } else if (mode === 'right') {
                durationMin = Math.ceil(pumpSeconds / 60) || null;
                rightDuration = pumpSeconds > 0 ? pumpSeconds : null;
            } else if (mode === 'separate') {
                const leftMin = Math.ceil(pumpLeftSeconds / 60);
                const rightMin = Math.ceil(pumpRightSeconds / 60);
                durationMin = leftMin || rightMin || null;
                leftDuration = pumpLeftSeconds > 0 ? pumpLeftSeconds : null;
                rightDuration = pumpRightSeconds > 0 ? pumpRightSeconds : null;
            } else if (mode === 'simultaneous') {
                durationMin = Math.ceil(simSeconds / 60) || null;
                // 双侧同时：如果某边提前停止，记录实际经过时间（这里简化处理，都用总时长）
                leftDuration = simSeconds > 0 ? simSeconds : null;
                rightDuration = simSeconds > 0 ? simSeconds : null;
            }

            const data = {
                baby_id: App.currentBaby,
                pump_time: document.getElementById('pumpTime').value,
                pump_type: mode === 'simultaneous' ? 'double' : 'electric',
                side: mode === 'left' ? 'left' : mode === 'right' ? 'right' : 'both',
                duration_minutes: durationMin,
                left_amount: leftAmount || null,
                right_amount: rightAmount || null,
                total_amount: totalAmount || null,
                left_duration: leftDuration,
                right_duration: rightDuration,
                note: document.getElementById('pumpNote').value
            };

            api(`/api/babies/${App.currentBaby}/pumping`, {
                method: 'POST',
                body: JSON.stringify(data)
            }).then(res => {
                if (res.success) {
                    hideModal('pumpingModal');
                    showToast.success('记录已添加');
                    loadPumpingPage();
                    loadDashboard();
                    this.reset();
                } else {
                    showToast.error(res.message || '添加失败');
                }
            });
        });
    }
}

function showPumpingModal() {
    if (!App.currentBaby) return;
    // 设置默认时间为当前时间
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    document.getElementById('pumpTime').value = `${year}-${month}-${day}T${hours}:${minutes}`;
    resetPumpSideTimers();
    // 初始化UI状态
    const modeSelect = document.getElementById('pumpMode');
    if (modeSelect) modeSelect.value = 'left';
    // 触发模式切换
    onPumpModeChange();
    showModal('pumpingModal');
}

// ==================== 喂奶记录页面 ====================
// 日期范围：1=今日 3=近3天 7=近7天 0=全部
let feedingRange = 1;

function loadFeedingPage() {
    if (!App.currentBaby) return;
    const container = document.getElementById('feedingList');
    if (!container) return;

    const params = new URLSearchParams();
    if (feedingRange > 0) {
        const start = new Date();
        start.setDate(start.getDate() - (feedingRange - 1));
        params.set('start', formatDate(start));
    }
    params.set('limit', '200');

    api(`/api/babies/${App.currentBaby}/feeding?${params.toString()}`).then(res => {
        if (!res.success) return;
        renderFeedingList(res.data || []);
    });

    loadFeedingStats();
    bindFeedingPageControls();
}

/** 顶部统计栏：今日次数 / 瓶喂总量 / 亲喂总时长 / 距上次 */
function loadFeedingStats() {
    api(`/api/babies/${App.currentBaby}/feeding/stats`).then(res => {
        if (!res.success) return;
        const d = res.data || {};
        const total = d.today_total || {};
        setText('feedingTodayCount', total.count || 0);
        setText('feedingTodayAmount', Math.round(total.amount || 0));
        setText('feedingTodayBreast', Math.round((total.breast_seconds || 0) / 60));

        const lastEl = document.getElementById('feedingLastTime');
        if (lastEl) {
            lastEl.textContent = (d.minutes_since_last == null) ? '--' : formatAgoCN(d.minutes_since_last);
        }
        renderFeedingReminder(d);
    });
}

/** 距上次喂奶提醒条：超过设定小时数就变红提示 */
function renderFeedingReminder(stats) {
    const box = document.getElementById('feedingReminder');
    const textEl = document.getElementById('feedingReminderText');
    if (!box || !textEl) return;

    const minutes = stats.minutes_since_last;
    if (minutes == null) {
        box.style.display = 'none';
        return;
    }

    const hours = getFeedingReminderHours();
    const threshold = hours * 60;
    const overdue = minutes >= threshold;
    box.style.display = 'flex';
    box.classList.toggle('overdue', overdue);

    if (overdue) {
        textEl.textContent = `距离上次喂奶已经 ${formatAgoCN(minutes)}，超过 ${hours} 小时了，该喂啦`;
    } else {
        const left = Math.max(0, Math.round(threshold - minutes));
        textEl.textContent = `距离上次喂奶 ${formatAgoCN(minutes)}，还有约 ${left} 分钟到 ${hours} 小时`;
    }
}

/** 提醒间隔（小时）：优先用「通知设置」里保存的值，没有就用 3 小时 */
function getFeedingReminderHours() {
    const saved = parseFloat(localStorage.getItem('feedingReminderHours'));
    return (isFinite(saved) && saved > 0) ? saved : 3;
}

/** 列表按天分组，每天带一行小计 */
function renderFeedingList(records) {
    const container = document.getElementById('feedingList');
    if (!container) return;

    if (!records.length) {
        container.innerHTML = '<p class="empty-tip">暂无喂奶记录<br><small>点击上方按钮或快捷按钮添加记录</small></p>';
        return;
    }

    const groups = new Map();
    records.forEach(r => {
        const day = (r.start_time || '').slice(0, 10) || '未知日期';
        if (!groups.has(day)) groups.set(day, []);
        groups.get(day).push(r);
    });

    const today = getToday();
    const yesterday = formatDate(new Date(Date.now() - 86400000));
    let html = '';
    groups.forEach((items, day) => {
        const title = day === today ? '今天' : (day === yesterday ? '昨天' : day);
        const amount = items.reduce((s, r) => s + (Number(r.amount) || 0), 0);
        const seconds = items.reduce(
            (s, r) => s + (Number(r.left_duration) || 0) + (Number(r.right_duration) || 0), 0);
        let sum = `${items.length} 次`;
        if (amount) sum += ` · ${Math.round(amount)}ml`;
        if (seconds) sum += ` · 亲喂 ${Math.round(seconds / 60)} 分`;

        html += `<div class="care-day-group">
            <div class="care-day-header">
                <span class="care-day-title">${escapeHtml(title)}</span>
                <span class="care-day-sum">${sum}</span>
            </div>
            ${items.map(renderFeedingItems).join('')}
        </div>`;
    });
    container.innerHTML = html;
}

/** 页面上的按钮只绑一次（loadFeedingPage 会被反复调用） */
function bindFeedingPageControls() {
    const btn = document.getElementById('addFeedingBtnPage');
    if (btn && !btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', () => showRecordModal('feeding'));
    }

    const quickActions = document.getElementById('feedingQuickActions');
    if (quickActions && !quickActions._bound) {
        quickActions._bound = true;
        quickActions.addEventListener('click', (e) => {
            const btn = e.target.closest('.quick-btn');
            if (!btn) return;
            quickAddFeeding(btn.dataset.type, parseInt(btn.dataset.amount) || 0);
        });
    }

    const tabs = document.getElementById('feedingRangeTabs');
    if (tabs && !tabs._bound) {
        tabs._bound = true;
        tabs.addEventListener('click', (e) => {
            const btn = e.target.closest('.tab-btn');
            if (!btn) return;
            tabs.querySelectorAll('.tab-btn').forEach(x => x.classList.remove('active'));
            btn.classList.add('active');
            feedingRange = parseInt(btn.dataset.range) || 0;
            loadFeedingPage();
        });
    }

    const remindBtn = document.getElementById('feedingReminderBtn');
    if (remindBtn && !remindBtn._bound) {
        remindBtn._bound = true;
        remindBtn.addEventListener('click', () => showRecordModal('feeding'));
    }
}

// 快捷添加喂奶记录
function quickAddFeeding(type, amount) {
    if (!App.currentBaby) return;
    const data = {
        baby_id: App.currentBaby,
        feeding_type: type,
        start_time: nowLocalStr(),
    };
    if (type === 'breast') {
        // 母乳不记 ml，写 0 会在库里留脏值，也会让「总量」统计失真
        data.side = 'both';
    } else if (amount > 0) {
        data.amount = amount;
    }
    api('/api/babies/' + App.currentBaby + '/feeding', {
        method: 'POST',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            showToast.success('已添加喂奶记录');
            loadFeedingPage();
            loadTimeline();
            loadDashboard(); // 同步更新首页
        } else {
            showToast.error(res.message || '添加失败');
        }
    });
}

// ==================== 吸奶记录页面 ====================
function loadPumpingPage() {
    if (!App.currentBaby) return;
    const container = document.getElementById('pumpingList');
    if (!container) return;

    api(`/api/babies/${App.currentBaby}/pumping`).then(res => {
        if (!res.success) return;

        // 更新统计栏
        const today = getToday();
        const todayRecords = (res.data || []).filter(r => r.pump_time && r.pump_time.startsWith(today));
        const todayAmount = todayRecords.reduce((sum, r) => sum + (r.total_amount || 0), 0);
        const countEl = document.getElementById('pumpingTodayCount');
        const amountEl = document.getElementById('pumpingTodayAmount');
        if (countEl) countEl.textContent = todayRecords.length;
        if (amountEl) amountEl.textContent = todayAmount;

        if (!res.data || res.data.length === 0) {
            container.innerHTML = '<p class="empty-tip">暂无吸奶记录</p>';
            return;
        }
        container.innerHTML = res.data.map(renderPumpingItems).join('');
    });

    // 绑定添加按钮事件
    const btn = document.getElementById('addPumpingBtnPage');
    if (btn && !btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', () => showPumpingModal());
    }
}

// ==================== 睡眠记录页面 ====================
/** 睡眠列表的日期筛选：1=今日, 3/7=近N天, 0=全部 */
let sleepRange = 1;

function loadSleepPage() {
    if (!App.currentBaby) return;
    const container = document.getElementById('sleepList');
    if (!container) return;

    const params = new URLSearchParams();
    if (sleepRange > 0) {
        const start = new Date();
        start.setDate(start.getDate() - (sleepRange - 1));
        params.set('start', formatDate(start));
    }
    params.set('limit', '200');

    api(`/api/babies/${App.currentBaby}/sleep?${params.toString()}`).then(res => {
        if (!res.success) return;
        renderSleepList(res.data || []);
    });

    loadSleepStats();
    loadSleepWeeklySummary();
    loadSleepReference();
    bindSleepPageControls();
}

/** 顶部统计栏：今日次数 / 总时长 / 夜间 / 小憩 / 距上次（走 /stats，前端不再全量自己算） */
function loadSleepStats() {
    api(`/api/babies/${App.currentBaby}/sleep/stats`).then(res => {
        if (!res.success) return;
        const d = res.data || {};
        const t = d.today || {};
        setText('sleepTodayCount', t.count || 0);
        setText('sleepTodayDuration', ((t.total_minutes || 0) / 60).toFixed(1));
        setText('sleepTodayNight', ((t.night_minutes || 0) / 60).toFixed(1));
        setText('sleepTodayNap', Math.round(t.nap_minutes || 0));

        const lastEl = document.getElementById('sleepLastTime');
        if (lastEl) {
            const mins = d.minutes_since_last;
            // 负数说明记了未来的时间，显示成 '--' 而不是「-3小时前」
            lastEl.textContent = (mins == null || mins < 0) ? '--' : formatAgoCN(mins);
        }
    });
}

/** 本周概览（平均睡眠 / 入睡时间 / 次数 / 质量评分） */
function loadSleepWeeklySummary() {
    api(`/api/babies/${App.currentBaby}/sleep-analysis?days=7`).then(res => {
        if (!res.success) return;
        const data = res.data || {};
        const summaryEl = document.getElementById('weeklySummaryStats');
        if (!summaryEl) return;

        const avg = data.averages || {};
        const avgDuration = avg.avg_duration || 0;
        const avgBedtime = data.avg_bedtime || '--';
        const totalSessions = avg.total_sessions || 0;
        const qualityDist = data.quality_distribution || {};
        const totalQuality = (qualityDist.good || 0) + (qualityDist.normal || 0) + (qualityDist.poor || 0);
        let qualityScore = '--';
        if (totalQuality > 0) {
            const score = ((qualityDist.good || 0) * 100 + (qualityDist.normal || 0) * 70 + (qualityDist.poor || 0) * 40) / totalQuality;
            qualityScore = Math.round(score) + '分';
        }
        summaryEl.innerHTML = `
            <div class="weekly-stat-item"><span class="weekly-stat-label">平均睡眠</span><strong>${formatMinutes(avgDuration)}</strong></div>
            <div class="weekly-stat-item"><span class="weekly-stat-label">入睡时间</span><strong>${escapeHtml(String(avgBedtime))}</strong></div>
            <div class="weekly-stat-item"><span class="weekly-stat-label">睡眠次数</span><strong>${totalSessions}次</strong></div>
            <div class="weekly-stat-item"><span class="weekly-stat-label">质量评分</span><strong>${qualityScore}</strong></div>
        `;
    });
}

/** 列表按天分组，每天带一行小计（今日/昨日/日期） */
function renderSleepList(records) {
    const container = document.getElementById('sleepList');
    if (!container) return;

    if (!records.length) {
        container.innerHTML = '<p class="empty-tip">暂无睡眠记录<br><small>点击上方按钮或快捷按钮添加记录</small></p>';
        return;
    }

    const groups = new Map();
    records.forEach(r => {
        const day = (r.start_time || '').slice(0, 10) || '未知日期';
        if (!groups.has(day)) groups.set(day, []);
        groups.get(day).push(r);
    });

    const today = getToday();
    const yesterday = formatDate(new Date(Date.now() - 86400000));
    let html = '';
    groups.forEach((items, day) => {
        const title = day === today ? '今天' : (day === yesterday ? '昨天' : day);
        const total = items.reduce((s, r) => s + (Number(r.duration_minutes) || 0), 0);
        const nap = items.filter(r => r.is_nap === 1).reduce((s, r) => s + (Number(r.duration_minutes) || 0), 0);
        let sum = `${items.length} 次`;
        if (total) sum += ` · 共 ${formatMinutesCN(total)}`;
        if (nap) sum += ` · 小憩 ${formatMinutesCN(nap)}`;

        html += `<div class="care-day-group">
            <div class="care-day-header">
                <span class="care-day-title">${escapeHtml(title)}</span>
                <span class="care-day-sum">${sum}</span>
            </div>
            ${items.map(renderSleepItems).join('')}
        </div>`;
    });
    container.innerHTML = html;
}

/** 页面上的按钮只绑一次（loadSleepPage 会被反复调用） */
function bindSleepPageControls() {
    const btn = document.getElementById('addSleepBtnPage');
    if (btn && !btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', () => showRecordModal('sleep'));
    }

    const timerBtn = document.getElementById('sleepTimerBtn');
    if (timerBtn && !timerBtn._bound) {
        timerBtn._bound = true;
        timerBtn.addEventListener('click', toggleSleepTimer);
    }

    // 这两个也必须只绑一次：loadSleepPage 每次进页面都会跑，
    // 重复绑会让「结束睡眠」点一次触发两遍，写出两条记录
    const stopBtn = document.getElementById('sleepTimerStopBtn');
    if (stopBtn && !stopBtn._bound) {
        stopBtn._bound = true;
        stopBtn.addEventListener('click', stopSleepTimer);
    }
    const cancelBtn = document.getElementById('sleepTimerCancelBtn');
    if (cancelBtn && !cancelBtn._bound) {
        cancelBtn._bound = true;
        cancelBtn.addEventListener('click', cancelSleepTimer);
    }

    const quickActions = document.getElementById('sleepQuickActions');
    if (quickActions && !quickActions._bound) {
        quickActions._bound = true;
        quickActions.addEventListener('click', (e) => {
            const btn = e.target.closest('.quick-btn');
            if (!btn) return;
            quickAddSleep(parseInt(btn.dataset.duration) || 0, parseInt(btn.dataset.nap) || 0);
        });
    }

    const tabs = document.getElementById('sleepRangeTabs');
    if (tabs && !tabs._bound) {
        tabs._bound = true;
        tabs.addEventListener('click', (e) => {
            const btn = e.target.closest('.tab-btn');
            if (!btn) return;
            tabs.querySelectorAll('.tab-btn').forEach(x => x.classList.remove('active'));
            btn.classList.add('active');
            sleepRange = parseInt(btn.dataset.range) || 0;
            loadSleepPage();
        });
    }
}

// ==================== 睡眠计时器 ====================
let sleepTimerInterval = null;

function toggleSleepTimer() {
    const card = document.getElementById('sleepTimerCard');
    if (card && card.style.display !== 'none') {
        // 已经在运行，不做任何事
        return;
    }
    startSleepTimer();
}

function startSleepTimer() {
    const card = document.getElementById('sleepTimerCard');
    const display = document.getElementById('sleepTimerDisplay');
    const status = document.getElementById('sleepTimerStatus');
    const startInput = document.getElementById('sleepTimerStartTime');

    if (!card || !display) return;

    const now = new Date();
    card.style.display = 'block';
    if (status) status.textContent = '正在睡眠';
    if (startInput) startInput.value = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

    // 滚动到计时器
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });

    // 开始计时
    const startTime = now.getTime();
    sleepTimerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        const h = Math.floor(elapsed / 3600);
        const m = Math.floor((elapsed % 3600) / 60);
        const s = elapsed % 60;
        display.textContent = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }, 1000);
}

function stopSleepTimer() {
    if (sleepTimerInterval) {
        clearInterval(sleepTimerInterval);
        sleepTimerInterval = null;
    }

    const startInput = document.getElementById('sleepTimerStartTime');
    const startTime = startInput?.value;

    if (!startTime) {
        cancelSleepTimer();
        return;
    }

    const data = {
        baby_id: App.currentBaby,
        start_time: startTime.replace('T', ' ').slice(0, 19),
        end_time: nowLocalStr(),
        is_nap: 0,
        sleep_quality: null,
        note: '计时器记录'
    };

    api(`/api/babies/${App.currentBaby}/sleep`, {
        method: 'POST',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            showToast.success('睡眠记录已保存');
            document.getElementById('sleepTimerCard').style.display = 'none';
            document.getElementById('sleepTimerDisplay').textContent = '00:00:00';
            loadSleepPage();
            loadTimeline();
            loadDashboard();
        } else {
            showToast.error(res.message || '保存失败');
        }
    });
}

function cancelSleepTimer() {
    if (sleepTimerInterval) {
        clearInterval(sleepTimerInterval);
        sleepTimerInterval = null;
    }
    const card = document.getElementById('sleepTimerCard');
    if (card) card.style.display = 'none';
    const display = document.getElementById('sleepTimerDisplay');
    if (display) display.textContent = '00:00:00';
}

// ==================== 月龄睡眠参考 ====================
const SLEEP_REFERENCE = [
    { maxMonths: 0, label: '新生儿', total: '15-18小时', night: '8-9小时', nap: '7-9小时', note: '每次2-4小时' },
    { maxMonths: 3, label: '1-3月龄', total: '14-17小时', night: '9-10小时', nap: '4-5小时', note: '4-6次小睡' },
    { maxMonths: 6, label: '4-6月龄', total: '12-16小时', night: '10-11小时', nap: '3-4小时', note: '3次小睡' },
    { maxMonths: 12, label: '7-12月龄', total: '11-15小时', night: '10-12小时', nap: '2-3小时', note: '2次小睡' },
    { maxMonths: 24, label: '1-2岁', total: '11-14小时', night: '10-12小时', nap: '1-3小时', note: '1-2次小睡' },
    { maxMonths: 36, label: '2-3岁', total: '10-13小时', night: '10-12小时', nap: '0-2小时', note: '1次小睡' },
];

function loadSleepReference() {
    const content = document.getElementById('sleepReferenceContent');
    if (!content || !App.currentBaby) return;

    // 获取宝宝信息
    api(`/api/babies/${App.currentBaby}`).then(res => {
        if (!res.success || !res.data || !res.data.birthday) {
            content.innerHTML = '<span>请设置宝宝生日后查看参考</span>';
            return;
        }
        const birthday = new Date(res.data.birthday);
        const now = new Date();
        const ageMonths = (now - birthday) / (1000 * 60 * 60 * 24 * 30.44);

        const ref = SLEEP_REFERENCE.find(r => ageMonths <= r.maxMonths) || SLEEP_REFERENCE[SLEEP_REFERENCE.length - 1];

        content.innerHTML = `
            <div class="sleep-ref-row"><span class="sleep-ref-label">月龄段</span><span class="sleep-ref-value">${ref.label}</span></div>
            <div class="sleep-ref-row"><span class="sleep-ref-label">推荐总量</span><span class="sleep-ref-value">${ref.total}</span></div>
            <div class="sleep-ref-row"><span class="sleep-ref-label">夜间睡眠</span><span class="sleep-ref-value">${ref.night}</span></div>
            <div class="sleep-ref-row"><span class="sleep-ref-label">白天小睡</span><span class="sleep-ref-value">${ref.nap}</span></div>
            <div class="sleep-ref-note">${ref.note}</div>
        `;
    }).catch(() => {
        content.innerHTML = '<span>请设置宝宝生日后查看参考</span>';
    });
}

// 快捷添加睡眠记录
function quickAddSleep(duration, isNap) {
    if (!App.currentBaby) return;
    const now = new Date();
    const startTime = new Date(now.getTime() - duration * 60000);
    const data = {
        baby_id: App.currentBaby,
        start_time: toLocalStr(startTime),
        end_time: toLocalStr(now),
        duration_minutes: duration,
        is_nap: isNap,
    };
    api('/api/babies/' + App.currentBaby + '/sleep', {
        method: 'POST',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            showToast.success('已添加睡眠记录');
            loadSleepPage();
            loadTimeline();
            loadDashboard(); // 同步更新首页
        } else {
            showToast.error(res.message || '添加失败');
        }
    });
}

// ==================== 换尿布记录页面 ====================
function loadDiaperPage() {
    if (!App.currentBaby) return;
    const container = document.getElementById('diaperList');
    if (!container) return;

    api(`/api/babies/${App.currentBaby}/diaper`).then(res => {
        if (!res.success) return;

        // 更新统计栏
        const today = getToday();
        const todayRecords = (res.data || []).filter(r => r.change_time && r.change_time.startsWith(today));
        const wetCount = todayRecords.filter(r => r.diaper_type === 'wet' || r.diaper_type === 'both').length;
        const dirtyCount = todayRecords.filter(r => r.diaper_type === 'dirty' || r.diaper_type === 'both').length;
        const countEl = document.getElementById('diaperTodayCount');
        const wetEl = document.getElementById('diaperTodayWet');
        const dirtyEl = document.getElementById('diaperTodayDirty');
        const lastEl = document.getElementById('diaperLastTime');
        if (countEl) countEl.textContent = todayRecords.length;
        if (wetEl) wetEl.textContent = wetCount;
        if (dirtyEl) dirtyEl.textContent = dirtyCount;

        // 计算距离上次换尿布时间
        if (lastEl && res.data && res.data.length > 0) {
            const lastRecord = res.data[0];
            if (lastRecord.change_time) {
                const lastTime = new Date(lastRecord.change_time);
                const now = new Date();
                const diffMs = now - lastTime;
                const diffMin = Math.floor(diffMs / 60000);
                if (diffMin < 60) {
                    lastEl.textContent = `${diffMin}分钟前`;
                } else if (diffMin < 1440) {
                    lastEl.textContent = `${Math.floor(diffMin / 60)}小时前`;
                } else {
                    lastEl.textContent = `${Math.floor(diffMin / 1440)}天前`;
                }
            }
        } else if (lastEl) {
            lastEl.textContent = '--';
        }

        if (!res.data || res.data.length === 0) {
            container.innerHTML = '<p class="empty-tip">暂无换尿布记录<br><small>点击快捷按钮快速记录</small></p>';
            return;
        }
        container.innerHTML = res.data.map(renderDiaperItems).join('');
    });

    // 绑定添加按钮事件
    const btn = document.getElementById('addDiaperBtnPage');
    if (btn && !btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', () => showRecordModal('diaper'));
    }

    // 绑定快捷操作按钮
    const quickActions = document.getElementById('diaperQuickActions');
    if (quickActions && !quickActions._bound) {
        quickActions._bound = true;
        quickActions.addEventListener('click', (e) => {
            const btn = e.target.closest('.quick-btn');
            if (!btn) return;
            const type = btn.dataset.type;
            quickAddDiaper(type);
        });
    }

    // 绑定皮肤状况按钮
    const skinBar = document.getElementById('skinConditionBar');
    if (skinBar && !skinBar._bound) {
        skinBar._bound = true;
        skinBar.addEventListener('click', (e) => {
            const btn = e.target.closest('.skin-btn');
            if (!btn) return;
            const skin = btn.dataset.skin;
            quickAddDiaperWithSkin(skin);
        });
    }

    // 绑定报表按钮
    const reportBtn = document.getElementById('diaperReportBtn');
    if (reportBtn && !reportBtn._bound) {
        reportBtn._bound = true;
        reportBtn.addEventListener('click', showDiaperReportModal);
    }
}

// 快捷添加换尿布记录（带皮肤状况）
function quickAddDiaperWithSkin(skinCondition) {
    if (!App.currentBaby) return;
    const data = {
        baby_id: App.currentBaby,
        diaper_type: 'wet',
        change_time: nowLocalStr(),
        skin_condition: skinCondition,
    };
    api('/api/babies/' + App.currentBaby + '/diaper', {
        method: 'POST',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            showToast.success('已记录皮肤状况');
            loadDiaperPage();
            loadDashboard();
        } else {
            showToast.error(res.message || '添加失败');
        }
    });
}

// 显示换尿布报表弹窗
function showDiaperReportModal() {
    if (!App.currentBaby) {
        showToast('请先选择宝宝');
        return;
    }

    api(`/api/babies/${App.currentBaby}/diaper/report`).then(res => {
        if (!res.success) return;
        const data = res.data;

        const colorNames = { black: '黑色', brown: '棕色', green: '绿色', yellow: '黄色', other: '其他' };
        const colorDistHtml = Object.entries(data.color_distribution).map(([color, count]) =>
            `<span class="color-item">${colorNames[color] || color}: ${count}次</span>`
        ).join('') || '<span class="empty-tip">暂无数据</span>';

        const dailyChartHtml = data.daily_stats.slice(-7).map(d => {
            const maxTotal = Math.max(...data.daily_stats.map(s => s.total), 1);
            const height = Math.max((d.total / maxTotal) * 60, 4);
            const date = new Date(d.date);
            return `<div class="daily-bar" style="height:${height}px" title="${d.date}: ${d.total}次"><span>${date.getDate()}日</span></div>`;
        }).join('');

        const modal = document.createElement('div');
        modal.className = 'modal-overlay';
        modal.id = 'diaperReportModal';
        modal.innerHTML = `
            <div class="modal modal-large">
                <div class="modal-header">
                    <h2>换尿布报表</h2>
                    <button class="modal-close" onclick="closeDiaperReportModal()">X</button>
                </div>
                <div class="modal-body">
                    <div class="report-summary-grid">
                        <div class="report-summary-card">
                            <div class="report-value">${data.total_changes}</div>
                            <div class="report-label">30天总次数</div>
                        </div>
                        <div class="report-summary-card">
                            <div class="report-value">${data.avg_per_day}</div>
                            <div class="report-label">日均次数</div>
                        </div>
                        <div class="report-summary-card">
                            <div class="report-value">${data.skin_issues}</div>
                            <div class="report-label">皮肤问题</div>
                        </div>
                    </div>
                    <div class="activity-detail-section">
                        <h4>类型分布</h4>
                        <div class="type-distribution">
                            <span class="type-item">💧 小便: ${data.wet_count}次</span>
                            <span class="type-item">💩 大便: ${data.dirty_count}次</span>
                            <span class="type-item">💧💩 都有: ${data.both_count}次</span>
                            <span class="type-item">✅ 干爽: ${data.dry_count}次</span>
                        </div>
                    </div>
                    <div class="activity-detail-section">
                        <h4>大便颜色分布</h4>
                        <div class="color-distribution">${colorDistHtml}</div>
                    </div>
                    <div class="activity-detail-section">
                        <h4>最近7天趋势</h4>
                        <div class="daily-chart">${dailyChartHtml || '<p class="empty-tip">暂无数据</p>'}</div>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }).catch(err => {
        console.error('加载报表失败:', err);
        showToast('加载报表失败');
    });
}

function closeDiaperReportModal() {
    const modal = document.getElementById('diaperReportModal');
    if (modal) modal.remove();
}

// 快捷添加换尿布记录
function quickAddDiaper(type) {
    if (!App.currentBaby) return;
    const data = {
        baby_id: App.currentBaby,
        diaper_type: type,
        change_time: nowLocalStr(),
    };
    api('/api/babies/' + App.currentBaby + '/diaper', {
        method: 'POST',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            showToast.success('已添加换尿布记录');
            loadDiaperPage();
            loadTimeline();
            loadDashboard(); // 同步更新首页
        } else {
            showToast.error(res.message || '添加失败');
        }
    });
}

function renderFeedingItems(r) {
    const typeMap = { breast: '母乳', bottle: '配方奶', solid: '辅食', mixed: '混合' };
    const sideMap = { left: '左侧', right: '右侧', both: '双侧' };
    const parts = [];

    if (r.amount) parts.push(`${escapeHtml(String(r.amount))}ml`);
    if (r.side) parts.push(sideMap[r.side] || escapeHtml(String(r.side)));

    const totalSec = (Number(r.left_duration) || 0) + (Number(r.right_duration) || 0);
    if (totalSec) {
        const lr = `（左 ${formatDurationCN(r.left_duration) || '-'} / 右 ${formatDurationCN(r.right_duration) || '-'}）`;
        parts.push(`亲喂 ${formatDurationCN(totalSec)}${lr}`);
    } else {
        // 没计左右侧、只填了起止时间 → 直接算本次时长
        const s = parseDateSafe(r.start_time);
        const e = parseDateSafe(r.end_time);
        if (s && e && e > s) parts.push(`时长 ${formatDurationCN(Math.round((e - s) / 1000))}`);
    }
    if (r.note) parts.push(escapeHtml(r.note));

    return `
        <div class="care-item">
            <button class="care-delete" onclick="deleteRecord('feeding', ${r.id})">✕</button>
            <button class="care-edit" onclick="editFeeding(${r.id})">✎</button>
            <div class="care-item-header">
                <span class="care-type">${typeMap[r.feeding_type] || r.feeding_type}</span>
                <span class="care-time">${formatDateTime(r.start_time)}</span>
            </div>
            <div class="care-detail">${parts.join(' · ')}</div>
        </div>
    `;
}

// 编辑喂奶记录：与新增共用同一套表单，左右侧计时时长会回填，保存时不会被抹掉
function editFeeding(id) {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/feeding/${id}`).then(res => {
        if (!res.success) return showToast.error('加载失败');
        const r = res.data;
        editingFeedingId = id;
        feedingFormRecord = r;
        // 复用 recordModal 容器（showModal 只认元素 id，不能直接传 HTML 字符串）
        document.getElementById('recordModalTitle').textContent = '编辑喂奶记录';
        document.getElementById('recordModalBody').innerHTML = feedingFormHtml(r);
        showModal('recordModal');
        bindRecordFormEvents('feeding');
    });
}

function renderPumpingItems(r) {
    const typeMap = { manual: '手动', electric: '电动', double: '双边' };
    const fmtDur = (s) => s ? `${Math.floor(s / 60)}分${s % 60}秒` : '';
    const durInfo = (r.duration_minutes || r.left_duration || r.right_duration)
        ? ` (${r.duration_minutes ? escapeHtml(String(r.duration_minutes)) + '分钟' : ''}${r.left_duration ? ' 左' + fmtDur(r.left_duration) : ''}${r.right_duration ? ' 右' + fmtDur(r.right_duration) : ''})`
        : '';
    return `
        <div class="care-item pumping">
            <button class="care-delete" onclick="deleteRecord('pumping', ${r.id})">✕</button>
            <button class="care-edit" onclick="editPumping(${r.id})">✎</button>
            <div class="care-item-header">
                <span class="care-type"> 吸奶 ${r.pump_type ? typeMap[r.pump_type] : ''}</span>
                <span class="care-time">${formatDateTime(r.pump_time)}</span>
            </div>
            <div class="care-detail">
                ${r.total_amount ? `<strong>${escapeHtml(String(r.total_amount))}ml</strong>` : ''}
                ${r.left_amount ? ` 左:${escapeHtml(String(r.left_amount))}ml` : ''}
                ${r.right_amount ? ` 右:${escapeHtml(String(r.right_amount))}ml` : ''}
                ${durInfo}
                ${r.note ? ` ${escapeHtml(r.note)}` : ''}
            </div>
        </div>
    `;
}

// 编辑吸奶记录（复用 recordModal 容器，与编辑喂奶同范式）
function editPumping(id) {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/pumping/${id}`).then(res => {
        if (!res.success) return showToast.error('加载失败');
        const r = res.data;
        document.getElementById('recordModalTitle').textContent = '编辑吸奶记录';
        const body = document.getElementById('recordModalBody');
        const val = (v) => (v != null ? v : '');
        body.innerHTML = `
            <form id="editPumpingForm">
                <div class="form-group">
                    <label>吸奶时间</label>
                    <input type="datetime-local" id="editPumpTime" value="${r.pump_time ? r.pump_time.slice(0, 16) : ''}" required>
                </div>
                <div class="form-group">
                    <label>吸奶类型</label>
                    <select id="editPumpType">
                        <option value="">选择</option>
                        <option value="manual" ${r.pump_type === 'manual' ? 'selected' : ''}>手动</option>
                        <option value="electric" ${r.pump_type === 'electric' ? 'selected' : ''}>电动</option>
                        <option value="double" ${r.pump_type === 'double' ? 'selected' : ''}>双边</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>总时长 (分钟)</label>
                    <input type="number" id="editPumpDuration" value="${val(r.duration_minutes)}" placeholder="可选">
                </div>
                <div class="form-group-row">
                    <div class="form-group">
                        <label>左侧奶量 (ml)</label>
                        <input type="number" id="editPumpLeftAmount" value="${val(r.left_amount)}" placeholder="可选">
                    </div>
                    <div class="form-group">
                        <label>右侧奶量 (ml)</label>
                        <input type="number" id="editPumpRightAmount" value="${val(r.right_amount)}" placeholder="可选">
                    </div>
                </div>
                <div class="form-group">
                    <label>总奶量 (ml)</label>
                    <input type="number" id="editPumpTotalAmount" value="${val(r.total_amount)}" placeholder="自动计算或手动填写">
                </div>
                <div class="form-group-row">
                    <div class="form-group">
                        <label>左侧时长 (秒)</label>
                        <input type="number" id="editPumpLeftDuration" value="${val(r.left_duration)}" placeholder="可选">
                    </div>
                    <div class="form-group">
                        <label>右侧时长 (秒)</label>
                        <input type="number" id="editPumpRightDuration" value="${val(r.right_duration)}" placeholder="可选">
                    </div>
                </div>
                <div class="form-group">
                    <label>备注</label>
                    <input type="text" id="editPumpNote" value="${escapeHtml(r.note || '')}" placeholder="可选">
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-cancel" onclick="hideModal('recordModal')">取消</button>
                    <button type="submit" class="btn btn-primary">保存</button>
                </div>
            </form>
        `;
        showModal('recordModal');
        document.getElementById('editPumpingForm').addEventListener('submit', function(e) {
            e.preventDefault();
            savePumpingEdit(id);
        });
    });
}

function savePumpingEdit(id) {
    const leftAmount = document.getElementById('editPumpLeftAmount').value;
    const rightAmount = document.getElementById('editPumpRightAmount').value;
    let totalAmount = document.getElementById('editPumpTotalAmount').value;
    if (!totalAmount && (leftAmount || rightAmount)) {
        totalAmount = (parseFloat(leftAmount) || 0) + (parseFloat(rightAmount) || 0);
    }
    const data = {
        pump_time: document.getElementById('editPumpTime').value,
        pump_type: document.getElementById('editPumpType').value || null,
        duration_minutes: document.getElementById('editPumpDuration').value || null,
        left_amount: leftAmount || null,
        right_amount: rightAmount || null,
        total_amount: totalAmount || null,
        left_duration: document.getElementById('editPumpLeftDuration').value || null,
        right_duration: document.getElementById('editPumpRightDuration').value || null,
        note: document.getElementById('editPumpNote').value
    };
    api(`/api/babies/${App.currentBaby}/pumping/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            showToast.success('已更新');
            hideModal('recordModal');
            loadPumpingPage();
            loadDashboard();
        } else {
            showToast.error(res.message || '更新失败');
        }
    });
}

function renderSleepItems(r) {
    const qualityMap = { good: '好', normal: '一般', poor: '差' };
    const napText = r.is_nap === 1 ? '小憩' : '夜间';
    const dur = formatMinutesCN(r.duration_minutes);
    return `
        <div class="care-item">
            <button class="care-delete" onclick="deleteRecord('sleep', ${r.id})">✕</button>
            <button class="care-edit" onclick="editSleep(${r.id})">✎</button>
            <div class="care-item-header">
                <span class="care-type">${napText}${dur ? ' · ' + dur : ''}</span>
                <span class="care-time">${formatDateTime(r.start_time)}</span>
            </div>
            <div class="care-detail">
                ${r.end_time ? `至 ${formatDateTime(r.end_time)}` : '进行中'}
                ${r.sleep_quality ? ` 质量: ${escapeHtml(qualityMap[r.sleep_quality] || r.sleep_quality)}` : ''}
                ${r.note ? ` ${escapeHtml(r.note)}` : ''}
            </div>
        </div>
    `;
}

// 编辑睡眠记录
function editSleep(id) {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/sleep/${id}`).then(res => {
        if (!res.success) return showToast.error('加载失败');
        const r = res.data;
        showModal(`
            <div class="modal-header"><h3>编辑睡眠记录</h3></div>
            <div class="modal-body">
                <div class="form-group">
                    <label>类型</label>
                    <select id="editSleepNap">
                        <option value="0" ${r.is_nap !== 1 ? 'selected' : ''}>夜间</option>
                        <option value="1" ${r.is_nap === 1 ? 'selected' : ''}>小憩</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>开始时间</label>
                    <input type="datetime-local" id="editSleepStart" value="${r.start_time ? r.start_time.slice(0, 16) : ''}">
                </div>
                <div class="form-group">
                    <label>结束时间</label>
                    <input type="datetime-local" id="editSleepEnd" value="${r.end_time ? r.end_time.slice(0, 16) : ''}">
                </div>
                <div class="form-group">
                    <label>睡眠质量</label>
                    <select id="editSleepQuality">
                        <option value="">未评</option>
                        <option value="good" ${r.sleep_quality === 'good' ? 'selected' : ''}>好</option>
                        <option value="normal" ${r.sleep_quality === 'normal' ? 'selected' : ''}>一般</option>
                        <option value="poor" ${r.sleep_quality === 'poor' ? 'selected' : ''}>差</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>备注</label>
                    <input type="text" id="editSleepNote" value="${escapeHtml(r.note || '')}" placeholder="可选">
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="closeModal()">取消</button>
                <button class="btn btn-primary" onclick="saveSleepEdit(${r.id})">保存</button>
            </div>
        `);
    });
}

function saveSleepEdit(id) {
    if (!App.currentBaby) return;
    const data = {
        is_nap: document.getElementById('editSleepNap').value === '1' ? 1 : 0,
        start_time: document.getElementById('editSleepStart').value,
        end_time: document.getElementById('editSleepEnd').value || null,
        // 传 null 让后端按新的起止时间重算（含跨夜），避免改了结束时间时长还停在旧值
        duration_minutes: null,
        // 「未评」要发 null 而不是空串，空串会被当成无效枚举值
        sleep_quality: document.getElementById('editSleepQuality').value || null,
        note: document.getElementById('editSleepNote').value,
    };
    api(`/api/babies/${App.currentBaby}/sleep/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            showToast.success('已更新');
            closeModal();
            loadSleepPage();
            loadTimeline();
            loadDashboard();
        } else {
            showToast.error(res.message || '更新失败');
        }
    });
}

function renderDiaperItems(r) {
    const typeMap = { wet: '💧 小便', dirty: '💩 大便', both: '💧💩 都有', dry: '✅ 干爽' };
    const colorMap = { black: '黑色', brown: '棕色', green: '绿色', yellow: '黄色', other: '其他' };
    const skinMap = { normal: '😊 正常', slight_red: '😐 轻微发红', rash: '😟 红臀', severe_rash: '😢 严重红臀' };
    const skinClass = { normal: 'skin-normal', slight_red: 'skin-slight', rash: 'skin-rash', severe_rash: 'skin-severe' };

    return `
        <div class="care-item ${r.skin_condition ? 'skin-' + r.skin_condition : ''}">
            <button class="care-delete" onclick="deleteRecord('diaper', ${r.id})">✕</button>
            <button class="care-edit" onclick="editDiaper(${r.id})">✎</button>
            <div class="care-item-header">
                <span class="care-type">${typeMap[r.diaper_type] || r.diaper_type}</span>
                <span class="care-time">${formatDateTime(r.change_time)}</span>
            </div>
            <div class="care-detail">
                ${r.color ? '<span class="care-color">' + (colorMap[r.color] || r.color) + ' </span>' : ''}
                ${r.skin_condition && r.skin_condition !== 'normal' ? '<span class="care-skin ' + (skinClass[r.skin_condition] || '') + '">' + (skinMap[r.skin_condition] || r.skin_condition) + ' </span>' : ''}
                ${r.note ? escapeHtml(r.note) : ''}
            </div>
        </div>
    `;
}

// 编辑换尿布记录（复用 recordModal 容器，与喂奶编辑同范式）
function editDiaper(id) {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/diaper/${id}`).then(res => {
        if (!res.success) return showToast.error('加载失败');
        const r = res.data;
        const timeVal = (r.change_time || '').slice(0, 16).replace(' ', 'T');
        const colorShown = (r.diaper_type === 'dirty' || r.diaper_type === 'both') ? 'block' : 'none';
        const title = document.getElementById('recordModalTitle');
        if (title) title.textContent = '编辑换尿布记录';
        const body = document.getElementById('recordModalBody');
        if (!body) return;
        body.innerHTML = `
            <form id="diaperEditForm">
                <div class="form-group">
                    <label>时间</label>
                    <input type="datetime-local" id="editDiaperTime" value="${timeVal}" required>
                </div>
                <div class="form-group">
                    <label>类型</label>
                    <select id="editDiaperType">
                        <option value="wet" ${r.diaper_type === 'wet' ? 'selected' : ''}>💧 小便</option>
                        <option value="dirty" ${r.diaper_type === 'dirty' ? 'selected' : ''}>💩 大便</option>
                        <option value="both" ${r.diaper_type === 'both' ? 'selected' : ''}>💧💩 都有</option>
                        <option value="dry" ${r.diaper_type === 'dry' ? 'selected' : ''}>✅ 干爽</option>
                    </select>
                </div>
                <div class="form-group" id="editDiaperColorGroup" style="display:${colorShown}">
                    <label>大便颜色</label>
                    <select id="editDiaperColor">
                        <option value="">选择颜色</option>
                        <option value="black" ${r.color === 'black' ? 'selected' : ''}>黑色（胎便）</option>
                        <option value="brown" ${r.color === 'brown' ? 'selected' : ''}>棕色（正常）</option>
                        <option value="green" ${r.color === 'green' ? 'selected' : ''}>绿色</option>
                        <option value="yellow" ${r.color === 'yellow' ? 'selected' : ''}>黄色</option>
                        <option value="other" ${r.color === 'other' ? 'selected' : ''}>其他</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>皮肤状况</label>
                    <select id="editDiaperSkin">
                        <option value="" ${!r.skin_condition ? 'selected' : ''}>正常</option>
                        <option value="normal" ${r.skin_condition === 'normal' ? 'selected' : ''}>😊 正常</option>
                        <option value="slight_red" ${r.skin_condition === 'slight_red' ? 'selected' : ''}>😐 轻微发红</option>
                        <option value="rash" ${r.skin_condition === 'rash' ? 'selected' : ''}>😟 红臀</option>
                        <option value="severe_rash" ${r.skin_condition === 'severe_rash' ? 'selected' : ''}>😢 严重红臀</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>备注</label>
                    <input type="text" id="editDiaperNote" value="${escapeHtml(r.note || '')}" placeholder="可选">
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-cancel" onclick="hideModal('recordModal')">取消</button>
                    <button type="submit" class="btn btn-primary">保存</button>
                </div>
            </form>
        `;
        // 类型切换时显示/隐藏颜色选择（与添加表单一致）
        document.getElementById('editDiaperType')?.addEventListener('change', function() {
            const g = document.getElementById('editDiaperColorGroup');
            if (g) g.style.display = (this.value === 'dirty' || this.value === 'both') ? 'block' : 'none';
        });
        document.getElementById('diaperEditForm')?.addEventListener('submit', function(e) {
            e.preventDefault();
            saveDiaperEdit(id);
        });
        showModal('recordModal');
    });
}

function saveDiaperEdit(id) {
    const diaperType = document.getElementById('editDiaperType')?.value || '';
    const skinCondition = document.getElementById('editDiaperSkin')?.value || null;
    const data = {
        baby_id: App.currentBaby,
        change_time: document.getElementById('editDiaperTime')?.value || '',
        diaper_type: diaperType,
        color: (diaperType === 'dirty' || diaperType === 'both') ? document.getElementById('editDiaperColor')?.value || null : null,
        skin_condition: skinCondition || null,
        note: document.getElementById('editDiaperNote')?.value || ''
    };

    api(`/api/babies/${App.currentBaby}/diaper/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            hideModal('recordModal');
            showToast.success('已更新');
            loadDiaperPage();
            loadDashboard();
            loadTimeline();
        } else {
            showToast.error(res.message || '更新失败');
        }
    });
}

function deleteRecord(type, id) {
    if (!confirm('确定要删除这条记录吗？')) return;

    api(`/api/${type}/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast.success('已删除');
            // 根据类型重新加载对应页面
            if (type === 'feeding') {
                loadFeedingPage();
            } else if (type === 'pumping') {
                loadPumpingPage();
            } else if (type === 'sleep') {
                loadSleepPage();
            } else if (type === 'diaper') {
                loadDiaperPage();
            } else if (type === 'growth') {
                loadGrowthPage();
                // 如果当前在健康档案的生长曲线Tab，也刷新图表
                if (document.getElementById('tab-growth') && document.getElementById('tab-growth').classList.contains('active')) {
                    const activeMetric = document.querySelector('.growth-metric-btn.active');
                    if (activeMetric) loadGrowthChart(activeMetric.dataset.metric);
                }
            }
            loadDashboard();
        } else {
            showToast.error('删除失败');
        }
    });
}

// ==================== 日记 ====================

// 心情图标映射
const moodIcons = {
    happy: { icon: '', label: '开心', color: '#f59e0b' },
    calm: { icon: '', label: '平静', color: '#10b981' },
    excited: { icon: '', label: '兴奋', color: '#f43f5e' },
    fussy: { icon: '', label: '烦躁', color: '#6366f1' }
};

// 日记收藏
function getFavoriteDiaries() {
    try {
        return JSON.parse(localStorage.getItem('favoriteDiaries') || '[]');
    } catch {
        return [];
    }
}

function isDiaryFavorite(id) {
    return getFavoriteDiaries().includes(id);
}

function toggleDiaryFavorite(id) {
    const favs = getFavoriteDiaries();
    const idx = favs.indexOf(id);
    if (idx >= 0) {
        favs.splice(idx, 1);
    } else {
        favs.push(id);
    }
    localStorage.setItem('favoriteDiaries', JSON.stringify(favs));
    return idx < 0;
}

function initDiaryModal() {
    document.getElementById('closeDiaryModal').addEventListener('click', () => hideModal('diaryModal'));
    document.getElementById('cancelDiaryForm').addEventListener('click', () => hideModal('diaryModal'));

    // 日记页面顶部添加按钮事件
    const addDiaryBtn = document.getElementById('addDiaryBtn');
    if (addDiaryBtn) {
        addDiaryBtn.addEventListener('click', () => showDiaryModal());
    }

    // 搜索和筛选事件
    document.getElementById('diarySearchInput')?.addEventListener('input', debounce(loadDiaryList, 300));
    document.getElementById('diaryMoodFilter')?.addEventListener('change', loadDiaryList);
    document.getElementById('diaryFavFilter')?.addEventListener('change', loadDiaryList);

    document.getElementById('diaryForm').addEventListener('submit', function(e) {
        e.preventDefault();
        const data = {
            baby_id: App.currentBaby,
            title: document.getElementById('diaryTitle').value,
            entry_date: document.getElementById('diaryDate').value,
            content: document.getElementById('diaryContent').value,
            mood: document.getElementById('diaryMood').value || null,
            photos: currentDiaryPhotos
        };

        api(`/api/babies/${App.currentBaby}/diary`, {
            method: 'POST',
            body: JSON.stringify(data)
        }).then(res => {
            if (res.success) {
                hideModal('diaryModal');
                showToast.success('日记已保存');
                this.reset();
                currentDiaryPhotos = [];
                loadDiaryList();
            } else {
                showToast.error(res.message || '保存失败');
            }
        });
    });
}

// 日记照片附件
let currentDiaryPhotos = [];

function loadDiaryList() {
    if (!App.currentBaby) return;

    const search = document.getElementById('diarySearchInput')?.value || '';
    const moodFilter = document.getElementById('diaryMoodFilter')?.value || '';
    const favOnly = document.getElementById('diaryFavFilter')?.checked || false;

    api(`/api/babies/${App.currentBaby}/diary`).then(res => {
        if (!res.success) return;
        const container = document.getElementById('diaryList');
        const statsContainer = document.getElementById('diaryStats');

        let entries = res.data || [];

        // 应用筛选
        if (search) {
            const s = search.toLowerCase();
            entries = entries.filter(e =>
                (e.title || '').toLowerCase().includes(s) ||
                (e.content || '').toLowerCase().includes(s)
            );
        }
        if (moodFilter) {
            entries = entries.filter(e => e.mood === moodFilter);
        }
        if (favOnly) {
            const favs = getFavoriteDiaries();
            entries = entries.filter(e => favs.includes(e.id));
        }

        // 渲染统计
        if (statsContainer) {
            const total = res.data.length;
            const thisMonth = res.data.filter(e => (e.entry_date || '').startsWith(getToday().slice(0, 7))).length;
            statsContainer.innerHTML = `
                <div class="diary-stat-item"><span class="diary-stat-num">${total}</span><span class="diary-stat-label">总日记</span></div>
                <div class="diary-stat-item"><span class="diary-stat-num">${thisMonth}</span><span class="diary-stat-label">本月</span></div>
                <div class="diary-stat-item"><span class="diary-stat-num">${getFavoriteDiaries().length}</span><span class="diary-stat-label">收藏</span></div>
            `;
        }

        if (entries.length === 0) {
            container.innerHTML = `
                <div class="diary-empty-add">
                    <p>${res.data.length === 0 ? '还没有日记，记录宝宝成长的每一天' : '没有匹配的日记'}</p>
                    <button class="btn btn-primary" onclick="showDiaryModal()">写日记</button>
                </div>
            `;
            return;
        }

        container.innerHTML = `
            <div style="text-align:right;margin-bottom:12px;">
                <button class="btn btn-primary" onclick="showDiaryModal()" style="width:auto;padding:8px 16px;">+ 写日记</button>
            </div>
            ${entries.map(entry => {
                const mood = moodIcons[entry.mood] || null;
                const isFav = isDiaryFavorite(entry.id);
                const photos = parseDiaryPhotos(entry.photos);
                return `
                <div class="diary-item" onclick="viewDiary(${entry.id})">
                    <div class="diary-item-header">
                        <div class="diary-title">
                            ${mood ? `<span class="diary-mood-icon" style="color:${mood.color}">${mood.icon}</span>` : ''}
                            ${escapeHtml(entry.title)}
                        </div>
                        <div class="diary-item-actions">
                            <button class="diary-fav-btn ${isFav ? 'active' : ''}" onclick="event.stopPropagation();toggleDiaryFavorite(${entry.id});loadDiaryList()" title="${isFav ? '取消收藏' : '收藏'}">${isFav ? '★' : '☆'}</button>
                            <button class="care-edit" onclick="event.stopPropagation();editDiary(${entry.id})">✎</button>
                            <button class="care-delete" onclick="event.stopPropagation();deleteDiary(${entry.id})">✕</button>
                        </div>
                    </div>
                    <div class="diary-meta">
                        <span>${escapeHtml(entry.entry_date)}</span>
                        ${mood ? `<span class="diary-mood-tag" style="background:${mood.color}20;color:${mood.color}">${mood.label}</span>` : ''}
                        ${photos.length > 0 ? `<span class="diary-photo-count">📷 ${photos.length}</span>` : ''}
                    </div>
                    <div class="diary-content">${escapeHtml((entry.content || '无内容').slice(0, 100))}${entry.content && entry.content.length > 100 ? '...' : ''}</div>
                    ${photos.length > 0 ? `<div class="diary-photos-preview">${photos.slice(0, 3).map(p => `<img src="${App.apiBase}/api/photos/thumbnail/${p}" class="diary-photo-thumb" loading="lazy">`).join('')}${photos.length > 3 ? `<span class="diary-photo-more">+${photos.length - 3}</span>` : ''}</div>` : ''}
                </div>
            `}).join('')}
        `;
    });
}

function parseDiaryPhotos(photosJson) {
    try {
        if (!photosJson) return [];
        const arr = JSON.parse(photosJson);
        return Array.isArray(arr) ? arr : [];
    } catch {
        return [];
    }
}

function editDiary(id) {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/diary/${id}`).then(res => {
        if (!res.success) return showToast.error('加载失败');
        const entry = res.data;
        showModal(`
            <div class="modal-header"><h3>编辑日记</h3></div>
            <div class="modal-body">
                <div class="form-group">
                    <label>标题</label>
                    <input type="text" id="editDiaryTitle" value="${escapeHtml(entry.title || '')}" placeholder="日记标题">
                </div>
                <div class="form-group">
                    <label>日期</label>
                    <input type="date" id="editDiaryDate" value="${entry.entry_date || ''}">
                </div>
                <div class="form-group">
                    <label>心情</label>
                    <select id="editDiaryMood">
                        <option value="">未选择</option>
                        <option value="happy" ${entry.mood === 'happy' ? 'selected' : ''}>开心</option>
                        <option value="calm" ${entry.mood === 'calm' ? 'selected' : ''}>平静</option>
                        <option value="fussy" ${entry.mood === 'fussy' ? 'selected' : ''}>烦躁</option>
                        <option value="excited" ${entry.mood === 'excited' ? 'selected' : ''}>兴奋</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>内容</label>
                    <textarea id="editDiaryContent" rows="5" placeholder="记录宝宝成长的点滴...">${escapeHtml(entry.content || '')}</textarea>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="closeModal()">取消</button>
                <button class="btn btn-primary" onclick="saveDiaryEdit(${entry.id})">保存</button>
            </div>
        `);
    });
}

function saveDiaryEdit(id) {
    if (!App.currentBaby) return;
    const data = {
        title: document.getElementById('editDiaryTitle').value,
        entry_date: document.getElementById('editDiaryDate').value,
        mood: document.getElementById('editDiaryMood').value || null,
        content: document.getElementById('editDiaryContent').value,
    };
    api(`/api/babies/${App.currentBaby}/diary/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            showToast.success('已更新');
            closeModal();
            loadDiaryList();
        } else {
            showToast.error(res.message || '更新失败');
        }
    });
}

function showDiaryModal() {
    if (!App.currentBaby) {
        showToast.warning('请先添加宝宝');
        return;
    }
    document.getElementById('diaryForm').reset();
    document.getElementById('diaryDate').value = getToday();
    showModal('diaryModal');
}

function viewDiary(id) {
    // 简单实现：显示在弹窗中
    api(`/api/babies/${App.currentBaby}/diary`).then(res => {
        if (!res.success) return;
        const entry = res.data.find(e => e.id === id);
        if (entry) {
            document.getElementById('articleTitle').textContent = entry.title;
            // 使用textContent防止XSS，内容中的HTML标签会被转义
            document.getElementById('articleContent').innerHTML = `
                <p style="color:var(--text-secondary);margin-bottom:12px;">${escapeHtml(entry.entry_date)}</p>
                ${escapeHtml(entry.content || '无内容').replace(/\n/g, '<br>')}
            `;
            showModal('articleModal');
        }
    });
}

function deleteDiary(id) {
    if (!confirm('确定要删除这篇日记吗？')) return;
    api(`/api/diary/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast.success('已删除');
            loadDiaryList();
        }
    });
}

// ==================== 育儿知识 ====================

function initKnowledgeCategories() {
    if (App.knowledgeInitialized) return;
    App.knowledgeInitialized = true;
    App.currentKnowledgeCategory = App.currentKnowledgeCategory || 'all';

    // 分类筛选：仅作用于「育儿知识库」页面（避免误响应健康百科的分类按钮）
    document.querySelectorAll('#page-knowledge .category-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#page-knowledge .category-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            App.currentKnowledgeCategory = btn.dataset.category;
            loadKnowledgeList();
        });
    });

    // 文章详情弹窗关闭 + 管理员增删改
    document.getElementById('closeArticleModal')?.addEventListener('click', closeArticleModal);
    document.getElementById('addArticleBtn')?.addEventListener('click', () => openArticleForm());
    document.getElementById('closeArticleFormModal')?.addEventListener('click', closeArticleFormModal);
    document.getElementById('cancelArticleForm')?.addEventListener('click', closeArticleFormModal);
    document.getElementById('articleForm')?.addEventListener('submit', saveArticleForm);
    document.getElementById('editArticleBtn')?.addEventListener('click', editCurrentArticle);
    document.getElementById('deleteArticleBtn')?.addEventListener('click', deleteCurrentArticle);
}

let knowledgeSearchTimer = null;

function initKnowledgeSearch() {
    const searchInput = document.getElementById('knowledgeSearchInput');
    if (!searchInput) return;

    searchInput.addEventListener('input', () => {
        clearTimeout(knowledgeSearchTimer);
        knowledgeSearchTimer = setTimeout(() => {
            loadKnowledgeList(searchInput.value.trim());
        }, 300);
    });
}

function loadKnowledgeList(searchKeyword) {
    const category = App.currentKnowledgeCategory;
    let url = category === 'all' ? '/api/knowledge' : `/api/knowledge?category=${category}`;

    if (searchKeyword) {
        url += (url.includes('?') ? '&' : '?') + `q=${encodeURIComponent(searchKeyword)}`;
    }

    api(url).then(res => {
        if (!res.success) return;
        const container = document.getElementById('knowledgeList');

        if (res.data.length === 0) {
            container.innerHTML = '<p class="empty-tip">暂无文章</p>';
            return;
        }

        const categoryMap = {
            feeding: '喂养', sleep: '睡眠', development: '发育',
            health: '健康', care: '护理', vaccine: '疫苗'
        };

        container.innerHTML = res.data.map(article => `
            <div class="knowledge-item" onclick="viewArticle(${article.id})">
                <div class="knowledge-title">${escapeHtml(article.title)}</div>
                <div class="knowledge-meta">
                    <span class="knowledge-tag">${escapeHtml(categoryMap[article.category] || article.category)}</span>
                    ${article.age_range ? `<span>${escapeHtml(article.age_range)}</span>` : ''}
                </div>
            </div>
        `).join('');
    });
}

function viewArticle(id) {
    api(`/api/knowledge/${id}`).then(res => {
        if (!res.success) return;
        const article = res.data;
        currentArticleId = id;
        currentArticleData = article;

        document.getElementById('articleTitle').textContent = article.title;

        // 先转义HTML特殊字符防止XSS，再渲染Markdown
        let content = escapeHtml(article.content)
            .replace(/^## (.*$)/gm, '<h2>$1</h2>')
            .replace(/^### (.*$)/gm, '<h3>$1</h3>')
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/^- (.*$)/gm, '<li>$1</li>')
            .replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')
            .replace(/^\d+\. (.*$)/gm, '<li>$1</li>')
            .replace(/\n\n/g, '</p><p>')
            .replace(/\n/g, '<br>');

        document.getElementById('articleContent').innerHTML = `<p>${content}</p>`;
        showModal('articleModal');
    });
}

// ==================== 育儿知识文章 管理 ====================
// currentArticleId / currentArticleData 用隐式全局，避免与其它全局同名变量冲突
function closeArticleModal() {
    hideModal('articleModal');
    currentArticleId = null;
    currentArticleData = null;
}

function openArticleForm(article) {
    const modal = document.getElementById('articleFormModal');
    if (!modal) return;
    const form = document.getElementById('articleForm');
    form.reset();
    if (article) {
        document.getElementById('articleFormTitle').textContent = '编辑文章';
        document.getElementById('articleFormId').value = article.id || '';
        document.getElementById('articleFormTitleInput').value = article.title || '';
        document.getElementById('articleFormCategory').value = article.category || '其他';
        document.getElementById('articleFormAgeRange').value = article.age_range || '';
        document.getElementById('articleFormContent').value = article.content || '';
    } else {
        document.getElementById('articleFormTitle').textContent = '新增文章';
        document.getElementById('articleFormId').value = '';
    }
    showModal('articleFormModal');
}

function closeArticleFormModal() {
    hideModal('articleFormModal');
}

function saveArticleForm(e) {
    e.preventDefault();
    const id = document.getElementById('articleFormId').value;
    const data = {
        title: document.getElementById('articleFormTitleInput').value.trim(),
        category: document.getElementById('articleFormCategory').value,
        age_range: document.getElementById('articleFormAgeRange').value.trim(),
        content: document.getElementById('articleFormContent').value,
    };
    if (!data.title) { showToast('请填写文章标题'); return; }
    const url = id ? `/api/knowledge/${id}` : '/api/knowledge';
    const method = id ? 'PUT' : 'POST';
    api(url, { method, body: JSON.stringify(data) }).then(res => {
        if (res.success) {
            showToast(id ? '文章已更新' : '文章已添加');
            closeArticleFormModal();
            loadKnowledgeList();
        } else {
            showToast(res.message || '保存失败');
        }
    }).catch(err => { console.error('保存文章失败:', err); showToast('保存失败，请重试'); });
}

function editCurrentArticle() {
    if (currentArticleData) { closeArticleModal(); openArticleForm(currentArticleData); }
}

function deleteCurrentArticle() {
    if (!currentArticleId) return;
    if (!confirm('确定要删除这篇文章吗？删除后无法恢复。')) return;
    api(`/api/knowledge/${currentArticleId}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast('文章已删除');
            closeArticleModal();
            loadKnowledgeList();
        } else {
            showToast(res.message || '删除失败');
        }
    }).catch(err => { console.error('删除文章失败:', err); showToast('删除失败，请重试'); });
}

// ==================== 健康百科 ====================
function initWikiPage() {
    if (App.wikiInitialized) return;
    App.wikiInitialized = true;
    App.currentWikiCategory = App.currentWikiCategory || 'all';

    // 分类筛选（仅作用于健康百科页面）
    document.querySelectorAll('#page-wiki .category-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#page-wiki .category-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            App.currentWikiCategory = btn.dataset.category;
            loadWikiList();
        });
    });

    // 搜索（防抖）
    const wikiSearch = document.getElementById('wikiSearchInput');
    if (wikiSearch) {
        wikiSearch.addEventListener('input', () => {
            clearTimeout(window._wikiSearchTimer);
            window._wikiSearchTimer = setTimeout(() => loadWikiList(wikiSearch.value.trim()), 300);
        });
    }

    // 详情弹窗关闭 + 管理员增删改
    document.getElementById('addWikiBtn')?.addEventListener('click', () => openWikiForm());
    document.getElementById('closeWikiModal')?.addEventListener('click', closeWikiModal);
    document.getElementById('closeWikiFormModal')?.addEventListener('click', closeWikiFormModal);
    document.getElementById('cancelWikiForm')?.addEventListener('click', closeWikiFormModal);
    document.getElementById('wikiForm')?.addEventListener('submit', saveWikiForm);
    document.getElementById('editWikiBtn')?.addEventListener('click', editCurrentWiki);
    document.getElementById('deleteWikiBtn')?.addEventListener('click', deleteCurrentWiki);
}

function loadWikiList(searchKeyword) {
    const category = App.currentWikiCategory || 'all';
    let url = category !== 'all' ? `/api/wiki?category=${encodeURIComponent(category)}` : '/api/wiki';
    if (searchKeyword) url += (url.includes('?') ? '&' : '?') + `keyword=${encodeURIComponent(searchKeyword)}`;

    api(url).then(res => {
        const container = document.getElementById('wikiList');
        if (!container) return;
        if (!res.success) { container.innerHTML = '<p class="empty-tip">加载失败</p>'; return; }
        const items = res.data || [];
        if (items.length === 0) { container.innerHTML = '<p class="empty-tip">暂无条目</p>'; return; }
        container.innerHTML = items.map(w => `
            <div class="knowledge-item" onclick="viewWiki(${w.id})">
                <div class="knowledge-title">${escapeHtml(w.title)}</div>
                <div class="knowledge-meta">
                    <span class="knowledge-tag">${escapeHtml(w.category || '')}</span>
                    ${w.symptoms ? `<span>${escapeHtml((w.symptoms || '').split(/[,,]/)[0].trim())}</span>` : ''}
                </div>
            </div>
        `).join('');
    }).catch(err => console.error('加载健康百科失败:', err));
}

function viewWiki(id) {
    api(`/api/wiki/${id}`).then(res => {
        if (!res.success) return;
        const w = res.data;
        currentWikiId = id;
        currentWikiData = w;
        document.getElementById('wikiTitle').textContent = w.title;
        const body = document.getElementById('wikiModalBody');
        if (body) {
            body.innerHTML = `
                <div class="wiki-meta">
                    <span class="knowledge-tag">${escapeHtml(w.category || '')}</span>
                </div>
                ${w.symptoms ? `<div class="wiki-section"><h4>症状</h4><p>${escapeHtml(w.symptoms)}</p></div>` : ''}
                <div class="wiki-section wiki-content">${escapeHtml(w.content || '').replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>')}</div>
            `;
        }
        showModal('wikiModal');
    }).catch(err => console.error('加载百科详情失败:', err));
}

function closeWikiModal() {
    hideModal('wikiModal');
    currentWikiId = null;
    currentWikiData = null;
}

function openWikiForm(wiki) {
    const modal = document.getElementById('wikiFormModal');
    if (!modal) return;
    const form = document.getElementById('wikiForm');
    form.reset();
    if (wiki) {
        document.getElementById('wikiFormTitle').textContent = '编辑百科条目';
        document.getElementById('wikiFormId').value = wiki.id || '';
        document.getElementById('wikiFormTitleInput').value = wiki.title || '';
        document.getElementById('wikiFormCategory').value = wiki.category || '常见病';
        document.getElementById('wikiFormSymptoms').value = wiki.symptoms || '';
        document.getElementById('wikiFormContent').value = wiki.content || '';
    } else {
        document.getElementById('wikiFormTitle').textContent = '新增百科条目';
        document.getElementById('wikiFormId').value = '';
    }
    showModal('wikiFormModal');
}

function closeWikiFormModal() {
    hideModal('wikiFormModal');
}

function saveWikiForm(e) {
    e.preventDefault();
    const id = document.getElementById('wikiFormId').value;
    const data = {
        title: document.getElementById('wikiFormTitleInput').value.trim(),
        category: document.getElementById('wikiFormCategory').value,
        symptoms: document.getElementById('wikiFormSymptoms').value.trim(),
        content: document.getElementById('wikiFormContent').value,
    };
    if (!data.title) { showToast('请填写标题'); return; }
    const url = id ? `/api/wiki/${id}` : '/api/wiki';
    const method = id ? 'PUT' : 'POST';
    api(url, { method, body: JSON.stringify(data) }).then(res => {
        if (res.success) {
            showToast(id ? '百科条目已更新' : '百科条目已添加');
            closeWikiFormModal();
            loadWikiList();
        } else {
            showToast(res.message || '保存失败');
        }
    }).catch(err => { console.error('保存百科失败:', err); showToast('保存失败，请重试'); });
}

function editCurrentWiki() {
    if (currentWikiData) { closeWikiModal(); openWikiForm(currentWikiData); }
}

function deleteCurrentWiki() {
    if (!currentWikiId) return;
    if (!confirm('确定要删除这个百科条目吗？删除后无法恢复。')) return;
    api(`/api/wiki/${currentWikiId}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast('百科条目已删除');
            closeWikiModal();
            loadWikiList();
        } else {
            showToast(res.message || '删除失败');
        }
    }).catch(err => { console.error('删除百科失败:', err); showToast('删除失败，请重试'); });
}

// ==================== 时间线 ====================

function initTimeline() {
    document.getElementById('timelineDays').addEventListener('change', () => {
        loadTimeline();
    });
    document.getElementById('timelineType').addEventListener('change', () => {
        loadTimeline();
    });
}

function loadTimeline() {
    if (!App.currentBaby) return;

    const days = document.getElementById('timelineDays').value;
    const typeFilter = document.getElementById('timelineType')?.value || 'all';
    const fromDate = new Date();
    fromDate.setDate(fromDate.getDate() - parseInt(days));
    const from = formatDate(fromDate);

    api(`/api/babies/${App.currentBaby}/timeline?from=${from}&limit=200`).then(res => {
        if (!res.success) return;
        const container = document.getElementById('timelineList');
        const statsEl = document.getElementById('timelineStats');

        let events = res.data || [];

        // 类型筛选
        if (typeFilter !== 'all') {
            events = events.filter(e => e.type === typeFilter);
        }

        // 显示统计
        if (events.length > 0 && statsEl) {
            const feedingCount = events.filter(e => e.type === 'feeding').length;
            const sleepCount = events.filter(e => e.type === 'sleep').length;
            const sleepMinutes = events.filter(e => e.type === 'sleep').reduce((s, e) => s + (e.duration_minutes || 0), 0);
            const diaperCount = events.filter(e => e.type === 'diaper').length;
            statsEl.innerHTML = `
                <div class="stat-chip">喂奶 ${feedingCount} 次</div>
                <div class="stat-chip">睡眠 ${sleepCount} 次 (${Math.floor(sleepMinutes / 60)}h${sleepMinutes % 60}m)</div>
                <div class="stat-chip">换尿布 ${diaperCount} 次</div>
                <div class="stat-chip">共 ${events.length} 条</div>
            `;
            statsEl.style.display = 'flex';
        } else if (statsEl) {
            statsEl.style.display = 'none';
        }

        if (events.length === 0) {
            container.innerHTML = '<p class="empty-tip">暂无记录</p>';
            return;
        }

        // 按日期分组
        const groups = {};
        res.data.forEach(event => {
            const date = event.time ? event.time.substring(0, 10) : '';
            if (!groups[date]) groups[date] = [];
            groups[date].push(event);
        });

        const typeMap = {
            feeding: { icon: '喂', class: 'feeding', title: (e) => {
                const types = { breast: '母乳', bottle: '配方奶', solid: '辅食' };
                return types[e.subtype] || '喂奶';
            }},
            sleep: { icon: '睡', class: 'sleep', title: (e) => {
                return e.is_nap === 1 ? '白天小憩' : '夜间睡眠';
            }},
            diaper: { icon: '布', class: 'diaper', title: (e) => {
                const types = { wet: '小便', dirty: '大便', both: '小便+大便' };
                return types[e.diaper_type] || '换尿布';
            }},
            growth: { icon: '长', class: 'growth', title: () => '成长记录' },
            milestone: { icon: '★', class: 'milestone', title: (e) => e.title }
        };

        let html = '';
        for (const [date, events] of Object.entries(groups)) {
            const dateObj = new Date(date);
            const dateLabel = `${dateObj.getMonth() + 1}月${dateObj.getDate()}日`;
            html += `<div class="timeline-date-group"><div class="timeline-date-label">${dateLabel}</div>`;

            events.forEach(event => {
                const t = typeMap[event.type] || { icon: '记', class: '', title: () => '记录' };
                const detail = getTimelineDetail(event);
                const time = event.time ? event.time.substring(11, 16) : '';

                html += `
                    <div class="timeline-item">
                        <div class="timeline-icon ${t.class}">${t.icon}</div>
                        <div class="timeline-content">
                            <div class="timeline-title">${escapeHtml(String(t.title(event) || ''))}</div>
                            ${detail ? `<div class="timeline-detail">${escapeHtml(String(detail))}</div>` : ''}
                        </div>
                        <span class="timeline-time">${time}</span>
                    </div>
                `;
            });
            html += '</div>';
        }

        container.innerHTML = html;
    });
}

function getTimelineDetail(event) {
    switch (event.type) {
        case 'feeding': {
            const parts = [];
            if (event.amount) parts.push(`${event.amount}ml`);
            if (event.side) {
                const sideMap = {left: '左侧', right: '右侧', both: '双侧'};
                parts.push(sideMap[event.side] || event.side);
            }
            if (event.left_duration || event.right_duration) {
                const left = event.left_duration ? `${Math.floor(event.left_duration / 60)}分${event.left_duration % 60}秒` : '-';
                const right = event.right_duration ? `${Math.floor(event.right_duration / 60)}分${event.right_duration % 60}秒` : '-';
                parts.push(`[左:${left} 右:${right}]`);
            }
            if (event.note) parts.push(event.note);
            return parts.filter(Boolean).join(' ');
        }
        case 'sleep': {
            const parts = [];
            if (event.duration_minutes) parts.push(`${event.duration_minutes}分钟`);
            if (event.sleep_quality) {
                const qualityMap = {good: '质量:好', normal: '质量:一般', poor: '质量:差'};
                parts.push(qualityMap[event.sleep_quality] || '');
            }
            if (event.note) parts.push(event.note);
            return parts.filter(Boolean).join(' ');
        }
        case 'diaper': {
            const parts = [];
            if (event.color) {
                const colorMap = {black: '黑色', brown: '棕色', green: '绿色', yellow: '黄色'};
                parts.push(colorMap[event.color] || event.color);
            }
            if (event.note) parts.push(event.note);
            return parts.filter(Boolean).join(' ');
        }
        case 'growth': {
            const parts = [];
            if (event.height) parts.push(`身高${event.height}cm`);
            if (event.weight) parts.push(`体重${event.weight}kg`);
            if (event.head_circumference) parts.push(`头围${event.head_circumference}cm`);
            if (event.bmi) parts.push(`BMI ${event.bmi}`);
            return parts.filter(Boolean).join(' ');
        }
        case 'milestone':
            return event.description || event.title || '';
        default:
            return event.note || event.description || '';
    }
}

// ==================== WHO 生长曲线图 ====================

/** 成长页 Tab → 视图容器 id。新增 Tab 只改这里一处。 */
const GROWTH_TAB_VIEWS = {
    'records': 'growthViewRecords',
    'who-chart': 'growthViewWhoChart',
    'velocity': 'growthViewVelocity',
    'correlation': 'growthViewCorrelation',
    'sleep-analysis': 'growthViewSleepAnalysis',
};

/**
 * 已并入成长页 Tab 的页面：page id → 宿主页面。
 * 「睡眠分析」原本是独立页面，报表类统一收进成长，
 * 但底部导航/相关功能仍可能按 'sleep-analysis' 跳，这里做一次转换，
 * 否则 switchPage 找不到 #page-sleep-analysis 会直接白屏。
 */
const GROWTH_TAB_PAGES = { 'sleep-analysis': { host: 'growth', tab: 'sleep-analysis' } };

/**
 * 已并入健康档案 Tab 的页面：合并前的 page id → { 宿主页面, 目标 Tab }。
 * 疫苗 / 体温 / 用药提醒 / 过敏测试四个独立页已删除，底部导航与「相关功能」
 * 仍可能按老 id 跳，靠这张表落到「健康档案 + 对应 Tab」。
 */
const HEALTH_TAB_PAGES = {
    'vaccines': { host: 'health', tab: 'vaccine' },
    'temperature': { host: 'health', tab: 'temperature' },
    'med-reminder': { host: 'health', tab: 'reminders' },
    'allergy-detail': { host: 'health', tab: 'allergy' },
};

/** 切到宿主页面（growth / health）的某个 Tab */
function activatePageTab(host, tab) {
    if (host === 'growth') return activateGrowthTab(tab);
    if (host === 'health') return activateHealthTab(tab);
    return false;
}

/** 切到成长页的某个 Tab。视图先显示再加载数据——
 *  canvas 在 display:none 时宽高是 0，先显示才能画出图。 */
function activateGrowthTab(tab) {
    const btn = document.querySelector('.growth-tab-btn[data-growth-tab="' + tab + '"]');
    if (btn) {
        btn.click();
        return true;
    }
    return false;
}

function initGrowthTabs() {
    document.querySelectorAll('.growth-tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.growth-tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.growthTab;

            Object.entries(GROWTH_TAB_VIEWS).forEach(([name, id]) => {
                const el = document.getElementById(id);
                if (el) el.style.display = (name === tab) ? 'block' : 'none';
            });

            // 顶部「身高/体重/BMI/头围」是成长专属，睡眠分析 Tab 下藏起来
            const summary = document.getElementById('growthSummary');
            if (summary) summary.style.display = (tab === 'sleep-analysis') ? 'none' : '';

            // 进入 Tab 时再拉数据：canvas 已可见，不会画成 0 宽高
            if (tab === 'who-chart') loadWhoChart();
            else if (tab === 'velocity') loadGrowthVelocity();
            else if (tab === 'correlation') loadCorrelationData();
            else if (tab === 'sleep-analysis') {
                loadSleepAnalysis();
                loadSleepPrediction();
            }
        });
    });

    document.querySelectorAll('.who-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.who-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            loadWhoChart(tab.dataset.metric);
        });
    });
}

function loadWhoChart(metric) {
    if (!App.currentBaby) return;
    metric = metric || 'weight';

    const baby = App.babies.find(b => b.id === App.currentBaby);
    const sex = baby && (baby.gender === 'boy' || baby.gender === 'girl') ? baby.gender : 'boy';

    // 获取参考数据
    api(`/api/who-reference?sex=${sex}&metric=${metric}`).then(refRes => {
        if (!refRes.success) return;
        const refData = refRes.data;

        // 获取宝宝成长记录
        api(`/api/babies/${App.currentBaby}/growth`).then(growthRes => {
            if (!growthRes.success) return;
            const records = growthRes.data.filter(r => {
                if (metric === 'weight') return r.weight;
                if (metric === 'height') return r.height;
                if (metric === 'head_circumference') return r.head_circumference;
                if (metric === 'bmi') return r.bmi;
                return false;
            });

            drawWhoChart(refData, records, metric, sex);
        });
    });
}

function drawWhoChart(refData, records, metric, sex) {
    const canvas = document.getElementById('whoChartCanvas');
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();

    canvas.width = rect.width * dpr;
    canvas.height = 350 * dpr;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = 350;
    const padding = { top: 30, right: 20, bottom: 40, left: 50 };
    const chartW = w - padding.left - padding.right;
    const chartH = h - padding.top - padding.bottom;

    ctx.clearRect(0, 0, w, h);

    if (refData.length === 0) {
        ctx.fillStyle = '#999';
        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('暂无参考数据', w / 2, h / 2);
        return;
    }

    const maxAge = refData[refData.length - 1].age_in_days;
    const allValues = refData.flatMap(d => [d.p3, d.p15, d.p50, d.p85, d.p97]);
    records.forEach(r => {
        const val = metric === 'weight' ? r.weight : metric === 'height' ? r.height : r.bmi;
        if (val) allValues.push(val);
    });
    const minVal = Math.floor(Math.min(...allValues) * 0.9);
    const maxVal = Math.ceil(Math.max(...allValues) * 1.1);

    // 绘制网格和标签
    ctx.strokeStyle = '#eee';
    ctx.lineWidth = 1;
    ctx.font = '11px sans-serif';
    ctx.fillStyle = '#999';
    ctx.textAlign = 'right';

    const ySteps = 5;
    for (let i = 0; i <= ySteps; i++) {
        const y = padding.top + (chartH / ySteps) * i;
        const val = maxVal - ((maxVal - minVal) / ySteps) * i;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(w - padding.right, y);
        ctx.stroke();
        ctx.fillText(val.toFixed(1), padding.left - 5, y + 4);
    }

    // X轴标签
    ctx.textAlign = 'center';
    const xSteps = Math.min(6, refData.length);
    for (let i = 0; i < xSteps; i++) {
        const idx = Math.floor(i * (refData.length - 1) / (xSteps - 1));
        const x = padding.left + (chartW / (xSteps - 1)) * i;
        const months = Math.round(refData[idx].age_in_days / 30);
        ctx.fillText(`${months}月`, x, h - padding.bottom + 20);
    }

    function xPos(days) {
        return padding.left + (days / maxAge) * chartW;
    }
    function yPos(val) {
        return padding.top + (1 - (val - minVal) / (maxVal - minVal)) * chartH;
    }

    // 绘制百分位区间填充
    const bands = [
        { lo: 'p3', hi: 'p50', color: 'rgba(255, 243, 224, 0.4)' },   // P3-P50 浅橙
        { lo: 'p50', hi: 'p97', color: 'rgba(232, 245, 233, 0.4)' }    // P50-P97 浅绿
    ];

    bands.forEach(band => {
        ctx.fillStyle = band.color;
        ctx.beginPath();
        // 正向：从P3到P97
        refData.forEach((d, i) => {
            const x = xPos(d.age_in_days);
            const y = yPos(d[band.hi]);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        // 反向：从P97回到P3
        for (let i = refData.length - 1; i >= 0; i--) {
            const x = xPos(refData[i].age_in_days);
            const y = yPos(refData[i][band.lo]);
            ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.fill();
    });

    // 绘制百分位曲线
    const percentiles = [
        { key: 'p3', color: '#e17055', label: 'P3', width: 1 },
        { key: 'p15', color: '#fdcb6e', label: 'P15', width: 1 },
        { key: 'p50', color: '#00b894', label: 'P50', width: 2.5 },
        { key: 'p85', color: '#6c5ce7', label: 'P85', width: 1 },
        { key: 'p97', color: '#e84393', label: 'P97', width: 1 }
    ];

    percentiles.forEach(p => {
        ctx.strokeStyle = p.color;
        ctx.lineWidth = p.width;
        ctx.setLineDash(p.key === 'p50' ? [] : [4, 3]);
        ctx.beginPath();
        refData.forEach((d, i) => {
            const x = xPos(d.age_in_days);
            const y = yPos(d[p.key]);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
        ctx.setLineDash([]);

        // 标签（只在最后一点）
        const last = refData[refData.length - 1];
        ctx.fillStyle = p.color;
        ctx.font = 'bold 10px sans-serif';
        ctx.textAlign = 'left';
        ctx.fillText(p.label, xPos(last.age_in_days) + 3, yPos(last[p.key]) + 3);
    });

    // 绘制宝宝实际数据点（更大更醒目）
    if (records.length > 0) {
        const baby = App.babies.find(b => b.id === App.currentBaby);
        if (baby) {
            const birthday = new Date(baby.birthday);
            records.forEach(r => {
                const recordDate = new Date(r.record_date);
                const ageDays = Math.max(0, (recordDate - birthday) / (1000 * 60 * 60 * 24));
                const val = metric === 'weight' ? r.weight : metric === 'height' ? r.height : metric === 'head_circumference' ? r.head_circumference : r.bmi;
                if (val && ageDays <= maxAge) {
                    const x = xPos(ageDays);
                    const y = yPos(val);

                    // 外圈光晕
                    ctx.fillStyle = 'rgba(255, 107, 157, 0.2)';
                    ctx.beginPath();
                    ctx.arc(x, y, 10, 0, Math.PI * 2);
                    ctx.fill();

                    // 主点
                    ctx.fillStyle = '#ff6b9d';
                    ctx.beginPath();
                    ctx.arc(x, y, 6, 0, Math.PI * 2);
                    ctx.fill();
                    ctx.strokeStyle = '#fff';
                    ctx.lineWidth = 2.5;
                    ctx.stroke();

                    // 数值标签
                    ctx.fillStyle = '#2d3436';
                    ctx.font = 'bold 10px sans-serif';
                    ctx.textAlign = 'center';
                    ctx.fillText(val.toFixed(1), x, y - 12);
                }
            });
        }
    }

    // 更新百分位信息
    api(`/api/babies/${App.currentBaby}/percentiles`).then(res => {
        if (res.success && res.data.percentiles && res.data.percentiles[metric]) {
            const p = res.data.percentiles[metric];
            const metricLabel = metric === 'weight' ? '体重' : metric === 'height' ? '身高' : metric === 'head_circumference' ? '头围' : 'BMI';
            const latestVal = res.data.latest ? res.data.latest[metric] : null;
            document.getElementById('whoChartInfo').innerHTML =
                `${metricLabel}：最新值 <strong>${latestVal || '--'}</strong>，百分位 <strong style="color:#00b894">${p.percentile || '--'}</strong> | 绿色线为中位数(P50)`;
        }
    });
}

// ==================== 生长速度 ====================

function loadGrowthVelocity() {
    if (!App.currentBaby) return;
    const summaryEl = document.getElementById('velocitySummary');
    const detailsEl = document.getElementById('velocityDetails');

    api(`/api/babies/${App.currentBaby}/growth-velocity`).then(res => {
        if (!res.success || !res.data) {
            summaryEl.innerHTML = `<p class="empty-tip">${escapeHtml(res.message || '需要至少2条记录才能计算生长速度')}</p>`;
            detailsEl.innerHTML = '';
            return;
        }

        const v = res.data;

        // 汇总卡片
        let summaryHtml = '<div class="velocity-cards">';
        if (v.weight_velocity !== undefined) {
            const assessmentClass = v.weight_assessment === '正常' ? 'good' : v.weight_assessment === '偏慢' ? 'warn' : 'alert';
            summaryHtml += `
                <div class="velocity-card ${assessmentClass}">
                    <div class="velocity-card-label">日均增重</div>
                    <div class="velocity-card-value">${(v.weight_velocity * 1000).toFixed(1)}g</div>
                    <div class="velocity-card-assessment">${v.weight_assessment}</div>
                </div>`;
        }
        if (v.height_velocity !== undefined) {
            const assessmentClass = v.height_assessment === '正常' ? 'good' : v.height_assessment === '偏慢' ? 'warn' : 'alert';
            summaryHtml += `
                <div class="velocity-card ${assessmentClass}">
                    <div class="velocity-card-label">日均长高</div>
                    <div class="velocity-card-value">${(v.height_velocity * 10).toFixed(2)}mm</div>
                    <div class="velocity-card-assessment">${v.height_assessment}</div>
                </div>`;
        }
        if (v.head_velocity !== undefined) {
            summaryHtml += `
                <div class="velocity-card">
                    <div class="velocity-card-label">日均头围</div>
                    <div class="velocity-card-value">${(v.head_velocity * 10).toFixed(2)}mm</div>
                    <div class="velocity-card-assessment">增长</div>
                </div>`;
        }
        summaryHtml += '</div>';
        summaryEl.innerHTML = summaryHtml;

        // 详细信息
        let detailsHtml = '<div class="velocity-detail-section">';
        detailsHtml += `<h4 class="subsection-title">近两次对比（间隔 ${v.days_diff} 天）</h4>`;
        detailsHtml += '<div class="velocity-detail-list">';

        if (v.weight_diff !== undefined) {
            const sign = v.weight_diff >= 0 ? '+' : '';
            detailsHtml += `
                <div class="velocity-detail-item">
                    <span class="detail-label">体重变化</span>
                    <span class="detail-value">${sign}${v.weight_diff} kg</span>
                </div>`;
        }
        if (v.height_diff !== undefined) {
            const sign = v.height_diff >= 0 ? '+' : '';
            detailsHtml += `
                <div class="velocity-detail-item">
                    <span class="detail-label">身高变化</span>
                    <span class="detail-value">${sign}${v.height_diff} cm</span>
                </div>`;
        }
        if (v.head_diff !== undefined) {
            const sign = v.head_diff >= 0 ? '+' : '';
            detailsHtml += `
                <div class="velocity-detail-item">
                    <span class="detail-label">头围变化</span>
                    <span class="detail-value">${sign}${v.head_diff} cm</span>
                </div>`;
        }
        detailsHtml += '</div>';

        // 总增长
        if (v.total_days) {
            detailsHtml += `<h4 class="subsection-title">累计增长（${v.total_days} 天）</h4>`;
            detailsHtml += '<div class="velocity-detail-list">';
            if (v.total_weight_gain !== undefined) {
                detailsHtml += `
                    <div class="velocity-detail-item">
                        <span class="detail-label">总体重增长</span>
                        <span class="detail-value">+${v.total_weight_gain} kg</span>
                    </div>`;
            }
            if (v.total_height_gain !== undefined) {
                detailsHtml += `
                    <div class="velocity-detail-item">
                        <span class="detail-label">总身高增长</span>
                        <span class="detail-value">+${v.total_height_gain} cm</span>
                    </div>`;
            }
            detailsHtml += '</div>';
        }

        // 参考标准
        detailsHtml += `<h4 class="subsection-title">参考标准</h4>`;
        detailsHtml += `<div class="velocity-reference"><p>0-3月：25-30g/天 | 3-6月：15-20g/天 | 6-12月：10-15g/天</p><p>身高：新生儿≈50cm，1岁≈75cm，2岁≈87cm</p></div>`;
        detailsHtml += '</div>';

        detailsEl.innerHTML = detailsHtml;
    });
}

// ==================== 记录-成长关联 ====================

function loadCorrelationData() {
    if (!App.currentBaby) return;
    const overviewEl = document.getElementById('correlationOverview');
    const feedingBody = document.getElementById('correlationFeedingBody');
    const sleepBody = document.getElementById('correlationSleepBody');
    const growthBody = document.getElementById('correlationGrowthBody');
    const insightsEl = document.getElementById('correlationInsights');

    overviewEl.innerHTML = '<p class="loading">分析中...</p>';

    api(`/api/babies/${App.currentBaby}/record-growth-correlation`).then(res => {
        if (!res.success) {
            overviewEl.innerHTML = '<p class="empty-tip">数据加载失败</p>';
            return;
        }
        const d = res.data;
        const feeding = d.feeding_30d;
        const sleep = d.sleep_30d;
        const trend = d.growth_trend;
        const latest = d.latest;

        // 概览卡片
        overviewEl.innerHTML = `
            <div class="correlation-overview-card">
                <div class="overview-item">
                    <span class="overview-value">${latest.weight ? latest.weight + ' kg' : '--'}</span>
                    <span class="overview-label">最新体重</span>
                </div>
                <div class="overview-item">
                    <span class="overview-value">${latest.height ? latest.height + ' cm' : '--'}</span>
                    <span class="overview-label">最新身高</span>
                </div>
                <div class="overview-item">
                    <span class="overview-value">${trend && trend.weight_velocity ? trend.weight_velocity + ' g/天' : '--'}</span>
                    <span class="overview-label">日均增重</span>
                </div>
                <div class="overview-item">
                    <span class="overview-value">${sleep.total_hours ? sleep.total_hours + ' h' : '--'}</span>
                    <span class="overview-label">30天睡眠</span>
                </div>
            </div>`;

        // 喂养统计
        feedingBody.innerHTML = `
            <div class="correlation-stat">
                <span class="stat-value">${feeding.total_count}</span>
                <span class="stat-label">总喂养次数</span>
            </div>
            <div class="correlation-stat">
                <span class="stat-value">${feeding.total_amount} ml</span>
                <span class="stat-label">总喂养量</span>
            </div>
            <div class="correlation-stat">
                <span class="stat-value">${feeding.avg_amount} ml</span>
                <span class="stat-label">平均每次</span>
            </div>
            <div class="correlation-stat">
                <span class="stat-value">${feeding.active_days}</span>
                <span class="stat-label">活跃天数</span>
            </div>
            <div class="correlation-stat">
                <span class="stat-value">${feeding.active_days > 0 ? (feeding.total_count / feeding.active_days).toFixed(1) : 0}</span>
                <span class="stat-label">日均次数</span>
            </div>`;

        // 睡眠统计
        sleepBody.innerHTML = `
            <div class="correlation-stat">
                <span class="stat-value">${sleep.total_hours} h</span>
                <span class="stat-label">总睡眠</span>
            </div>
            <div class="correlation-stat">
                <span class="stat-value">${sleep.total_sessions}</span>
                <span class="stat-label">睡眠次数</span>
            </div>
            <div class="correlation-stat">
                <span class="stat-value">${sleep.avg_session_minutes} min</span>
                <span class="stat-label">平均时长</span>
            </div>
            <div class="correlation-stat">
                <span class="stat-value">${(sleep.total_hours / 30).toFixed(1)} h</span>
                <span class="stat-label">日均睡眠</span>
            </div>`;

        // 近期增长
        if (trend && trend.weight_diff !== null) {
            const wSign = trend.weight_diff >= 0 ? '+' : '';
            const hSign = trend.height_diff >= 0 ? '+' : '';
            growthBody.innerHTML = `
                <div class="correlation-stat">
                    <span class="stat-value">${trend.previous_weight} → ${trend.latest_weight}</span>
                    <span class="stat-label">体重(kg)</span>
                </div>
                <div class="correlation-stat">
                    <span class="stat-value ${trend.weight_diff >= 0 ? 'positive' : 'negative'}">${wSign}${trend.weight_diff} kg</span>
                    <span class="stat-label">体重变化</span>
                </div>
                ${trend.height_diff !== null ? `
                <div class="correlation-stat">
                    <span class="stat-value">${trend.previous_height} → ${trend.latest_height}</span>
                    <span class="stat-label">身高(cm)</span>
                </div>
                <div class="correlation-stat">
                    <span class="stat-value ${trend.height_diff >= 0 ? 'positive' : 'negative'}">${hSign}${trend.height_diff} cm</span>
                    <span class="stat-label">身高变化</span>
                </div>` : ''}
                <div class="correlation-stat">
                    <span class="stat-value">${trend.days_diff} 天</span>
                    <span class="stat-label">间隔</span>
                </div>`;
        } else {
            growthBody.innerHTML = '<p class="empty-tip" style="padding:10px;font-size:12px;">需要至少2条成长记录才能分析';
        }

        // 智能洞察
        const insights = [];
        if (trend && trend.weight_velocity) {
            if (trend.weight_velocity >= 15) {
                insights.push({ type: 'good', text: '体重增长良好，喂养充足' });
            } else if (trend.weight_velocity >= 10) {
                insights.push({ type: 'normal', text: '体重增长正常' });
            } else {
                insights.push({ type: 'warn', text: '体重增长偏慢，建议增加喂养量或咨询医生' });
            }
        }
        if (sleep.total_hours / 30 >= 14) {
            insights.push({ type: 'good', text: '睡眠充足，有利于生长激素分泌' });
        } else if (sleep.total_hours / 30 >= 12) {
            insights.push({ type: 'normal', text: '睡眠量正常' });
        } else if (sleep.total_sessions > 0) {
            insights.push({ type: 'warn', text: '睡眠偏少，可能影响生长发育' });
        }
        if (feeding.active_days > 0) {
            const dailyAvg = feeding.total_amount / feeding.active_days;
            if (dailyAvg >= 600) {
                insights.push({ type: 'good', text: '日均喂养量充足' });
            } else if (dailyAvg >= 400) {
                insights.push({ type: 'normal', text: '日均喂养量适中' });
            } else if (dailyAvg > 0) {
                insights.push({ type: 'warn', text: '日均喂养量偏低' });
            }
        }

        if (insights.length > 0) {
            insightsEl.innerHTML = `
                <div class="insights-title">分析建议</div>
                <div class="insights-list">
                    ${insights.map(i => `<div class="insight-item insight-${i.type}">${i.text}</div>`).join('')}
                </div>`;
        } else {
            insightsEl.innerHTML = '<div class="insights-title">分析建议</div><p class="empty-tip" style="font-size:12px;">积累更多数据后将显示分析建议</p>';
        }
    }).catch(err => {
        console.error('加载关联数据失败:', err);
        overviewEl.innerHTML = '<p class="empty-tip">加载失败</p>';
    });
}

// ==================== 看诊摘要 ====================

function loadClinicSummary() {
    if (!App.currentBaby) return;

    api(`/api/babies/${App.currentBaby}/clinic-summary`).then(res => {
        if (!res.success) return;
        const data = res.data;
        const container = document.getElementById('clinicSummary');

        const genderMap = { boy: '男', girl: '女', other: '其他' };
        const ageText = getAgeText(data.age_days);

        let growthRows = '';
        data.growth_history.forEach(g => {
            growthRows += `<tr>
                <td>${g.record_date}</td>
                <td>${g.height || '--'}</td>
                <td>${g.weight || '--'}</td>
                <td>${g.bmi || '--'}</td>
            </tr>`;
        });

        let milestoneHtml = '';
        if (data.recent_milestones.length > 0) {
            milestoneHtml = data.recent_milestones.map(m =>
                `<div class="clinic-milestone-item"> ${escapeHtml(m.achieved_date)} ${escapeHtml(m.title)}</div>`
            ).join('');
        } else {
            milestoneHtml = '<p style="color:var(--text-light);font-size:12px;">暂无里程碑记录</p>';
        }

        // ---- 医生常问的四项：过敏史 / 近期体温 / 用药史 / 疫苗接种 ----
        const EMPTY_TIP = '<p style="color:var(--text-light);font-size:12px;">';

        const ALLERGEN_TYPE = { food: '食物', drug: '药物', environmental: '环境', other: '其他' };
        const ALLERGEN_SEVERITY = { mild: '轻度', moderate: '中度', severe: '重度', anaphylaxis: '过敏性休克' };
        const allergies = data.allergies || [];
        const allergyHtml = allergies.length ? allergies.map(a => {
            const heavy = (a.severity_level === 'severe' || a.severity_level === 'anaphylaxis');
            return `<div class="clinic-milestone-item">
                <b>${escapeHtml(a.allergen_name || '')}</b>
                <span class="clinic-tag${heavy ? ' clinic-tag-danger' : ''}">${escapeHtml(ALLERGEN_TYPE[a.allergen_type] || a.allergen_type || '')} · ${escapeHtml(ALLERGEN_SEVERITY[a.severity_level] || a.severity_level || '')}</span>
                ${a.reaction_detail ? `<div class="clinic-sub">${escapeHtml(a.reaction_detail)}</div>` : ''}
            </div>`;
        }).join('') : EMPTY_TIP + '暂无已知过敏史</p>';

        const TEMP_METHOD = { ear: '耳温', armpit: '腋温', oral: '口温', rectal: '肛温' };
        const temps = data.recent_temperatures || [];
        const tempHtml = temps.length ? temps.map(t =>
            `<div class="clinic-milestone-item">
                <b${t.is_fever ? ' class="clinic-danger"' : ''}>${escapeHtml(String(t.temperature))}°C</b>
                ${t.is_fever ? '<span class="clinic-tag clinic-tag-danger">发热</span>' : ''}
                <div class="clinic-sub">${escapeHtml(formatDateTime(t.measure_time))} · ${escapeHtml(TEMP_METHOD[t.measure_method] || t.measure_method || '')}${t.note ? ' · ' + escapeHtml(t.note) : ''}</div>
            </div>`
        ).join('') : EMPTY_TIP + '近期无体温记录</p>';

        const meds = data.recent_medications || [];
        const medHtml = meds.length ? meds.map(m =>
            `<div class="clinic-milestone-item">
                <b>${escapeHtml(m.medication_name || '')}</b>
                ${m.dosage ? `<span class="clinic-tag clinic-tag-muted">${escapeHtml(String(m.dosage))}${escapeHtml(m.dosage_unit || '')}</span>` : ''}
                <div class="clinic-sub">${escapeHtml(formatDateTime(m.measure_time))}${m.note ? ' · ' + escapeHtml(m.note) : ''}</div>
            </div>`
        ).join('') : EMPTY_TIP + '近期无用药记录</p>';

        const VAC_STATUS = { completed: '已接种', pending: '待接种', skipped: '已跳过', delayed: '已延期' };
        const vacs = data.vaccinations || [];
        const vacHtml = vacs.length ? vacs.map(v => {
            const dateText = v.actual_date ? `接种 ${v.actual_date}` : (v.scheduled_date ? `计划 ${v.scheduled_date}` : '');
            let reaction = '';
            if (v.has_reaction) {
                reaction = v.reaction_detail ? ` · 反应：${v.reaction_detail}` : ' · 有不良反应';
            }
            return `<div class="clinic-milestone-item">
                <b>${escapeHtml(v.vaccine_name || '')} 第${v.dose_number || 1}剂</b>
                <span class="clinic-tag${v.status === 'completed' ? '' : ' clinic-tag-muted'}">${escapeHtml(VAC_STATUS[v.status] || v.status || '')}</span>
                <div class="clinic-sub">${escapeHtml(dateText)}${escapeHtml(reaction)}</div>
            </div>`;
        }).join('') : EMPTY_TIP + '暂无疫苗记录</p>';

        container.innerHTML = `
            <div class="clinic-card">
                <div class="clinic-card-title"> 宝宝基本信息</div>
                <div class="clinic-baby-info">
                    <div class="clinic-baby-avatar"></div>
                    <div>
                        <div class="clinic-baby-name">${escapeHtml(data.baby.name)}</div>
                        <div class="clinic-baby-age">${ageText} | ${genderMap[data.baby.gender] || '未知'} | 出生: ${data.baby.birthday}</div>
                    </div>
                </div>
            </div>

            <div class="clinic-card">
                <div class="clinic-card-title"> 近7天统计</div>
                <div class="clinic-stats-grid">
                    <div class="clinic-stat">
                        <div class="clinic-stat-value">${data.week_feeding_count}</div>
                        <div class="clinic-stat-label">喂奶(次)</div>
                    </div>
                    <div class="clinic-stat">
                        <div class="clinic-stat-value">${Math.round(data.week_sleep_total_minutes / 60 * 10) / 10}</div>
                        <div class="clinic-stat-label">睡眠(小时)</div>
                    </div>
                    <div class="clinic-stat">
                        <div class="clinic-stat-value">${data.week_diaper_count}</div>
                        <div class="clinic-stat-label">换尿布(次)</div>
                    </div>
                </div>
            </div>

            <div class="clinic-card">
                <div class="clinic-card-title"> 成长趋势</div>
                ${data.growth_history.length > 0 ? `
                <table class="clinic-growth-table">
                    <thead>
                        <tr><th>日期</th><th>身高(cm)</th><th>体重(kg)</th><th>BMI</th></tr>
                    </thead>
                    <tbody>${growthRows}</tbody>
                </table>` : '<p style="color:var(--text-light);font-size:12px;">暂无成长记录</p>'}
            </div>

            <div class="clinic-card">
                <div class="clinic-card-title"> 过敏史</div>
                <div class="clinic-milestone-list">${allergyHtml}</div>
            </div>

            <div class="clinic-card">
                <div class="clinic-card-title"> 近期体温</div>
                <div class="clinic-milestone-list">${tempHtml}</div>
            </div>

            <div class="clinic-card">
                <div class="clinic-card-title"> 用药史</div>
                <div class="clinic-milestone-list">${medHtml}</div>
            </div>

            <div class="clinic-card">
                <div class="clinic-card-title"> 疫苗接种</div>
                <div class="clinic-milestone-list">${vacHtml}</div>
            </div>

            <div class="clinic-card">
                <div class="clinic-card-title"> 近期里程碑</div>
                <div class="clinic-milestone-list">${milestoneHtml}</div>
            </div>

            <div class="clinic-card">
                <div class="clinic-card-title"> 就医参考</div>
                <div class="clinic-action">
                    <button class="btn btn-primary" onclick="window.print()"> 打印报告</button>
                </div>
            </div>
        `;
    });
}

function getAgeText(days) {
    if (days < 30) return `${days}天`;
    if (days < 365) {
        const m = Math.floor(days / 30);
        const d = days % 30;
        return d > 0 ? `${m}个月${d}天` : `${m}个月`;
    }
    const y = Math.floor(days / 365);
    const m = Math.floor((days % 365) / 30);
    return m > 0 ? `${y}岁${m}个月` : `${y}岁`;
}

// ==================== 应用设置 ====================

function loadSettings() {
    api('/api/settings').then(res => {
        if (!res.success) return;
        const settings = res.data || {};

        const weightEl = document.getElementById('settingsWeightUnit');
        const heightEl = document.getElementById('settingsHeightUnit');
        const tempEl = document.getElementById('settingsTempUnit');
        const sizeEl = document.getElementById('settingsPageSize');

        if (weightEl && settings.weight_unit) weightEl.value = settings.weight_unit;
        if (heightEl && settings.height_unit) heightEl.value = settings.height_unit;
        if (tempEl && settings.temp_unit) tempEl.value = settings.temp_unit;
        if (sizeEl && settings.page_size) sizeEl.value = settings.page_size;
    });
    // 加载"保持登录状态"设置（默认关闭）
    const keepLogin = localStorage.getItem('babycare_keep_login') === '1';
    const keepLoginToggle = document.getElementById('keepLoginToggle');
    if (keepLoginToggle) keepLoginToggle.checked = keepLogin;
}

function initSettings() {
    const saveBtn = document.getElementById('saveSettingsBtn');
    if (saveBtn) {
        saveBtn.addEventListener('click', () => {
            const data = {
                weight_unit: document.getElementById('settingsWeightUnit')?.value || 'kg',
                height_unit: document.getElementById('settingsHeightUnit')?.value || 'cm',
                temp_unit: document.getElementById('settingsTempUnit')?.value || '°C',
                page_size: document.getElementById('settingsPageSize')?.value || '20'
            };

            api('/api/settings', {
                method: 'PUT',
                body: JSON.stringify(data)
            }).then(res => {
                // 保存"保持登录状态"设置到 localStorage
                const keepLoginToggle = document.getElementById('keepLoginToggle');
                if (keepLoginToggle) {
                    localStorage.setItem('babycare_keep_login', keepLoginToggle.checked ? '1' : '0');
                }
                const tip = document.getElementById('settingsTip');
                if (res.success) {
                    if (tip) {
                        tip.textContent = '设置已保存';
                        tip.style.display = 'block';
                        setTimeout(() => { tip.style.display = 'none'; }, 2000);
                    }
                    showToast.success('设置已保存');
                } else {
                    showToast.error(res.message || '保存失败');
                }
            });
        });
    }

    // LLM 配置初始化
    initLLMSettings();
}

// LLM 全局状态和配置
let llmProviders = {};  // 缓存厂商模板（全局唯一声明，勿重复声明）
let llmProviderGroups = [];  // 缓存厂商分组
const LLM_KEY_LINKS = {
    openai: 'https://platform.openai.com/api-keys',
    anthropic: 'https://console.anthropic.com/settings/keys',
    deepseek: 'https://platform.deepseek.com/api_keys',
    kimi: 'https://platform.moonshot.cn/console/api-keys',
    qwen: 'https://dashscope.console.aliyun.com/?apiKey=1',
    glm: 'https://open.bigmodel.cn/usercenter/apikeys',
    doubao: 'https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey',
    ollama: 'https://ollama.com/download',
    lmstudio: 'https://lmstudio.ai/'
};

const LLM_MODELS_FEATURED = {
    deepseek: ['deepseek-chat'],
    kimi: ['moonshot-v1-8k'],
    qwen: ['qwen-turbo', 'qwen-plus'],
    glm: ['glm-4-flash'],
    openai: ['gpt-4o-mini'],
    doubao: ['doubao-seed-1-8-250615'],
    ollama: ['qwen2.5:7b', 'qwen3:8b'],
    lmstudio: ['qwen2.5-7b-instruct', 'llama-3.1-8b-instruct']
};

// ==================== 推送通知设置 ====================

function initNotificationSettings() {
    // 加载推送配置
    loadNotificationConfig();

    // 保存按钮
    const saveBtn = document.getElementById('saveNotifyBtn');
    if (saveBtn) {
        saveBtn.addEventListener('click', saveNotificationConfig);
    }

    // 测试按钮
    const testBtn = document.getElementById('testNotifyBtn');
    if (testBtn) {
        testBtn.addEventListener('click', testNotification);
    }
}

function loadNotificationConfig() {
    api('/api/notifications/config').then(res => {
        if (!res.success) return;
        const cfg = res.config || {};

        // 填充表单
        const setVal = (id, val) => {
            const el = document.getElementById(id);
            if (el && val) el.value = val;
        };
        const setCheck = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.checked = !!val;
        };

        setCheck('notifyEnabled', cfg.reminder_enabled);
        setVal('notifyWechat', cfg.wechat_webhook_url);
        setVal('notifyDingtalk', cfg.dingtalk_webhook_url);
        setVal('notifyFeishu', cfg.feishu_webhook_url);
        setVal('notifyBark', cfg.bark_url);
        setVal('notifyPushplus', cfg.pushplus_token);
        setCheck('notifyDndEnabled', cfg.dnd_enabled);
        setVal('notifyDndStart', cfg.dnd_start_time);
        setVal('notifyDndEnd', cfg.dnd_end_time);
        setCheck('notifyFeedingEnabled', cfg.feeding_reminder_enabled);
        setVal('notifyFeedingInterval', cfg.feeding_reminder_interval);
        if (cfg.feeding_reminder_interval) {
            localStorage.setItem('feedingReminderHours', String(cfg.feeding_reminder_interval));
        }
        setCheck('notifyDiaperEnabled', cfg.diaper_reminder_enabled);
        setVal('notifyDiaperInterval', cfg.diaper_reminder_interval);
        setCheck('notifyMedEnabled', cfg.medication_reminder_enabled);
        setCheck('notifyVaccineEnabled', cfg.vaccine_reminder_enabled);
    });
}

function saveNotificationConfig() {
    const data = {
        reminder_enabled: document.getElementById('notifyEnabled')?.checked || false,
        wechat_webhook_url: document.getElementById('notifyWechat')?.value || '',
        dingtalk_webhook_url: document.getElementById('notifyDingtalk')?.value || '',
        feishu_webhook_url: document.getElementById('notifyFeishu')?.value || '',
        bark_url: document.getElementById('notifyBark')?.value || '',
        pushplus_token: document.getElementById('notifyPushplus')?.value || '',
        dnd_enabled: document.getElementById('notifyDndEnabled')?.checked || false,
        dnd_start_time: document.getElementById('notifyDndStart')?.value || '22:00',
        dnd_end_time: document.getElementById('notifyDndEnd')?.value || '07:00',
        feeding_reminder_enabled: document.getElementById('notifyFeedingEnabled')?.checked || false,
        feeding_reminder_interval: parseFloat(document.getElementById('notifyFeedingInterval')?.value) || 3,
        diaper_reminder_enabled: document.getElementById('notifyDiaperEnabled')?.checked || false,
        diaper_reminder_interval: parseFloat(document.getElementById('notifyDiaperInterval')?.value) || 2,
        medication_reminder_enabled: document.getElementById('notifyMedEnabled')?.checked || false,
        vaccine_reminder_enabled: document.getElementById('notifyVaccineEnabled')?.checked || false,
    };

    // 喂奶页顶部的提醒条也用这个间隔，存一份到本地免得每次去服务端取
    localStorage.setItem('feedingReminderHours', String(data.feeding_reminder_interval));

    api('/api/notifications/config', {
        method: 'POST',
        body: JSON.stringify(data)
    }).then(res => {
        const tip = document.getElementById('notifyTip');
        if (res.success) {
            if (tip) {
                tip.textContent = '推送设置已保存';
                tip.style.color = 'var(--success-color)';
                tip.style.display = 'block';
                setTimeout(() => { tip.style.display = 'none'; }, 3000);
            }
            showToast.success('推送设置已保存');
        } else {
            if (tip) {
                tip.textContent = '✗ ' + (res.message || '保存失败');
                tip.style.color = 'var(--danger-color)';
                tip.style.display = 'block';
            }
            showToast.error(res.message || '保存失败');
        }
    }).catch(err => {
        showToast.error('保存失败: ' + err.message);
    });
}

function testNotification() {
    const tip = document.getElementById('notifyTip');
    if (tip) {
        tip.textContent = '正在发送测试消息...';
        tip.style.color = 'var(--text-light)';
        tip.style.display = 'block';
    }

    api('/api/notifications/test', {
        method: 'POST',
        body: JSON.stringify({})
    }).then(res => {
        if (res.success) {
            if (tip) {
                tip.textContent = '测试消息已发送，请检查手机通知';
                tip.style.color = 'var(--success-color)';
                tip.style.display = 'block';
            }
            showToast.success('测试消息已发送');
        } else {
            if (tip) {
                tip.textContent = '✗ ' + (res.message || '发送失败');
                tip.style.color = 'var(--danger-color)';
                tip.style.display = 'block';
            }
            showToast.error(res.message || '发送失败');
        }
    }).catch(err => {
        if (tip) {
            tip.textContent = '✗ 发送失败: ' + err.message;
            tip.style.color = 'var(--danger-color)';
            tip.style.display = 'block';
        }
    });
}

// ==================== 大模型配置 ====================
// 注意：llmProviders 已在上方「LLM 全局状态和配置」处统一声明，此处勿重复声明
// （let 重复声明会导致整个 app.js 解析失败、全站 JS 失效）

function initLLMSettings() {
    // 加载厂商模板
    loadLLMProviders().then(() => {
        loadLLMSettings();
    });

    // 厂商切换 → 更新模型列表
    const providerSelect = document.getElementById('llmProvider');
    if (providerSelect) {
        providerSelect.addEventListener('change', () => {
            onProviderChange(providerSelect.value);
            updateQuickButtonsState(providerSelect.value);
            updateStatusBadge();
        });
    }

    // 快速配置按钮
    document.querySelectorAll('.llm-quick-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const preset = btn.dataset.preset;
            applyQuickPreset(preset);
        });
    });

    // API Key 显示/隐藏
    const togglePwdBtn = document.getElementById('toggleApiKeyBtn');
    if (togglePwdBtn) {
        togglePwdBtn.addEventListener('click', () => {
            const apiKeyInput = document.getElementById('llmApiKey');
            if (apiKeyInput) {
                const isPassword = apiKeyInput.type === 'password';
                apiKeyInput.type = isPassword ? 'text' : 'password';
                togglePwdBtn.textContent = isPassword ? '隐藏' : '显示';
            }
        });
    }

    // 高级设置折叠面板
    const advancedToggle = document.getElementById('llmAdvancedToggle');
    const advancedPanel = document.getElementById('llmAdvancedPanel');
    if (advancedToggle && advancedPanel) {
        advancedToggle.addEventListener('click', () => {
            const isExpanded = advancedToggle.classList.toggle('expanded');
            advancedPanel.style.display = isExpanded ? 'block' : 'none';
        });
    }

    // Temperature 滑块实时显示数值
    const tempRange = document.getElementById('llmTemperature');
    const tempVal = document.getElementById('llmTemperatureVal');
    if (tempRange && tempVal) {
        tempRange.addEventListener('input', () => { tempVal.textContent = tempRange.value; });
    }

    // 恢复默认系统提示词
    const resetPromptBtn = document.getElementById('resetSystemPromptBtn');
    if (resetPromptBtn) {
        resetPromptBtn.addEventListener('click', () => {
            const sp = document.getElementById('llmSystemPrompt');
            if (sp) {
                sp.value = '';
                showToast.success('已恢复默认提示词');
            }
        });
    }

// 保存
const saveBtn = document.getElementById('saveLLMBtn');
if (saveBtn) {
    saveBtn.addEventListener('click', () => {
        const provider = providerSelect?.value || '';
        if (!provider) {
            // 关闭 LLM
            saveLLMConfig({ provider: '', api_key: '', model: '', api_url: '', base_url: '', custom_models: [], temperature: 0.7, max_tokens: 0, timeout: 30, system_prompt: '', consent_cloud: false, vision_enabled: false, vision_model: '' });
            return;
        }

        const apiKey = document.getElementById('llmApiKey')?.value || '';
        const config = {
            provider: provider,
            api_key: apiKey,  // 后端会自动加密
            model: document.getElementById('llmModel')?.value || '',
            api_url: document.getElementById('llmApiUrl')?.value || '',
            base_url: document.getElementById('llmBaseUrl')?.value || '',
            custom_models: currentCustomModels || [],
            temperature: parseFloat(document.getElementById('llmTemperature')?.value || '0.7') || 0.7,
            max_tokens: parseInt(document.getElementById('llmMaxTokens')?.value || '0', 10) || 0,
            timeout: parseInt(document.getElementById('llmTimeout')?.value || '30', 10) || 30,
            system_prompt: document.getElementById('llmSystemPrompt')?.value || '',
            consent_cloud: document.getElementById('llmConsent')?.checked || false,
            vision_enabled: document.getElementById('llmVisionEnabled')?.checked || false,
            vision_model: document.getElementById('llmVisionModel')?.value || '',
        };

        saveLLMConfig(config);
    });
}

    // 初始化自定义模型事件
    initCustomModelEvents();

    // 测试
    const testBtn = document.getElementById('testLLMBtn');
    if (testBtn) {
        testBtn.addEventListener('click', testLLMConnection);
    }
}

function updateQuickButtonsState(activeProvider) {
    document.querySelectorAll('.llm-quick-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.preset === activeProvider);
    });
}

function updateStatusBadge() {
    const badge = document.getElementById('llmStatusBadge');
    const providerSelect = document.getElementById('llmProvider');
    if (!badge || !providerSelect) return;

    const provider = providerSelect.value;
    if (provider && llmProviders[provider]) {
        badge.textContent = llmProviders[provider].name;
        badge.classList.add('active');
    } else {
        badge.textContent = '规则引擎';
        badge.classList.remove('active');
    }
}

function applyQuickPreset(preset) {
    const providerSelect = document.getElementById('llmProvider');
    if (!providerSelect || !llmProviders[preset]) return;

    providerSelect.value = preset;
    onProviderChange(preset);
    updateQuickButtonsState(preset);
    updateStatusBadge();

    // 更新 API Key 链接
    const getKeyLink = document.getElementById('getKeyLink');
    if (getKeyLink && LLM_KEY_LINKS[preset]) {
        getKeyLink.href = LLM_KEY_LINKS[preset];
        getKeyLink.style.display = preset === 'ollama' ? 'none' : 'inline-block';
    }

    // 本地服务自动填充默认地址和获取模型
    if (preset === 'ollama') {
        const urlInput = document.getElementById('llmApiUrl');
        if (urlInput && !urlInput.value) {
            urlInput.value = 'http://localhost:11434/api/chat';
        }
        loadOllamaLocalModels();
    } else if (preset === 'lmstudio') {
        const urlInput = document.getElementById('llmApiUrl');
        if (urlInput && !urlInput.value) {
            urlInput.value = 'http://localhost:1234/v1/chat/completions';
        }
        loadLMStudioLocalModels();
    } else {
        // 云端厂商提示填写 API Key
        const apiKeyInput = document.getElementById('llmApiKey');
        if (apiKeyInput && !apiKeyInput.value) {
            apiKeyInput.focus();
            showToast('请填写 ' + llmProviders[preset].name + ' 的 API Key');
        }
    }
}

function loadLMStudioLocalModels() {
    const modelSelect = document.getElementById('llmModel');
    const modelHint = document.getElementById('llmModelHint');
    if (!modelSelect) return;

    // 加载状态
    modelSelect.innerHTML = '<option value="">正在检测本地模型...</option>';
    if (modelHint) modelHint.textContent = '正在连接 LM Studio 服务...';

    api('/api/ai/lmstudio/models').then(res => {
        if (res.success && res.models && res.models.length > 0) {
            // 有本地模型
            modelSelect.innerHTML = '';
            res.models.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m;
                opt.textContent = m;
                modelSelect.appendChild(opt);
            });
            if (modelHint) {
                modelHint.textContent = `检测到 ${res.models.length} 个 LM Studio 模型`;
            }
            showToast.success(`检测到 ${res.models.length} 个 LM Studio 模型`);
        } else {
            // 无本地模型或连接失败
            modelSelect.innerHTML = '';
            // 添加默认推荐
            const defaultModels = ['qwen2.5-7b-instruct', 'llama-3.1-8b-instruct', 'deepseek-r1-7b', 'gemma-2-9b-instruct', 'mistral-7b-instruct'];
            defaultModels.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m;
                opt.textContent = m;
                modelSelect.appendChild(opt);
            });
            if (modelHint) {
                modelHint.textContent = '未检测到本地模型，请在 LM Studio 中加载模型';
            }
            if (res.message) {
                showToast('LM Studio: ' + res.message);
            }
        }
    }).catch(() => {
        // 连接失败，使用默认列表
        modelSelect.innerHTML = '';
        const defaultModels = ['qwen2.5-7b-instruct', 'llama-3.1-8b-instruct', 'deepseek-r1-7b', 'gemma-2-9b-instruct', 'mistral-7b-instruct'];
        defaultModels.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m;
            opt.textContent = m;
            modelSelect.appendChild(opt);
        });
        if (modelHint) modelHint.textContent = '无法连接 LM Studio，使用默认模型列表';
    });
}

function loadOllamaLocalModels() {
    const modelSelect = document.getElementById('llmModel');
    const modelHint = document.getElementById('llmModelHint');
    if (!modelSelect) return;

    // 加载状态
    modelSelect.innerHTML = '<option value="">正在检测本地模型...</option>';
    if (modelHint) modelHint.textContent = '正在连接 Ollama 服务...';

    api('/api/ai/ollama/models').then(res => {
        if (res.success && res.models && res.models.length > 0) {
            // 有本地模型
            modelSelect.innerHTML = '';
            res.models.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m;
                opt.textContent = m;
                modelSelect.appendChild(opt);
            });
            if (modelHint) {
                modelHint.textContent = `检测到 ${res.models.length} 个本地模型`;
            }
            showToast.success(`检测到 ${res.models.length} 个 Ollama 本地模型`);
        } else {
            // 无本地模型或连接失败
            modelSelect.innerHTML = '';
            // 添加默认推荐
            const defaultModels = ['qwen2.5:7b', 'qwen3:8b', 'llama3.1:8b', 'deepseek-r1:7b'];
            defaultModels.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m;
                opt.textContent = m;
                modelSelect.appendChild(opt);
            });
            if (modelHint) {
                modelHint.textContent = '未检测到本地模型，请先在 Ollama 中安装模型';
            }
            if (res.message) {
                showToast('Ollama: ' + res.message);
            }
        }
    }).catch(() => {
        // 连接失败，使用默认列表
        modelSelect.innerHTML = '';
        const defaultModels = ['qwen2.5:7b', 'qwen3:8b', 'llama3.1:8b', 'deepseek-r1:7b'];
        defaultModels.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m;
            opt.textContent = m;
            modelSelect.appendChild(opt);
        });
        if (modelHint) modelHint.textContent = '无法连接 Ollama，使用默认模型列表';
    });
}

function loadLLMProviders() {
    return api('/api/ai/llm/providers').then(res => {
        if (!res.success) return;
        llmProviders = res.providers || {};
        llmProviderGroups = res.groups || [];

        const select = document.getElementById('llmProvider');
        if (!select) return;

        // 保留第一个"关闭"选项
        select.innerHTML = '<option value="">关闭（使用规则引擎）</option>';

        // 按分组添加提供商
        if (llmProviderGroups && llmProviderGroups.length > 0) {
            for (const group of llmProviderGroups) {
                const groupProviders = (group.providers || []).filter(id => llmProviders[id]);
                if (groupProviders.length === 0) continue;

                const optgroup = document.createElement('optgroup');
                optgroup.label = group.name;
                for (const id of groupProviders) {
                    const info = llmProviders[id];
                    const opt = document.createElement('option');
                    opt.value = id;
                    opt.textContent = info.name;
                    optgroup.appendChild(opt);
                }
                select.appendChild(optgroup);
            }
        } else {
            // 无分组时直接添加
            for (const [id, info] of Object.entries(llmProviders)) {
                const opt = document.createElement('option');
                opt.value = id;
                opt.textContent = info.name;
                select.appendChild(opt);
            }
        }
    }).catch(() => {/* 静默失败 */});
}

function onProviderChange(providerId) {
    const modelRow = document.getElementById('llmModelRow');
    const apiKeyRow = document.getElementById('llmApiKeyRow');
    const urlRow = document.getElementById('llmUrlRow');
    const baseUrlRow = document.getElementById('llmBaseUrlRow');
    const modelSelect = document.getElementById('llmModel');
    const modelHint = document.getElementById('llmModelHint');
    const modelTags = document.getElementById('llmModelTags');
    const modelHintText = document.getElementById('llmModelHintText');
    const getKeyLink = document.getElementById('getKeyLink');
    const consentRow = document.getElementById('llmConsentRow');
    const visionModelRow = document.getElementById('llmVisionModelRow');
    const visionSelectRow = document.getElementById('llmVisionSelectRow');
    const visionModelSelect = document.getElementById('llmVisionModel');
    const visionModelHint = document.getElementById('llmVisionModelHint');
    const customModelRow = document.getElementById('llmCustomModelRow');
    const apiUrlInput = document.getElementById('llmApiUrl');
    const apiUrlHint = document.getElementById('llmApiUrlHint');
    const baseUrlInput = document.getElementById('llmBaseUrl');

    if (!providerId) {
        modelRow.style.display = 'none';
        apiKeyRow.style.display = 'none';
        urlRow.style.display = 'none';
        if (baseUrlRow) baseUrlRow.style.display = 'none';
        if (consentRow) consentRow.style.display = 'none';
        if (visionModelRow) visionModelRow.style.display = 'none';
        if (visionSelectRow) visionSelectRow.style.display = 'none';
        if (customModelRow) customModelRow.style.display = 'none';
        return;
    }

    const provider = llmProviders[providerId];
    if (!provider) return;

    // 重置自定义模型列表（切换提供商时清空）
    currentCustomModels = [];

    // 更新文本模型下拉
    modelSelect.innerHTML = '';
    const featured = LLM_MODELS_FEATURED[providerId] || [];
    for (const model of provider.models) {
        const opt = document.createElement('option');
        opt.value = model;
        opt.textContent = model;
        modelSelect.appendChild(opt);
    }
    if (provider.default_model) {
        modelSelect.value = provider.default_model;
    }

    // 更新模型标签（推荐标记）
    if (modelTags) {
        modelTags.innerHTML = '';
        featured.forEach(m => {
            const tag = document.createElement('span');
            tag.className = 'llm-model-tag recommended';
            tag.textContent = '推荐 ' + m;
            modelTags.appendChild(tag);
        });
    }

    // 更新模型提示
    if (modelHint) {
        modelHint.textContent = featured.length > 0 ? '推荐: ' + featured.join(', ') : '选择具体使用的模型';
    }

    // 更新模型说明
    if (modelHintText && provider.model_hint) {
        modelHintText.textContent = provider.model_hint;
        modelHintText.style.display = 'block';
    } else if (modelHintText) {
        modelHintText.style.display = 'none';
    }

    // 更新视觉模型
    const visionModels = provider.vision_models || [];
    if (visionModelRow && visionSelectRow && visionModelSelect) {
        if (visionModels.length > 0) {
            visionModelRow.style.display = '';
            // 更新视觉模型下拉
            visionModelSelect.innerHTML = '';
            for (const model of visionModels) {
                const opt = document.createElement('option');
                opt.value = model;
                opt.textContent = model;
                visionModelSelect.appendChild(opt);
            }
            if (provider.default_vision_model) {
                visionModelSelect.value = provider.default_vision_model;
            }
            if (visionModelHint) {
                visionModelHint.textContent = '选择支持图片分析的模型';
            }
        } else {
            visionModelRow.style.display = 'none';
            visionSelectRow.style.display = 'none';
        }
    }

    // 更新 API Key 链接
    if (getKeyLink) {
        if (LLM_KEY_LINKS[providerId]) {
            getKeyLink.href = LLM_KEY_LINKS[providerId];
            const isLocal = providerId === 'ollama' || providerId === 'lmstudio';
            getKeyLink.style.display = isLocal ? 'none' : 'inline-block';
            getKeyLink.textContent = providerId === 'ollama' ? '下载 Ollama' : (providerId === 'lmstudio' ? '下载 LM Studio' : '获取 API Key');
        } else {
            getKeyLink.style.display = 'none';
        }
    }

    // 自动填充 API URL 和 Base URL
    if (apiUrlInput && provider.api_url) {
        apiUrlInput.value = provider.api_url;
        if (apiUrlHint) {
            apiUrlHint.textContent = '默认: ' + provider.api_url + '，可修改为代理地址';
        }
    }
    if (baseUrlInput && provider.base_url) {
        baseUrlInput.value = provider.base_url;
    }

    // 显示/隐藏相关字段
    modelRow.style.display = '';
    apiKeyRow.style.display = provider.needs_api_key ? '' : 'none';
    const isLocal = providerId === 'ollama' || providerId === 'lmstudio';
    urlRow.style.display = '';
    if (baseUrlRow) baseUrlRow.style.display = '';
    if (consentRow) {
        consentRow.style.display = isLocal ? 'none' : '';
    }

    // 显示自定义模型输入区域
    if (customModelRow) {
        customModelRow.style.display = '';
        renderCustomModelTags(providerId);
    }
}

function loadLLMSettings() {
    api('/api/ai/llm/status').then(res => {
        if (!res.success) return;
        const status = res.status || {};

        const providerSelect = document.getElementById('llmProvider');
        if (!providerSelect) return;

        // 更新状态徽章
        updateStatusBadge();

        if (status.enabled && status.provider) {
            providerSelect.value = status.provider;
            onProviderChange(status.provider);
            updateQuickButtonsState(status.provider);

            // 回填文本模型
            if (status.model) {
                const modelSelect = document.getElementById('llmModel');
                if (modelSelect) modelSelect.value = status.model;
            }
            // 回填视觉模型
            if (status.vision_model) {
                const visionModelSelect = document.getElementById('llmVisionModel');
                if (visionModelSelect) visionModelSelect.value = status.vision_model;
            }
            // 回填高级参数
            const t = document.getElementById('llmTemperature');
            if (t && status.temperature != null) { t.value = status.temperature; const tv = document.getElementById('llmTemperatureVal'); if (tv) tv.textContent = status.temperature; }
            const mt = document.getElementById('llmMaxTokens');
            if (mt && status.max_tokens != null) mt.value = status.max_tokens;
            const to = document.getElementById('llmTimeout');
            if (to && status.timeout != null) to.value = status.timeout;
            const sp = document.getElementById('llmSystemPrompt');
            if (sp && status.system_prompt != null) sp.value = status.system_prompt;
            const cs = document.getElementById('llmConsent');
            if (cs) cs.checked = !!status.consent_cloud;
            const ve = document.getElementById('llmVisionEnabled');
            if (ve) ve.checked = !!status.vision_enabled;

            // 回填 API Key 状态
            const apiKeyInput = document.getElementById('llmApiKey');
            const apiKeyStatus = document.getElementById('llmApiKeyStatus');
            const apiKeyMasked = document.getElementById('llmApiKeyMasked');
            if (status.api_key_configured) {
                if (apiKeyInput) apiKeyInput.placeholder = '已配置 API Key（输入新值以替换）';
                if (apiKeyStatus && apiKeyMasked) {
                    apiKeyMasked.textContent = status.api_key_masked || '****';
                    apiKeyStatus.style.display = 'block';
                }
            } else {
                if (apiKeyInput) apiKeyInput.placeholder = '输入 API Key（本地 AES-256 加密存储）';
                if (apiKeyStatus) apiKeyStatus.style.display = 'none';
            }

            // 回填自定义地址和 Base URL
            const urlInput = document.getElementById('llmApiUrl');
            if (urlInput && status.api_url) {
                urlInput.value = status.api_url;
            }
            const baseUrlInput = document.getElementById('llmBaseUrl');
            if (baseUrlInput && status.base_url) {
                baseUrlInput.value = status.base_url;
            }

            // 加载自定义模型列表
            if (status.custom_models && status.custom_models.length > 0) {
                currentCustomModels = status.custom_models;
                renderCustomModelTags(status.provider);
            }

            // 本地服务加载本地模型列表
            if (status.provider === 'ollama') {
                loadOllamaLocalModels();
            } else if (status.provider === 'lmstudio') {
                loadLMStudioLocalModels();
            }
        } else {
            providerSelect.value = '';
            onProviderChange('');
            updateQuickButtonsState('');
        }
    }).catch(() => {/* 静默失败 */});
}

function _llmConsentGate(config) {
    // 云端厂商需用户明确同意数据外发（后端会校验 consent_cloud 字段）
    // 本地服务（Ollama、LM Studio）不需要同意
    if (!config || !config.provider || config.provider === 'ollama' || config.provider === 'lmstudio') {
        return Promise.resolve(Object.assign({}, config, { consent_cloud: false }));
    }
    if (config.consent_cloud === true) {
        return Promise.resolve(config);
    }
    const agreed = window.confirm(
        '启用在线 AI 服务须知：\n\n' +
        '· 您提出的问题、最近聊天记录（可能包含宝宝身高等分析信息）将发送至所选厂商服务器\n' +
        '· 建议优先使用本地 Ollama 方案以保护隐私\n' +
        '· 可随时在设置中关闭此功能\n\n' +
        '是否已阅读并同意，继续启用？'
    );
    return Promise.resolve(agreed ? Object.assign({}, config, { consent_cloud: true }) : null);
}

function saveLLMConfig(config) {
    _llmConsentGate(config).then(gated => {
        if (!gated) { showToast('已取消：未同意数据外发条款'); return; }
        api('/api/ai/llm/configure', {
            method: 'POST',
            body: JSON.stringify(gated)
        }).then(res => {
            const tip = document.getElementById('llmTip');
            if (res.success) {
                if (tip) {
                    tip.textContent = gated.provider ? '大模型配置已保存' : '已切换到规则引擎';
                    tip.style.color = 'var(--success-color)';
                    tip.style.display = 'block';
                    setTimeout(() => { tip.style.display = 'none'; }, 2000);
                }
                showToast.success('配置已保存');
            } else {
                showToast.error(res.message || '保存失败');
            }
        }).catch(() => {
            showToast.error('保存失败，请检查网络');
        });
    });
}

// ==================== 自定义模型管理 ====================

let currentCustomModels = [];  // 当前提供商的自定义模型列表

function renderCustomModelTags(providerId) {
    const tagsContainer = document.getElementById('llmCustomModelTags');
    if (!tagsContainer) return;
    tagsContainer.innerHTML = '';

    if (!currentCustomModels || currentCustomModels.length === 0) {
        tagsContainer.innerHTML = '<span style="color:var(--text-muted);font-size:12px;">暂无自定义模型</span>';
        return;
    }

    currentCustomModels.forEach(model => {
        const tag = document.createElement('span');
        tag.className = 'llm-custom-model-tag';
        tag.innerHTML = `
            <span class="llm-custom-model-name">${model}</span>
            <button type="button" class="llm-custom-model-remove" data-model="${model}" title="移除">×</button>
        `;
        tagsContainer.appendChild(tag);
    });

    // 绑定移除事件
    tagsContainer.querySelectorAll('.llm-custom-model-remove').forEach(btn => {
        btn.addEventListener('click', () => {
            const modelToRemove = btn.dataset.model;
            removeCustomModel(modelToRemove, providerId);
        });
    });
}

function addCustomModel(modelName, providerId) {
    const name = modelName.trim();
    if (!name) return false;

    // 检查是否已存在
    if (currentCustomModels.includes(name)) {
        showToast('该模型已存在');
        return false;
    }

    // 添加到列表
    currentCustomModels.push(name);
    renderCustomModelTags(providerId);

    // 添加到下拉框
    const modelSelect = document.getElementById('llmModel');
    if (modelSelect) {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name + ' (自定义)';
        modelSelect.appendChild(opt);
        modelSelect.value = name;
    }

    showToast.success('已添加自定义模型: ' + name);
    return true;
}

function removeCustomModel(modelName, providerId) {
    // 从列表中移除
    currentCustomModels = currentCustomModels.filter(m => m !== modelName);
    renderCustomModelTags(providerId);

    // 从下拉框移除
    const modelSelect = document.getElementById('llmModel');
    if (modelSelect) {
        const opt = modelSelect.querySelector(`option[value="${modelName}"]`);
        if (opt) opt.remove();
    }

    showToast('已移除: ' + modelName);
}

function initCustomModelEvents() {
    const addBtn = document.getElementById('addCustomModelBtn');
    const input = document.getElementById('llmCustomModelInput');
    const providerSelect = document.getElementById('llmProvider');

    if (addBtn && input) {
        addBtn.addEventListener('click', () => {
            const providerId = providerSelect?.value || 'custom';
            if (addCustomModel(input.value, providerId)) {
                input.value = '';
            }
        });

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const providerId = providerSelect?.value || 'custom';
                if (addCustomModel(input.value, providerId)) {
                    input.value = '';
                }
            }
        });
    }
}

function testLLMConnection() {
    const provider = document.getElementById('llmProvider')?.value;
    const tip = document.getElementById('llmTip');
    const testResult = document.getElementById('llmTestResult');
    const testResultIcon = document.getElementById('llmTestResultIcon');
    const testResultText = document.getElementById('llmTestResultText');
    const testResultDetail = document.getElementById('llmTestResultDetail');

    if (!provider) {
        if (tip) {
            tip.textContent = '请先选择厂商';
            tip.style.color = 'var(--error-color, #dc3545)';
            tip.style.display = 'block';
        }
        if (testResult) testResult.style.display = 'none';
        return;
    }

    if (tip) {
        tip.textContent = '测试中...';
        tip.style.color = 'var(--text-light)';
        tip.style.display = 'block';
    }
    if (testResult) {
        testResult.className = 'llm-test-result loading';
        testResultIcon.textContent = '⏳';
        testResultText.textContent = '正在连接...';
        testResultDetail.textContent = '';
        testResult.style.display = 'block';
    }

    // 构建测试配置
    const config = {
        provider: provider,
        api_key: document.getElementById('llmApiKey')?.value || '',
        model: document.getElementById('llmModel')?.value || '',
        api_url: document.getElementById('llmApiUrl')?.value || '',
        base_url: document.getElementById('llmBaseUrl')?.value || '',
        timeout: parseInt(document.getElementById('llmTimeout')?.value || '15', 10),
    };

    // 云端厂商需先通过同意门控
    if (provider !== 'ollama' && provider !== 'lmstudio' && provider !== 'custom') {
        const consent = document.getElementById('llmConsent')?.checked;
        if (!consent) {
            if (tip) {
                tip.textContent = '请先勾选同意数据外发条款';
                tip.style.color = 'var(--warning-color, #f0ad4e)';
            }
            if (testResult) {
                testResult.className = 'llm-test-result warning';
                testResultIcon.textContent = '⚠';
                testResultText.textContent = '需要同意条款';
                testResultDetail.textContent = '云端厂商需要勾选"同意数据外发至所选厂商"才能测试';
            }
            return;
        }
    }

    // 调用测试端点
    api('/api/ai/llm/test', {
        method: 'POST',
        body: JSON.stringify(config)
    }).then(res => {
        if (res.success) {
            if (tip) {
                tip.textContent = res.message || '连接成功';
                tip.style.color = 'var(--success-color)';
            }
            if (testResult) {
                testResult.className = 'llm-test-result success';
                testResultIcon.textContent = '✓';
                testResultText.textContent = '连接成功';
                let detail = res.message || '';
                if (res.models && res.models.length > 0) {
                    detail += ` | 可用模型: ${res.models.slice(0, 5).join(', ')}`;
                    if (res.models.length > 5) detail += ` 等${res.models.length}个`;
                }
                if (res.reply) detail += ` | 回复: ${res.reply}`;
                testResultDetail.textContent = detail;
            }
        } else {
            if (tip) {
                tip.textContent = res.message || '连接失败';
                tip.style.color = 'var(--error-color, #dc3545)';
            }
            if (testResult) {
                testResult.className = 'llm-test-result error';
                testResultIcon.textContent = '✕';
                testResultText.textContent = '连接失败';
                testResultDetail.textContent = res.message || '未知错误';
            }
        }
    }).catch(err => {
        if (tip) {
            tip.textContent = '网络错误，请检查 API 地址';
            tip.style.color = 'var(--error-color, #dc3545)';
        }
        if (testResult) {
            testResult.className = 'llm-test-result error';
            testResultIcon.textContent = '✕';
            testResultText.textContent = '网络错误';
            testResultDetail.textContent = err.message || '无法连接到服务器';
        }
    });
}

// ==================== 计时器 ====================

let timerInterval = null;
let activeTimerId = null;
let timerStartTime = null;

function initTimerBar() {
    document.getElementById('timerBarStop').addEventListener('click', stopActiveTimer);

    // 定期刷新活跃计时器
    setInterval(updateTimerDisplay, 1000);

    // 页面加载时检查是否有活跃的计时器
    checkActiveTimers();
}

// ==================== AI 助手 ====================

function loadLLMStatus() {
    api('/api/ai/llm/status').then(res => {
        if (!res.success) return;
        const status = res.status || {};
        const badge = document.getElementById('aiLLMBadge');
        if (!badge) return;

        if (status.enabled) {
            badge.textContent = ` ${status.provider_name || status.model || 'LLM'}`;
            badge.style.background = 'linear-gradient(135deg, #11998e 0%, #38ef7d 100%)';
        } else {
            badge.textContent = ' 规则引擎';
            badge.style.background = 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';
        }
    }).catch(() => {
        const badge = document.getElementById('aiLLMBadge');
        if (badge) badge.textContent = ' 规则引擎';
    });
}

function showLLMIndicator(model) {
    const badge = document.getElementById('aiLLMBadge');
    if (badge && model) {
        badge.textContent = ` ${model}`;
        badge.style.background = 'linear-gradient(135deg, #11998e 0%, #38ef7d 100%)';
    }
}

function initAIChat() {
    // 防止重复绑定事件
    if (App.aiChatInitialized) {
        loadLLMStatus();  // 每次进入页面刷新 LLM 状态
        return;
    }
    App.aiChatInitialized = true;

    const sendBtn = document.getElementById('aiSendBtn');
    const input = document.getElementById('aiChatInput');

    if (!sendBtn || !input) return;

    // 加载 LLM 状态
    loadLLMStatus();

    // 初始化对话内模型选择器
    initChatModelSelect();

    // 发送消息
    const sendMessage = () => {
        const message = input.value.trim();
        if (!message) return;

        // 添加用户消息
        addChatMessage('user', message);
        input.value = '';

        // 显示加载状态
        const loadingId = addLoadingMessage();

        // 调用 AI 接口（携带对话内选择的回答模型）
        const modelSelect = document.getElementById('aiModelSelect');
        api('/api/ai/chat', {
            method: 'POST',
            body: JSON.stringify({
                message: message,
                baby_id: App.currentBaby,
                model: modelSelect ? modelSelect.value : ''
            })
        }).then(res => {
            removeLoadingMessage(loadingId);
            if (res.success) {
                addChatMessage('bot', res.answer);
                // 显示回答来源徽章
                if (res.llm_enabled) {
                    showLLMIndicator(res.llm_model);
                } else {
                    loadLLMStatus();
                }
            } else {
                addChatMessage('bot', '抱歉，我暂时无法回答这个问题。请稍后再试。');
            }
        }).catch(() => {
            removeLoadingMessage(loadingId);
            addChatMessage('bot', '网络错误，请检查连接后重试。');
        });
    };

    sendBtn.addEventListener('click', sendMessage);
    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });

    // 快捷问题按钮 + 常见问题 chips（点击直接发送并回复）
    document.querySelectorAll('.ai-quick-btn, .ai-faq-chip').forEach(btn => {
        if (btn.id) return;  // 一键分析/清除历史等已有专门绑定
        btn.addEventListener('click', () => {
            const question = btn.dataset.question;
            if (question) {
                input.value = question;
                sendMessage();
            }
        });
    });

    // 一键分析按钮
    const analyzeBtn = document.getElementById('aiAnalyzeBtn');
    if (analyzeBtn) {
        analyzeBtn.addEventListener('click', runAIAnalysis);
    }

    // 关闭分析面板
    const closeAnalysis = document.getElementById('aiAnalysisClose');
    if (closeAnalysis) {
        closeAnalysis.addEventListener('click', () => {
            document.getElementById('aiAnalysisPanel').style.display = 'none';
        });
    }

    // 清除历史按钮
    const clearBtn = document.getElementById('aiClearHistoryBtn');
    if (clearBtn) {
        clearBtn.addEventListener('click', clearChatHistory);
    }

    // 加载聊天历史
    loadChatHistory();
}

function fillChatModelSelect(sel) {
    if (!sel) return;
    const prev = App.aiChatModel || '';
    sel.innerHTML = '';
    sel.add(new Option('跟随设置（默认）', ''));
    sel.add(new Option('规则引擎（本地，无需联网）', '__rule__'));

    // 拉取当前配置的提供商及可用模型列表
    Promise.all([api('/api/ai/llm/status'), api('/api/ai/llm/providers')]).then(([stRes, prRes]) => {
        const st = (stRes && stRes.status) || {};
        const providers = (prRes && prRes.providers) || {};
        if (!st.enabled || !st.provider || !providers[st.provider]) return;
        let models = (providers[st.provider].models || []).slice();
        // 本地部署优先展示已安装/已加载的模型
        if (Array.isArray(st.local_models) && st.local_models.length) {
            models = st.local_models.slice();
        }
        if (!models.length) return;
        const group = document.createElement('optgroup');
        group.label = st.provider_name || '大模型';
        models.forEach(m => group.appendChild(new Option(m, m)));
        sel.appendChild(group);
    }).catch(() => {});

    sel.value = prev;
    if (sel.value !== prev) sel.value = '';
    sel.addEventListener('change', () => { App.aiChatModel = sel.value; });
}

function initChatModelSelect() {
    fillChatModelSelect(document.getElementById('aiModelSelect'));
}

function addChatMessage(role, content) {
    const container = document.getElementById('aiChatMessages');
    if (!container) return;

    const messageDiv = document.createElement('div');
    // analysis 类型使用特殊样式
    const cssClass = role === 'analysis' ? 'ai-message ai-message-analysis' : `ai-message ai-message-${role}`;
    messageDiv.className = cssClass;

    const avatar = document.createElement('div');
    avatar.className = 'ai-message-avatar';
    // 根据角色显示不同头像
    let avatarIcon = '';
    if (role === 'user') avatarIcon = '';
    else if (role === 'analysis') avatarIcon = '';
    avatar.textContent = avatarIcon;

    const contentDiv = document.createElement('div');
    contentDiv.className = 'ai-message-content';
    // 支持换行
    content.split('\n').forEach(line => {
        const p = document.createElement('p');
        p.textContent = line || ' ';
        contentDiv.appendChild(p);
    });

    messageDiv.appendChild(avatar);
    messageDiv.appendChild(contentDiv);
    container.appendChild(messageDiv);

    // 滚动到底部
    container.scrollTop = container.scrollHeight;
}

function addLoadingMessage() {
    const container = document.getElementById('aiChatMessages');
    if (!container) return null;

    const id = 'loading-' + Date.now();
    const messageDiv = document.createElement('div');
    messageDiv.className = 'ai-message ai-message-bot';
    messageDiv.id = id;

    messageDiv.innerHTML = `
        <div class="ai-message-avatar"></div>
        <div class="ai-message-content">
            <div class="ai-typing">
                <span></span><span></span><span></span>
            </div>
        </div>
    `;

    container.appendChild(messageDiv);
    container.scrollTop = container.scrollHeight;
    return id;
}

function removeLoadingMessage(id) {
    if (!id) return;
    const el = document.getElementById(id);
    if (el) el.remove();
}

function runAIAnalysis() {
    if (!App.currentBaby) {
        showToast.warning('请先添加宝宝');
        return;
    }

    const panel = document.getElementById('aiAnalysisPanel');
    const content = document.getElementById('aiAnalysisContent');
    panel.style.display = 'block';
    content.innerHTML = '<div class="ai-loading"><span></span><span></span><span></span><p>正在分析数据...</p></div>';

    api(`/api/ai/analyze/${App.currentBaby}`).then(res => {
        if (!res.success) {
            content.innerHTML = `<p class="empty-tip">${escapeHtml(res.message || '分析失败')}</p>`;
            return;
        }

        const insights = res.insights || [];
        const summary = res.summary || {};

        let html = `
            <div class="ai-analysis-summary">
                <div class="ai-summary-item">
                    <span class="ai-summary-num">${summary.total_growth_records || 0}</span>
                    <span class="ai-summary-label">生长记录</span>
                </div>
                <div class="ai-summary-item">
                    <span class="ai-summary-num">${summary.total_feeding_records || 0}</span>
                    <span class="ai-summary-label">喂养记录</span>
                </div>
                <div class="ai-summary-item">
                    <span class="ai-summary-num">${summary.total_sleep_records || 0}</span>
                    <span class="ai-summary-label">睡眠记录</span>
                </div>
            </div>
            <div class="ai-insights-list">
        `;

        if (insights.length === 0) {
            html += '<p class="empty-tip">暂无分析建议，请先添加更多数据记录。</p>';
        } else {
            insights.forEach(insight => {
                const icon = insight.type === 'success' ? '[OK]' : insight.type === 'warning' ? '[!]' : '[i]';
                html += `
                    <div class="ai-insight-item ai-insight-${insight.type}">
                        <div class="ai-insight-icon">${icon}</div>
                        <div class="ai-insight-body">
                            <div class="ai-insight-title">${escapeHtml(insight.title)}</div>
                            <div class="ai-insight-content">${escapeHtml(insight.content)}</div>
                        </div>
                    </div>
                `;
            });
        }

        html += '</div>';
        content.innerHTML = html;
    }).catch(() => {
        content.innerHTML = '<p class="empty-tip">网络错误，请重试</p>';
    });
}

// ==================== 聊天历史 UI ====================

function loadChatHistory() {
    if (!App.currentBaby) return;

    api(`/api/ai/chat/history/${App.currentBaby}`).then(res => {
        if (!res.success) return;
        const history = res.history || [];
        const container = document.getElementById('aiChatMessages');
        if (!container) return;

        if (history.length === 0) return;  // 保持默认欢迎消息

        // 清空并重新渲染历史
        container.innerHTML = '';
        history.forEach(msg => {
            addChatMessage(msg.role, msg.content);
        });
    }).catch(() => {/* 静默失败 */});
}

function clearChatHistory() {
    if (!App.currentBaby) return;
    if (!confirm('确定要清除所有聊天记录吗？')) return;

    api(`/api/ai/chat/history/${App.currentBaby}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast('聊天记录已清除');
            // 重置聊天页面
            App.aiChatInitialized = false;
            const container = document.getElementById('aiChatMessages');
            if (container) {
                container.innerHTML = `
                    <div class="ai-message ai-message-bot">
                        <div class="ai-message-avatar"></div>
                        <div class="ai-message-content">
                            <p>你好！我是育儿宝 AI 助手。</p>
                            <p>我可以帮你分析宝宝的生长发育数据、提供喂养和睡眠建议、指导辅食添加等。</p>
                            <p>请直接输入你的问题，或者点击左侧快捷问题。</p>
                        </div>
                    </div>
                `;
            }
        }
    }).catch(() => {
        showToast('清除失败');
    });
}

function startNewTimer(type, name) {
    if (!App.currentBaby) return;

    api(`/api/babies/${App.currentBaby}/timers`, {
        method: 'POST',
        body: JSON.stringify({ timer_type: type, name: name })
    }).then(res => {
        if (res.success) {
            showToast('计时器已启动');
            showTimerBar(res.data);
        }
    });
}

function showTimerBar(timer) {
    activeTimerId = timer.id;
    activeTimerType = timer.timer_type;
    timerStartTime = new Date(timer.start_time);

    const icons = { feeding: '', sleep: '', tummy: '' };
    const names = { feeding: '喂奶计时', sleep: '睡眠计时', tummy: '俯卧计时' };

    document.getElementById('timerBarIcon').textContent = icons[timer.timer_type] || '⏱';
    document.getElementById('timerBarName').textContent = names[timer.timer_type] || timer.name || '计时中';
    document.getElementById('timerBar').classList.add('active');

    updateTimerDisplay();
}

function updateTimerDisplay() {
    if (!timerStartTime) return;
    const elapsed = Math.floor((new Date() - timerStartTime) / 1000);
    const mins = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const secs = String(elapsed % 60).padStart(2, '0');
    const el = document.getElementById('timerBarDuration');
    if (el) el.textContent = `${mins}:${secs}`;
}

function stopActiveTimer() {
    if (!activeTimerId) return;
    const timerType = activeTimerType;
    const startTime = timerStartTime;
    const babyId = App.currentBaby;

    api(`/api/timers/${activeTimerId}/stop`, { method: 'POST' }).then(res => {
        if (res.success) {
            showToast('计时器已停止');
            // 睡眠计时结束时自动生成一条睡眠记录，让"计时"真正落到记录里
            if (timerType === 'sleep' && startTime && babyId) {
                api(`/api/babies/${babyId}/sleep`, {
                    method: 'POST',
                    body: JSON.stringify({
                        start_time: toLocalStr(startTime),
                        end_time: nowLocalStr(),
                        is_nap: 0,
                        sleep_quality: null,
                        note: '计时器记录'
                    })
                }).then(r => {
                    if (r.success) {
                        showToast.success('睡眠记录已保存');
                    } else {
                        showToast.error(r.message || '睡眠记录保存失败');
                    }
                });
            }
        }
        activeTimerId = null;
        timerStartTime = null;
        activeTimerType = null;
        document.getElementById('timerBar').classList.remove('active');

        // 刷新当前页面
        if (App.currentPage === 'dashboard') loadDashboard();
        if (App.currentPage === 'timeline') loadTimeline();
        if (App.currentPage === 'feeding') loadFeedingPage();
        if (App.currentPage === 'pumping') loadPumpingPage();
        if (App.currentPage === 'sleep-record') loadSleepPage();
        if (App.currentPage === 'diaper-record') loadDiaperPage();
    });
}

function checkActiveTimers() {
    if (!App.currentBaby) return;

    api(`/api/babies/${App.currentBaby}/timers`).then(res => {
        if (res.success && res.data.length > 0) {
            showTimerBar(res.data[0]);
        }
    });
}

// ==================== 母乳左右侧计时器 ====================

let leftTimerSeconds = 0;
let rightTimerSeconds = 0;
let leftTimerRunning = false;
let rightTimerRunning = false;
let leftTimerInterval = null;
let rightTimerInterval = null;

function initSideTimers() {
    // 左侧计时器
    document.getElementById('leftTimerStart')?.addEventListener('click', function() {
        if (rightTimerRunning) {
            stopRightTimer();
        }
        startLeftTimer();
    });
    document.getElementById('leftTimerStop')?.addEventListener('click', stopLeftTimer);

    // 右侧计时器
    document.getElementById('rightTimerStart')?.addEventListener('click', function() {
        if (leftTimerRunning) {
            stopLeftTimer();
        }
        startRightTimer();
    });
    document.getElementById('rightTimerStop')?.addEventListener('click', stopRightTimer);
}

function startLeftTimer() {
    leftTimerRunning = true;
    const leftStart = document.getElementById('leftTimerStart');
    const leftStop = document.getElementById('leftTimerStop');
    const leftBox = document.getElementById('leftTimerBox');
    if (leftStart) leftStart.style.display = 'none';
    if (leftStop) leftStop.style.display = 'inline-block';
    if (leftBox) leftBox.classList.add('timing');

    leftTimerInterval = setInterval(() => {
        leftTimerSeconds++;
        updateSideTimerDisplay('left', leftTimerSeconds);
        updateSideTimerTotal();
    }, 1000);
}

function stopLeftTimer() {
    leftTimerRunning = false;
    clearInterval(leftTimerInterval);
    const leftStart = document.getElementById('leftTimerStart');
    const leftStop = document.getElementById('leftTimerStop');
    const leftBox = document.getElementById('leftTimerBox');
    if (leftStart) leftStart.style.display = 'inline-block';
    if (leftStop) leftStop.style.display = 'none';
    if (leftBox) leftBox.classList.remove('timing');
}

function startRightTimer() {
    rightTimerRunning = true;
    const rightStart = document.getElementById('rightTimerStart');
    const rightStop = document.getElementById('rightTimerStop');
    const rightBox = document.getElementById('rightTimerBox');
    if (rightStart) rightStart.style.display = 'none';
    if (rightStop) rightStop.style.display = 'inline-block';
    if (rightBox) rightBox.classList.add('timing');

    rightTimerInterval = setInterval(() => {
        rightTimerSeconds++;
        updateSideTimerDisplay('right', rightTimerSeconds);
        updateSideTimerTotal();
    }, 1000);
}

function stopRightTimer() {
    rightTimerRunning = false;
    clearInterval(rightTimerInterval);
    const rightStart = document.getElementById('rightTimerStart');
    const rightStop = document.getElementById('rightTimerStop');
    const rightBox = document.getElementById('rightTimerBox');
    if (rightStart) rightStart.style.display = 'inline-block';
    if (rightStop) rightStop.style.display = 'none';
    if (rightBox) rightBox.classList.remove('timing');
}

function updateSideTimerDisplay(side, seconds) {
    const mins = String(Math.floor(seconds / 60)).padStart(2, '0');
    const secs = String(seconds % 60).padStart(2, '0');
    const el = document.getElementById(side + 'TimerDisplay');
    if (el) el.textContent = `${mins}:${secs}`;
}

function updateSideTimerTotal() {
    const total = leftTimerSeconds + rightTimerSeconds;
    const mins = String(Math.floor(total / 60)).padStart(2, '0');
    const secs = String(total % 60).padStart(2, '0');
    const el = document.getElementById('sideTimerTotal');
    if (el) el.textContent = `${mins}:${secs}`;
}

function resetSideTimers() {
    stopLeftTimer();
    stopRightTimer();
    leftTimerSeconds = 0;
    rightTimerSeconds = 0;
    updateSideTimerDisplay('left', 0);
    updateSideTimerDisplay('right', 0);
    updateSideTimerTotal();
}

// ==================== 吸奶计时器（支持单侧/分开计时/双侧同时） ====================
let pumpLeftSeconds = 0;
let pumpRightSeconds = 0;
let pumpLeftRunning = false;
let pumpRightRunning = false;
let pumpLeftInterval = null;
let pumpRightInterval = null;
let simSeconds = 0;
let simRunning = false;
let simLeftStopped = false;
let simRightStopped = false;
let simInterval = null;

function initPumpSideTimers() {
    // 模式切换
    const modeSelect = document.getElementById('pumpMode');
    if (modeSelect && !modeSelect._bound) { modeSelect._bound = true; modeSelect.addEventListener('change', onPumpModeChange); }

    // 单侧计时器
    const ss = document.getElementById('singleTimerStart');
    const sstop = document.getElementById('singleTimerStop');
    const sreset = document.getElementById('singleTimerReset');
    if (ss && !ss._bound) { ss._bound = true; ss.addEventListener('click', startSingleTimer); }
    if (sstop && !sstop._bound) { sstop._bound = true; sstop.addEventListener('click', stopSingleTimer); }
    if (sreset && !sreset._bound) { sreset._bound = true; sreset.addEventListener('click', resetSingleTimer); }

    // 双侧同时计时器
    const simStart = document.getElementById('simTimerStart');
    const simStop = document.getElementById('simTimerStop');
    const simReset = document.getElementById('simTimerReset');
    const simLeftStop = document.getElementById('simLeftStop');
    const simRightStop = document.getElementById('simRightStop');
    if (simStart && !simStart._bound) { simStart._bound = true; simStart.addEventListener('click', startSimTimer); }
    if (simStop && !simStop._bound) { simStop._bound = true; simStop.addEventListener('click', stopSimTimer); }
    if (simReset && !simReset._bound) { simReset._bound = true; simReset.addEventListener('click', resetSimTimer); }
    if (simLeftStop && !simLeftStop._bound) { simLeftStop._bound = true; simLeftStop.addEventListener('click', stopSimLeft); }
    if (simRightStop && !simRightStop._bound) { simRightStop._bound = true; simRightStop.addEventListener('click', stopSimRight); }

    // 左右独立计时器
    const ls = document.getElementById('pumpLeftTimerStart');
    const lstop = document.getElementById('pumpLeftTimerStop');
    const rs = document.getElementById('pumpRightTimerStart');
    const rstop = document.getElementById('pumpRightTimerStop');
    if (ls && !ls._bound) { ls._bound = true; ls.addEventListener('click', startPumpLeftTimer); }
    if (lstop && !lstop._bound) { lstop._bound = true; lstop.addEventListener('click', stopPumpLeftTimer); }
    if (rs && !rs._bound) { rs._bound = true; rs.addEventListener('click', startPumpRightTimer); }
    if (rstop && !rstop._bound) { rstop._bound = true; rstop.addEventListener('click', stopPumpRightTimer); }
}

function onPumpModeChange() {
    const mode = document.getElementById('pumpMode').value;
    const hint = document.getElementById('pumpModeHint');
    const singleContainer = document.getElementById('singleTimerContainer');
    const simContainer = document.getElementById('simultaneousTimerContainer');
    const sideContainer = document.getElementById('sideTimerContainer');
    const amountSingle = document.getElementById('pumpAmountSingle');
    const amountDouble = document.getElementById('pumpAmountDouble');

    // 先停止所有计时器
    stopAllPumpTimers();

    // 隐藏所有计时器
    singleContainer.style.display = 'none';
    simContainer.style.display = 'none';
    sideContainer.style.display = 'none';

    if (mode === 'left') {
        singleContainer.style.display = 'block';
        amountSingle.style.display = 'block';
        amountDouble.style.display = 'none';
        if (hint) hint.textContent = '只吸左侧，右侧不计时';
    } else if (mode === 'right') {
        singleContainer.style.display = 'block';
        amountSingle.style.display = 'block';
        amountDouble.style.display = 'none';
        if (hint) hint.textContent = '只吸右侧，左侧不计时';
    } else if (mode === 'separate') {
        sideContainer.style.display = 'grid';
        amountSingle.style.display = 'none';
        amountDouble.style.display = 'flex';
        if (hint) hint.textContent = '左右分开计时，各自独立';
    } else if (mode === 'simultaneous') {
        simContainer.style.display = 'block';
        amountSingle.style.display = 'none';
        amountDouble.style.display = 'flex';
        if (hint) hint.textContent = '双侧同时计时，可单边停止';
    }
}

function stopAllPumpTimers() {
    // 停止单侧
    if (pumpInterval) { clearInterval(pumpInterval); pumpInterval = null; }
    pumpRunning = false;
    // 停止双侧同时
    if (simInterval) { clearInterval(simInterval); simInterval = null; }
    simRunning = false;
    // 停止左右独立
    if (pumpLeftInterval) { clearInterval(pumpLeftInterval); pumpLeftInterval = null; }
    if (pumpRightInterval) { clearInterval(pumpRightInterval); pumpRightInterval = null; }
    pumpLeftRunning = false;
    pumpRightRunning = false;
}

// ==================== 单侧计时器 ====================
let pumpSeconds = 0;
let pumpRunning = false;
let pumpInterval = null;

function startSingleTimer() {
    pumpRunning = true;
    const startBtn = document.getElementById('singleTimerStart');
    const stopBtn = document.getElementById('singleTimerStop');
    const container = document.getElementById('singleTimerContainer');
    if (startBtn) startBtn.style.display = 'none';
    if (stopBtn) stopBtn.style.display = 'inline-block';
    if (container) container.classList.add('timing');
    if (pumpInterval) clearInterval(pumpInterval);
    pumpInterval = setInterval(() => {
        pumpSeconds++;
        const el = document.getElementById('singleTimerDisplay');
        if (el) el.textContent = formatTime(pumpSeconds);
    }, 1000);
}

function stopSingleTimer() {
    pumpRunning = false;
    if (pumpInterval) { clearInterval(pumpInterval); pumpInterval = null; }
    const startBtn = document.getElementById('singleTimerStart');
    const stopBtn = document.getElementById('singleTimerStop');
    const container = document.getElementById('singleTimerContainer');
    if (startBtn) startBtn.style.display = 'inline-block';
    if (stopBtn) stopBtn.style.display = 'none';
    if (container) container.classList.remove('timing');
}

function resetSingleTimer() {
    stopSingleTimer();
    pumpSeconds = 0;
    const el = document.getElementById('singleTimerDisplay');
    if (el) el.textContent = '00:00';
}

// ==================== 双侧同时计时器 ====================
function startSimTimer() {
    simRunning = true;
    simLeftStopped = false;
    simRightStopped = false;
    const startBtn = document.getElementById('simTimerStart');
    const stopBtn = document.getElementById('simTimerStop');
    const master = document.getElementById('simTimerDisplay').parentElement.parentElement;
    if (startBtn) startBtn.style.display = 'none';
    if (stopBtn) stopBtn.style.display = 'inline-block';
    if (master) master.classList.add('timing');
    // 重置侧状态
    updateSimSideStatus('left', '进行中');
    updateSimSideStatus('right', '进行中');
    document.getElementById('simLeftStop').disabled = false;
    document.getElementById('simRightStop').disabled = false;
    if (simInterval) clearInterval(simInterval);
    simInterval = setInterval(() => {
        simSeconds++;
        const el = document.getElementById('simTimerDisplay');
        if (el) el.textContent = formatTime(simSeconds);
    }, 1000);
}

function stopSimTimer() {
    simRunning = false;
    if (simInterval) { clearInterval(simInterval); simInterval = null; }
    const startBtn = document.getElementById('simTimerStart');
    const stopBtn = document.getElementById('simTimerStop');
    const master = document.getElementById('simTimerDisplay').parentElement.parentElement;
    if (startBtn) startBtn.style.display = 'inline-block';
    if (stopBtn) stopBtn.style.display = 'none';
    if (master) master.classList.remove('timing');
    // 两边都标记为已停止
    if (!simLeftStopped) updateSimSideStatus('left', '已停止');
    if (!simRightStopped) updateSimSideStatus('right', '已停止');
    document.getElementById('simLeftStop').disabled = true;
    document.getElementById('simRightStop').disabled = true;
}

function stopSimLeft() {
    simLeftStopped = true;
    updateSimSideStatus('left', '已停止');
    document.getElementById('simLeftStop').disabled = true;
    // 如果两边都停了，自动停止主计时器
    if (simRightStopped && simRunning) {
        stopSimTimer();
    }
}

function stopSimRight() {
    simRightStopped = true;
    updateSimSideStatus('right', '已停止');
    document.getElementById('simRightStop').disabled = true;
    // 如果两边都停了，自动停止主计时器
    if (simLeftStopped && simRunning) {
        stopSimTimer();
    }
}

function updateSimSideStatus(side, status) {
    const el = document.getElementById(side === 'left' ? 'simLeftStatus' : 'simRightStatus');
    if (el) { el.textContent = status; el.className = 'sim-side-status ' + (status === '进行中' ? 'active' : 'stopped'); }
}

function resetSimTimer() {
    stopSimTimer();
    simSeconds = 0;
    simLeftStopped = false;
    simRightStopped = false;
    const el = document.getElementById('simTimerDisplay');
    if (el) el.textContent = '00:00';
    updateSimSideStatus('left', '进行中');
    updateSimSideStatus('right', '进行中');
}

// ==================== 左右独立计时器 ====================
function startPumpLeftTimer() {
    pumpLeftRunning = true;
    const ls = document.getElementById('pumpLeftTimerStart');
    const lstop = document.getElementById('pumpLeftTimerStop');
    const box = document.getElementById('pumpLeftTimerBox');
    if (ls) ls.style.display = 'none';
    if (lstop) lstop.style.display = 'inline-block';
    if (box) box.classList.add('timing');
    if (pumpLeftInterval) clearInterval(pumpLeftInterval);
    pumpLeftInterval = setInterval(() => {
        pumpLeftSeconds++;
        const el = document.getElementById('pumpLeftTimerDisplay');
        if (el) el.textContent = formatTime(pumpLeftSeconds);
    }, 1000);
}

function stopPumpLeftTimer() {
    pumpLeftRunning = false;
    if (pumpLeftInterval) { clearInterval(pumpLeftInterval); pumpLeftInterval = null; }
    const ls = document.getElementById('pumpLeftTimerStart');
    const lstop = document.getElementById('pumpLeftTimerStop');
    const box = document.getElementById('pumpLeftTimerBox');
    if (ls) ls.style.display = 'inline-block';
    if (lstop) lstop.style.display = 'none';
    if (box) box.classList.remove('timing');
}

function startPumpRightTimer() {
    pumpRightRunning = true;
    const rs = document.getElementById('pumpRightTimerStart');
    const rstop = document.getElementById('pumpRightTimerStop');
    const box = document.getElementById('pumpRightTimerBox');
    if (rs) rs.style.display = 'none';
    if (rstop) rstop.style.display = 'inline-block';
    if (box) box.classList.add('timing');
    if (pumpRightInterval) clearInterval(pumpRightInterval);
    pumpRightInterval = setInterval(() => {
        pumpRightSeconds++;
        const el = document.getElementById('pumpRightTimerDisplay');
        if (el) el.textContent = formatTime(pumpRightSeconds);
    }, 1000);
}

function stopPumpRightTimer() {
    pumpRightRunning = false;
    if (pumpRightInterval) { clearInterval(pumpRightInterval); pumpRightInterval = null; }
    const rs = document.getElementById('pumpRightTimerStart');
    const rstop = document.getElementById('pumpRightTimerStop');
    const box = document.getElementById('pumpRightTimerBox');
    if (rs) rs.style.display = 'inline-block';
    if (rstop) rstop.style.display = 'none';
    if (box) box.classList.remove('timing');
}

function formatTime(seconds) {
    const mins = String(Math.floor(seconds / 60)).padStart(2, '0');
    const secs = String(seconds % 60).padStart(2, '0');
    return `${mins}:${secs}`;
}

function resetPumpSideTimers() {
    // 停止所有
    stopAllPumpTimers();
    // 重置所有秒数
    pumpLeftSeconds = 0;
    pumpRightSeconds = 0;
    pumpSeconds = 0;
    simSeconds = 0;
    simLeftStopped = false;
    simRightStopped = false;
    // 重置显示
    const displays = ['pumpLeftTimerDisplay', 'pumpRightTimerDisplay', 'singleTimerDisplay', 'simTimerDisplay'];
    displays.forEach(id => { const el = document.getElementById(id); if (el) el.textContent = '00:00'; });
    // 重置按钮状态
    const startBtns = ['pumpLeftTimerStart', 'pumpRightTimerStart', 'singleTimerStart', 'simTimerStart'];
    const stopBtns = ['pumpLeftTimerStop', 'pumpRightTimerStop', 'singleTimerStop', 'simTimerStop'];
    startBtns.forEach(id => { const el = document.getElementById(id); if (el) el.style.display = 'inline-block'; });
    stopBtns.forEach(id => { const el = document.getElementById(id); if (el) el.style.display = 'none'; });
    // 移除timing类
    ['pumpLeftTimerBox', 'pumpRightTimerBox', 'singleTimerContainer'].forEach(id => {
        const b = document.getElementById(id);
        if (b) b.classList.remove('timing');
    });
    const simMaster = document.getElementById('simTimerDisplay');
    if (simMaster && simMaster.parentElement && simMaster.parentElement.parentElement) {
        simMaster.parentElement.parentElement.classList.remove('timing');
    }
    // 重置侧状态
    updateSimSideStatus('left', '进行中');
    updateSimSideStatus('right', '进行中');
    document.getElementById('simLeftStop').disabled = false;
    document.getElementById('simRightStop').disabled = false;
}

// ==================== 生长记录页修改 ====================

function loadGrowthPage() {
    if (!App.currentBaby) return;

    api(`/api/babies/${App.currentBaby}/growth`).then(res => {
        if (!res.success) return;
        const records = res.data;

        // 更新汇总数据
        if (records.length > 0) {
            document.getElementById('latestHeight').textContent = records[0].height ? `${records[0].height} cm` : '-- cm';
            document.getElementById('latestWeightGrowth').textContent = records[0].weight ? `${records[0].weight} kg` : '-- kg';
            document.getElementById('latestBmi').textContent = records[0].bmi ? records[0].bmi : '--';
            document.getElementById('latestHead').textContent = records[0].head_circumference ? `${records[0].head_circumference} cm` : '-- cm';
        }

        const listContainer = document.getElementById('growthRecordList');
        if (records.length > 0) {
            listContainer.innerHTML = records.map(r => `
                <div class="record-item growth-record-item">
                    <button class="care-delete" onclick="deleteRecord('growth', ${r.id})">✕</button>
                    <button class="care-edit" onclick="editGrowth(${r.id})">✎</button>
                    <div class="record-item-content">
                        <span class="record-date">${escapeHtml(r.record_date)}</span>
                        <span class="record-values">
                            ${r.height ? `身高 ${escapeHtml(String(r.height))}cm ` : ''}
                            ${r.weight ? `体重 ${escapeHtml(String(r.weight))}kg ` : ''}
                            ${r.bmi ? `BMI ${escapeHtml(String(r.bmi))} ` : ''}
                            ${r.head_circumference ? `头围 ${escapeHtml(String(r.head_circumference))}cm` : ''}
                        </span>
                    </div>
                </div>
            `).join('');
        } else {
            listContainer.innerHTML = '<p class="empty-tip">暂无成长记录<br><small>点击上方按钮添加身高、体重等数据</small></p>';
        }
    });

    // 绑定添加按钮事件
    const btn = document.getElementById('addGrowthBtnPage');
    if (btn && !btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', () => showRecordModal('growth'));
    }
}

// 编辑成长记录
function editGrowth(id) {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/growth/${id}`).then(res => {
        if (!res.success) return showToast.error('加载失败');
        const r = res.data;
        showModal(`
            <div class="modal-header"><h3>编辑成长记录</h3></div>
            <div class="modal-body">
                <div class="form-group">
                    <label>记录日期</label>
                    <input type="date" id="editGrowthDate" value="${r.record_date || ''}">
                </div>
                <div class="form-group">
                    <label>身高 (cm)</label>
                    <input type="number" id="editGrowthHeight" value="${r.height || ''}" placeholder="0.0" step="0.1">
                </div>
                <div class="form-group">
                    <label>体重 (kg)</label>
                    <input type="number" id="editGrowthWeight" value="${r.weight || ''}" placeholder="0.0" step="0.01">
                </div>
                <div class="form-group">
                    <label>头围 (cm)</label>
                    <input type="number" id="editGrowthHead" value="${r.head_circumference || ''}" placeholder="0.0" step="0.1">
                </div>
                <div class="form-group">
                    <label>备注</label>
                    <input type="text" id="editGrowthNote" value="${escapeHtml(r.note || '')}" placeholder="可选">
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="closeModal()">取消</button>
                <button class="btn btn-primary" onclick="saveGrowthEdit(${r.id})">保存</button>
            </div>
        `);
    });
}

function saveGrowthEdit(id) {
    if (!App.currentBaby) return;
    const height = parseFloat(document.getElementById('editGrowthHeight').value) || null;
    const weight = parseFloat(document.getElementById('editGrowthWeight').value) || null;
    const head = parseFloat(document.getElementById('editGrowthHead').value) || null;
    let bmi = null;
    if (height && weight) {
        bmi = (weight / Math.pow(height / 100, 2)).toFixed(1);
    }
    const data = {
        record_date: document.getElementById('editGrowthDate').value,
        height: height,
        weight: weight,
        head_circumference: head,
        bmi: bmi,
        note: document.getElementById('editGrowthNote').value,
    };
    api(`/api/babies/${App.currentBaby}/growth/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            showToast.success('已更新');
            closeModal();
            loadGrowthPage();
            loadTimeline();
            loadDashboard();
            // 如果当前在健康档案的生长曲线Tab，也刷新图表
            if (document.getElementById('tab-growth') && document.getElementById('tab-growth').classList.contains('active')) {
                const activeMetric = document.querySelector('.growth-metric-btn.active');
                if (activeMetric) loadGrowthChart(activeMetric.dataset.metric);
            }
        } else {
            showToast.error(res.message || '更新失败');
        }
    });
}

// ==================== 照片相册 ====================

function initPhotoModal() {
    document.getElementById('uploadPhotoBtn').addEventListener('click', () => {
        document.getElementById('photoForm').reset();
        document.getElementById('photoDate').value = getToday();
        showModal('photoModal');
    });
    document.getElementById('closePhotoModal').addEventListener('click', () => hideModal('photoModal'));
    document.getElementById('cancelPhotoForm').addEventListener('click', () => hideModal('photoModal'));
    document.getElementById('closePhotoViewModal').addEventListener('click', () => hideModal('photoViewModal'));

    // 成长对比按钮
    const compareBtn = document.getElementById('photoCompareBtn');
    if (compareBtn) {
        compareBtn.addEventListener('click', showGrowthComparison);
    }

    // 照片分类筛选和排序
    const categoryFilter = document.getElementById('photoCategoryFilter');
    const sortFilter = document.getElementById('photoSortFilter');
    if (categoryFilter) {
        categoryFilter.addEventListener('change', loadPhotosPage);
    }
    if (sortFilter) {
        sortFilter.addEventListener('change', loadPhotosPage);
    }

    document.getElementById('photoForm').addEventListener('submit', function(e) {
        e.preventDefault();
        if (!App.currentBaby) return;

        const fileInput = document.getElementById('photoFile');
        if (!fileInput.files.length) {
            showToast('请选择照片');
            return;
        }

        const formData = new FormData();
        formData.append('photo', fileInput.files[0]);
        formData.append('photo_date', document.getElementById('photoDate').value);
        formData.append('description', document.getElementById('photoDesc').value);
        formData.append('category', document.getElementById('photoCategory')?.value || '');

        // 显示加载状态，防止重复提交
        const form = document.getElementById('photoForm');
        const submitBtn = form.querySelector('button[type="submit"]');
        const originalText = submitBtn ? submitBtn.textContent : '保存';
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.textContent = '上传中...';
        }

        fetch(`${App.apiBase}/api/babies/${App.currentBaby}/photos`, {
            method: 'POST',
            body: formData
        })
        .then(res => res.json())
        .then(res => {
            if (res.success) {
                showToast('照片上传成功');
                hideModal('photoModal');
                loadPhotosPage();
            } else {
                showToast.error(res.message || '上传失败');
            }
        })
        .catch(() => showToast('上传失败，请重试'))
        .finally(() => {
            // 恢复按钮状态
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
            }
        });
    });

    document.getElementById('deletePhotoBtn').addEventListener('click', function() {
        const photoId = this.dataset.photoId;
        if (!photoId) return;
        if (!confirm('确定要删除这张照片吗？')) return;

        api(`/api/photos/${photoId}`, { method: 'DELETE' }).then(res => {
            if (res.success) {
                showToast.success('已删除');
                hideModal('photoViewModal');
                loadPhotosPage(true);
            }
        });
    });

    // 设为封面（后端会把该宝宝其它照片的封面标记清掉，保证封面唯一）
    const setCoverBtn = document.getElementById('setCoverBtn');
    if (setCoverBtn) {
        setCoverBtn.addEventListener('click', function() {
            const photoId = this.dataset.photoId;
            if (!photoId) return;
            api(`/api/photos/${photoId}`, {
                method: 'PUT',
                body: JSON.stringify({ is_cover: true })
            }).then(res => {
                if (res.success) {
                    showToast.success('已设为封面');
                    hideModal('photoViewModal');
                    loadPhotosPage(true);
                } else {
                    showToast.error(res.message || '设置失败');
                }
            });
        });
    }
}

// 相册分页状态（照片多于一页时靠它做「加载更多」）
const photoPageState = { limit: 60, offset: 0, items: [], hasMore: false, totalFiltered: 0 };

function loadPhotosPage(reset = true) {
    if (!App.currentBaby) return;

    const grid = document.getElementById('photosGrid');
    const categoryFilter = document.getElementById('photoCategoryFilter');
    const category = (categoryFilter && categoryFilter.value) || '';

    if (reset) {
        photoPageState.offset = 0;
        photoPageState.items = [];
        photoPageState.hasMore = false;
        if (grid) grid.innerHTML = '<p class="empty-tip">加载中…</p>';
    }

    // 分类筛选交给后端按真实分类字段过滤。
    // 此前是拿「描述里有没有关键词」瞎猜（描述里得正好出现「里程碑」才筛得到）。
    let url = `/api/babies/${App.currentBaby}/photos?limit=${photoPageState.limit}&offset=${photoPageState.offset}`;
    if (category) url += `&category=${encodeURIComponent(category)}`;

    api(url).then(res => {
        if (!res.success) return;
        const batch = res.data || [];
        photoPageState.items = photoPageState.items.concat(batch);
        photoPageState.offset = photoPageState.items.length;
        photoPageState.hasMore = !!res.has_more;
        photoPageState.totalFiltered = res.total != null ? res.total : photoPageState.items.length;

        renderPhotosStats(res.stats || {});
        renderPhotosGrid();
    });
}

function renderPhotosStats(stats) {
    const container = document.getElementById('photosStats');
    if (!container) return;
    // 统计用后端给的全量口径：前端只有分页数据，自己数会在照片超过一页后算错
    container.innerHTML = `
        <div class="photos-stat-item">
            <div class="photos-stat-value">${stats.total || 0}</div>
            <div class="photos-stat-label">总照片</div>
        </div>
        <div class="photos-stat-item">
            <div class="photos-stat-value">${stats.this_month || 0}</div>
            <div class="photos-stat-label">本月新增</div>
        </div>
        <div class="photos-stat-item">
            <div class="photos-stat-value">${escapeHtml(stats.latest_date || '-')}</div>
            <div class="photos-stat-label">最新日期</div>
        </div>
    `;
}

const PHOTO_CAT_LABELS = {
    monthly: '月度记录', milestone: '里程碑', comparison: '成长对比',
    daily: '日常', other: '其他'
};

function renderPhotosGrid() {
    const grid = document.getElementById('photosGrid');
    if (!grid) return;
    const photos = photoPageState.items.slice();

    const sortFilter = document.getElementById('photoSortFilter');
    if (sortFilter && sortFilter.value === 'date_asc') {
        photos.sort((a, b) => new Date(a.photo_date) - new Date(b.photo_date));
    } else {
        photos.sort((a, b) => new Date(b.photo_date) - new Date(a.photo_date));
    }

    if (photos.length === 0) {
        grid.innerHTML = '<p class="empty-tip">没有符合条件的照片</p>';
        return;
    }

    grid.innerHTML = photos.map(p => `
        <div class="photo-item ${p.is_cover ? 'is-cover' : ''}" data-photo-id="${p.id}">
            <img src="${App.apiBase}/api/photos/thumbnail/${p.id}" alt="${escapeHtml(p.description || '照片')}" loading="lazy">
            ${p.is_cover ? '<span class="photo-cover-badge">封面</span>' : ''}
            <div class="photo-date">${escapeHtml(p.photo_date)}${p.category && PHOTO_CAT_LABELS[p.category] ? ' · ' + PHOTO_CAT_LABELS[p.category] : ''}</div>
            ${p.description ? `<div class="photo-desc">${escapeHtml(p.description)}</div>` : ''}
        </div>
    `).join('') + (photoPageState.hasMore
        ? `<button type="button" class="btn btn-outline btn-sm photos-load-more" id="photosLoadMore">加载更多（还有 ${Math.max(0, photoPageState.totalFiltered - photos.length)} 张）</button>`
        : '');

    // 点击照片查看大图
    grid.querySelectorAll('.photo-item').forEach(item => {
        item.addEventListener('click', () => openPhotoView(item.dataset.photoId));
    });
    const moreBtn = document.getElementById('photosLoadMore');
    if (moreBtn) {
        moreBtn.addEventListener('click', () => loadPhotosPage(false));
    }
}

function openPhotoView(photoId) {
    const photo = photoPageState.items.find(p => String(p.id) === String(photoId));
    if (!photo) return;

    document.getElementById('photoViewImage').src = `${App.apiBase}/api/photos/file/${photo.id}`;
    document.getElementById('photoViewTitle').textContent = photo.photo_date;
    document.getElementById('photoViewInfo').textContent = photo.description || '';
    document.getElementById('editPhotoDesc').value = photo.description || '';
    document.getElementById('editPhotoDesc').dataset.photoId = photo.id;
    const dateEl = document.getElementById('editPhotoDate');
    if (dateEl) dateEl.value = photo.photo_date || '';
    const catEl = document.getElementById('editPhotoCategory');
    if (catEl) catEl.value = photo.category || '';
    document.getElementById('deletePhotoBtn').dataset.photoId = photo.id;

    const coverBtn = document.getElementById('setCoverBtn');
    if (coverBtn) {
        coverBtn.dataset.photoId = photo.id;
        coverBtn.textContent = photo.is_cover ? '已是封面' : '设为封面';
        coverBtn.disabled = !!photo.is_cover;
    }
    showModal('photoViewModal');
}

function savePhotoDetails() {
    const descEl = document.getElementById('editPhotoDesc');
    const photoId = descEl.dataset.photoId;
    if (!photoId) return;

    // 描述、日期、分类一起保存（早期只能改描述，拍错日期的照片永远排在错的位置）
    const payload = { description: descEl.value };
    const dateEl = document.getElementById('editPhotoDate');
    if (dateEl && dateEl.value) payload.photo_date = dateEl.value;
    const catEl = document.getElementById('editPhotoCategory');
    if (catEl) payload.category = catEl.value;

    api(`/api/photos/${photoId}`, {
        method: 'PUT',
        body: JSON.stringify(payload)
    }).then(res => {
        if (res.success) {
            showToast.success('已保存');
            hideModal('photoViewModal');
            loadPhotosPage(true);
        } else {
            showToast.error(res.message || '保存失败');
        }
    });
}

// ==================== 成长对比 ====================

function showGrowthComparison() {
    if (!App.currentBaby) return;

    // 创建成长对比模态框
    let compareModal = document.getElementById('growthCompareModal');
    if (!compareModal) {
        compareModal = document.createElement('div');
        compareModal.id = 'growthCompareModal';
        compareModal.className = 'modal';
        compareModal.innerHTML = `
            <div class="modal-overlay" onclick="hideModal('growthCompareModal')"></div>
            <div class="modal-content modal-lg">
                <div class="modal-header">
                    <h3>成长对比</h3>
                    <button class="modal-close" onclick="hideModal('growthCompareModal')">&times;</button>
                </div>
                <div class="comparison-container" id="comparisonContainer">
                    <div class="comparison-header">
                        <span>选择照片进行对比</span>
                        <button class="btn btn-outline btn-sm" id="compareSelectedBtn" disabled>对比选中</button>
                    </div>
                    <div class="comparison-photos" id="comparisonPhotosGrid"></div>
                </div>
                <div class="comparison-view" id="comparisonView" style="display:none;"></div>
            </div>
        `;
        document.body.appendChild(compareModal);
    }

    showModal('growthCompareModal');
    loadComparisonPhotos();
}

function loadComparisonPhotos() {
    if (!App.currentBaby) return;

    api(`/api/babies/${App.currentBaby}/photos`).then(res => {
        if (!res.success) return;
        const photos = res.data;
        const grid = document.getElementById('comparisonPhotosGrid');

        if (photos.length < 2) {
            grid.innerHTML = '<p class="empty-tip">至少需要2张照片才能进行对比</p>';
            return;
        }

        // 按日期排序
        photos.sort((a, b) => new Date(a.photo_date) - new Date(b.photo_date));

        grid.innerHTML = photos.map((p, idx) => `
            <div class="comparison-item" data-photo-id="${p.id}" data-idx="${idx}">
                <img src="${App.apiBase}/api/photos/thumbnail/${p.id}" alt="${escapeHtml(p.description || '照片')}" loading="lazy">
                <div class="comparison-info">
                    <div class="comparison-age">${escapeHtml(p.photo_date)}</div>
                    <div class="comparison-date">${escapeHtml(p.description || '无描述')}</div>
                </div>
            </div>
        `).join('');

        // 选择照片进行对比
        const selectedPhotos = [];
        grid.querySelectorAll('.comparison-item').forEach(item => {
            item.addEventListener('click', () => {
                item.classList.toggle('selected');
                const photoId = parseInt(item.dataset.photoId);
                const idx = selectedPhotos.indexOf(photoId);
                if (idx > -1) {
                    selectedPhotos.splice(idx, 1);
                } else {
                    selectedPhotos.push(photoId);
                }
                const compareBtn = document.getElementById('compareSelectedBtn');
                compareBtn.disabled = selectedPhotos.length < 2;
                compareBtn.textContent = selectedPhotos.length >= 2
                    ? `对比选中 (${selectedPhotos.length})`
                    : '对比选中';
            });
        });

        document.getElementById('compareSelectedBtn').addEventListener('click', () => {
            if (selectedPhotos.length < 2) return;
            showComparisonView(photos, selectedPhotos);
        });
    });
}

function showComparisonView(allPhotos, selectedIds) {
    const container = document.getElementById('comparisonContainer');
    const view = document.getElementById('comparisonView');

    container.style.display = 'none';
    view.style.display = 'flex';

    const selectedPhotos = allPhotos.filter(p => selectedIds.includes(p.id));
    selectedPhotos.sort((a, b) => new Date(a.photo_date) - new Date(b.photo_date));

    view.innerHTML = `
        <button class="btn btn-outline btn-sm" onclick="backToComparisonGrid()" style="margin-bottom:16px;">← 返回选择</button>
        ${selectedPhotos.map(p => `
            <div class="comparison-view-image">
                <img src="${App.apiBase}/api/photos/file/${p.id}" alt="${escapeHtml(p.description || '照片')}">
                <div class="comparison-view-label">${escapeHtml(p.photo_date)}${p.description ? ' - ' + escapeHtml(p.description) : ''}</div>
            </div>
        `).join('')}
    `;
}

function backToComparisonGrid() {
    document.getElementById('comparisonContainer').style.display = 'block';
    document.getElementById('comparisonView').style.display = 'none';
}

// ==================== 疫苗追踪 ====================

function initVaccineModal() {
    // 「疫苗追踪」独立页已并入健康档案的疫苗 Tab，#addVaccineBtn 随 page-vaccines 一起删了。
    // 健康档案走 addVaccineBtn2 -> showVaccineForm（checkupFormModal 通用弹窗）。
    // 这里用可选链：按钮没了就只是没有入口，不能让下面关闭/提交的绑定也跟着失效。
    document.getElementById('addVaccineBtn')?.addEventListener('click', () => {
        document.getElementById('vaccineForm').reset();
        document.getElementById('vaccineId').value = '';
        document.getElementById('vaccineModalTitle').textContent = '添加疫苗记录';
        document.getElementById('vaccineDose').value = 1;
        document.getElementById('vaccineStatus').value = 'pending';
        showModal('vaccineModal');
    });
    document.getElementById('closeVaccineModal').addEventListener('click', () => hideModal('vaccineModal'));
    document.getElementById('cancelVaccineForm').addEventListener('click', () => hideModal('vaccineModal'));

    document.getElementById('vaccineForm').addEventListener('submit', function(e) {
        e.preventDefault();
        if (!App.currentBaby) return;

        const vaccineId = document.getElementById('vaccineId').value;
        const data = {
            vaccine_name: document.getElementById('vaccineName').value,
            vaccine_type: document.getElementById('vaccineType').value,
            dose_number: parseInt(document.getElementById('vaccineDose').value) || 1,
            scheduled_date: document.getElementById('vaccineScheduled').value || null,
            actual_date: document.getElementById('vaccineActual').value || null,
            status: document.getElementById('vaccineStatus').value,
            hospital: document.getElementById('vaccineHospital').value || '',
            doctor: document.getElementById('vaccineDoctor').value || '',
            manufacturer: document.getElementById('vaccineManufacturer').value || '',
            batch_number: document.getElementById('vaccineBatch').value || '',
            injection_site: document.getElementById('vaccineSite').value || '',
            has_reaction: document.getElementById('vaccineReaction')?.checked || false,
            reaction_detail: document.getElementById('vaccineReactionDetail')?.value || '',
            reaction_severity: document.getElementById('vaccineReactionSeverity')?.value || 'none',
            next_dose_date: document.getElementById('vaccineNextDose').value || null,
            note: document.getElementById('vaccineNote').value || ''
        };

        let url, method;
        if (vaccineId) {
            url = `/api/vaccines/${vaccineId}`;
            method = 'PUT';
        } else {
            url = `/api/babies/${App.currentBaby}/vaccines`;
            method = 'POST';
        }

        api(url, {
            method: method,
            body: JSON.stringify(data)
        }).then(res => {
            if (res.success) {
                showToast(vaccineId ? '已更新' : '添加成功');
                hideModal('vaccineModal');
                loadVaccinesPage();
            } else {
                showToast.error(res.message || '保存失败');
            }
        });
    });
}

function loadVaccinesPage() {
    // 「疫苗追踪」独立页已并入健康档案的疫苗 Tab，旧列表容器 #vaccinesList 已删除。
    // 这里保留旧入口（保存/生成计划后会调用），委托给健康档案那套实现，
    // 否则会去渲染一个不存在的节点而抛异常。
    loadVaccineRecords();
    loadVaccineStats();
}

function initVaccinesPage() {
    document.getElementById('initVaccinesBtn')?.addEventListener('click', () => {
        if (!App.currentBaby) return;
        if (!confirm('将根据宝宝生日自动生成接种计划，已有记录不会被覆盖。继续吗？')) return;

        api(`/api/babies/${App.currentBaby}/vaccines/init`, { method: 'POST' }).then(res => {
            if (res.success) {
                showToast(res.message);
                loadVaccinesPage();
            } else {
                showToast.error(res.message || '生成失败');
            }
        });
    });

    // 导出按钮
    const exportBtn = document.getElementById('exportVaccinesBtn');
    if (exportBtn) {
        exportBtn.addEventListener('click', () => {
            if (!App.currentBaby) return;
            window.open(`/api/babies/${App.currentBaby}/vaccines/export?format=csv`, '_blank');
        });
    }

    // 时间线按钮
    const timelineBtn = document.getElementById('showTimelineBtn');
    if (timelineBtn) {
        timelineBtn.addEventListener('click', () => {
            if (!App.currentBaby) return;
            loadVaccineTimeline();
        });
    }
}

function loadVaccineTimeline() {
    if (!App.currentBaby) return;
    // 「疫苗追踪」独立页已并入健康档案的疫苗 Tab，时间线渲染到 Tab 内的容器。
    // 点按钮是切换展开/收起，不是每次都重新拉数据。
    const box = document.getElementById('vaccineTimelineBox');
    if (!box) return;
    if (box.style.display !== 'none') {
        box.style.display = 'none';
        return;
    }
    api(`/api/babies/${App.currentBaby}/vaccines/timeline`).then(res => {
        if (!res.success) return;
        const timeline = res.data;
        const container = box;
        container.style.display = '';

        if (timeline.length === 0) {
            container.innerHTML = '<p class="empty-tip">暂无接种记录</p>';
            return;
        }

        container.innerHTML = `<div class="vaccine-timeline">` +
            timeline.map(t => `
                <div class="vaccine-timeline-item ${t.status}">
                    <div class="vaccine-timeline-dot ${t.status}"></div>
                    <div class="vaccine-timeline-content">
                        <div class="vaccine-timeline-date">${t.date} <span class="vaccine-timeline-age">${t.age_display}</span></div>
                        <div class="vaccine-timeline-name">${escapeHtml(t.vaccine_name)} (第${t.dose_number}剂)${t.vaccine_type === 'paid' ? ' <span class="vaccine-badge-paid">自费</span>' : ''}</div>
                        <div class="vaccine-timeline-meta">
                            ${t.hospital ? '接种: ' + escapeHtml(t.hospital) + ' ' : ''}
                            ${t.manufacturer ? '厂家: ' + escapeHtml(t.manufacturer) + ' ' : ''}
                            ${t.batch_number ? '批号: ' + escapeHtml(t.batch_number) + ' ' : ''}
                            ${t.injection_site ? '部位: ' + escapeHtml(t.injection_site) : ''}
                        </div>
                        ${t.has_reaction ? '<div class="vaccine-timeline-reaction">[!] 不良反应: ' + escapeHtml(t.reaction_detail || '有记录') + '</div>' : ''}
                    </div>
                </div>
            `).join('') +
        `</div>`;
    });
}

function loadVaccineStats() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/vaccines/stats`).then(res => {
        if (!res.success) return;
        const stats = res.data;
        const container = document.getElementById('vaccinesStats');
        if (!container) return;

        container.innerHTML = `
            <div class="vaccine-stat-card completed">
                <div class="vaccine-stat-value">${stats.total_completed}</div>
                <div class="vaccine-stat-label">已接种剂次</div>
            </div>
            <div class="vaccine-stat-card pending">
                <div class="vaccine-stat-value">${stats.total_doses - stats.total_completed}</div>
                <div class="vaccine-stat-label">待接种剂次</div>
            </div>
            <div class="vaccine-stat-card free">
                <div class="vaccine-stat-value">${stats.free.completed}/${stats.free.total}</div>
                <div class="vaccine-stat-label">一类疫苗</div>
            </div>
            <div class="vaccine-stat-card paid">
                <div class="vaccine-stat-value">${stats.paid.completed}/${stats.paid.total}</div>
                <div class="vaccine-stat-label">二类疫苗</div>
            </div>
        `;
    });
}

// ==================== 分享卡片 ====================

function initShareCard() {
    document.getElementById('shareCardBtn').addEventListener('click', () => {
        if (!App.currentBaby) {
            showToast.warning('请先添加宝宝');
            return;
        }
        showModal('shareCardModal');
        generateShareCard();
    });
    document.getElementById('closeShareCardModal').addEventListener('click', () => hideModal('shareCardModal'));
    document.getElementById('shareCardRefreshBtn').addEventListener('click', generateShareCard);
    document.getElementById('downloadCardBtn').addEventListener('click', downloadShareCard);
}

function generateShareCard() {
    if (!App.currentBaby) return;

    // 获取宝宝信息和仪表盘数据
    const baby = App.babies.find(b => b.id === App.currentBaby);
    if (!baby) return;

    api(`/api/babies/${App.currentBaby}/dashboard`).then(res => {
        if (!res.success) return;
        const data = res.data;

        const canvas = document.getElementById('shareCardCanvas');
        const ctx = canvas.getContext('2d');
        const W = canvas.width;
        const H = canvas.height;

        // 背景渐变
        const bgGrad = ctx.createLinearGradient(0, 0, W, H);
        bgGrad.addColorStop(0, '#ff9a9e');
        bgGrad.addColorStop(0.5, '#fad0c4');
        bgGrad.addColorStop(1, '#a18cd1');
        ctx.fillStyle = bgGrad;
        ctx.fillRect(0, 0, W, H);

        // 装饰圆形
        ctx.globalAlpha = 0.1;
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.arc(500, 100, 120, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(100, 700, 80, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;

        // 白色卡片背景
        ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
        roundRect(ctx, 30, 30, W - 60, H - 60, 20);
        ctx.fill();

        // 标题
        ctx.fillStyle = '#2d3436';
        ctx.font = 'bold 32px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(`${baby.name}的成长记录`, W / 2, 90);

        // 宝宝头像区域
        const avatarY = 140;
        ctx.fillStyle = '#ffeaa7';
        ctx.beginPath();
        ctx.arc(W / 2, avatarY, 40, 0, Math.PI * 2);
        ctx.fill();
        ctx.font = '36px sans-serif';
        ctx.fillStyle = '#2d3436';
        ctx.fillText('', W / 2, avatarY + 12);

        // 年龄
        ctx.font = '18px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.fillStyle = '#636e72';
        ctx.fillText(baby.age || '', W / 2, avatarY + 70);

        // 分隔线
        ctx.strokeStyle = '#eee';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(60, 210);
        ctx.lineTo(W - 60, 210);
        ctx.stroke();

        // 数据统计
        const statsY = 260;
        const statItems = [
            { icon: '', value: data.today_feeding.count, label: '今日喂奶', unit: '次' },
            { icon: '', value: data.today_sleep.total_minutes, label: '今日睡眠', unit: '分钟' },
            { icon: '', value: data.today_diaper.count, label: '今日换尿布', unit: '次' },
            { icon: '', value: data.latest_growth ? data.latest_growth.weight : '--', label: '最新体重', unit: 'kg' }
        ];

        const colWidth = W / 4;
        statItems.forEach((item, i) => {
            const x = colWidth * i + colWidth / 2;
            ctx.font = '28px sans-serif';
            ctx.fillStyle = '#2d3436';
            ctx.textAlign = 'center';
            ctx.fillText(item.icon, x, statsY);
            ctx.font = 'bold 24px "PingFang SC", "Microsoft YaHei", sans-serif';
            ctx.fillStyle = '#e85a8f';
            ctx.fillText(String(item.value), x, statsY + 35);
            ctx.font = '12px "PingFang SC", "Microsoft YaHei", sans-serif';
            ctx.fillStyle = '#636e72';
            ctx.fillText(item.label, x, statsY + 55);
        });

        // 成长数据
        if (data.latest_growth) {
            const growthY = 360;
            ctx.fillStyle = '#2d3436';
            ctx.font = 'bold 18px "PingFang SC", "Microsoft YaHei", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('成长数据', W / 2, growthY);

            const growthItems = [];
            if (data.latest_growth.height) growthItems.push(`身高 ${data.latest_growth.height}cm`);
            if (data.latest_growth.weight) growthItems.push(`体重 ${data.latest_growth.weight}kg`);
            if (data.latest_growth.bmi) growthItems.push(`BMI ${data.latest_growth.bmi}`);
            if (data.latest_growth.head_circumference) growthItems.push(`头围 ${data.latest_growth.head_circumference}cm`);

            ctx.font = '15px "PingFang SC", "Microsoft YaHei", sans-serif';
            ctx.fillStyle = '#636e72';
            ctx.fillText(growthItems.join('  |  '), W / 2, growthY + 30);
        }

        // 里程碑
        if (data.latest_milestones && data.latest_milestones.length > 0) {
            const mileY = 430;
            ctx.fillStyle = '#2d3436';
            ctx.font = 'bold 18px "PingFang SC", "Microsoft YaHei", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('最近里程碑', W / 2, mileY);

            ctx.font = '14px "PingFang SC", "Microsoft YaHei", sans-serif';
            ctx.fillStyle = '#636e72';
            data.latest_milestones.slice(0, 3).forEach((m, i) => {
                ctx.fillText(` ${m.title} (${m.achieved_date})`, W / 2, mileY + 30 + i * 24);
            });
        }

        // 底部品牌
        ctx.fillStyle = '#b2bec3';
        ctx.font = '13px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('育儿宝 · 飞牛 fnOS 育儿助手', W / 2, H - 50);

        // 日期
        ctx.font = '12px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.fillText(getToday(), W / 2, H - 28);
    });
}

function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
}

function downloadShareCard() {
    const canvas = document.getElementById('shareCardCanvas');
    const link = document.createElement('a');
    link.download = `育儿宝_分享卡片_${getToday()}.png`;
    link.href = canvas.toDataURL('image/png');
    link.click();
    showToast('卡片已保存');
}

// ==================== 里程碑管理 ====================

let currentMilestoneCategory = 'all';

function initMilestoneModal() {
    document.getElementById('addMilestoneBtn').addEventListener('click', () => {
        document.getElementById('milestoneForm').reset();
        document.getElementById('milestoneId').value = '';
        document.getElementById('milestoneModalTitle').textContent = '添加里程碑';
        document.getElementById('milestoneDate').value = getToday();
        document.getElementById('milestoneCategory').value = 'other';
        showModal('milestoneModal');
    });
    document.getElementById('closeMilestoneModal').addEventListener('click', () => hideModal('milestoneModal'));
    document.getElementById('cancelMilestoneForm').addEventListener('click', () => hideModal('milestoneModal'));

    document.getElementById('milestoneForm').addEventListener('submit', function(e) {
        e.preventDefault();
        if (!App.currentBaby) return;

        const milestoneId = document.getElementById('milestoneId').value;
        const data = {
            title: document.getElementById('milestoneTitle').value,
            achieved_date: document.getElementById('milestoneDate').value,
            category: document.getElementById('milestoneCategory').value,
            description: document.getElementById('milestoneDesc').value
        };

        let url, method;
        if (milestoneId) {
            url = `/api/babies/${App.currentBaby}/milestones/${milestoneId}`;
            method = 'PUT';
        } else {
            url = `/api/babies/${App.currentBaby}/milestones`;
            method = 'POST';
        }

        api(url, { method, body: JSON.stringify(data) }).then(res => {
            if (res.success) {
                showToast(milestoneId ? '已更新' : '添加成功');
                hideModal('milestoneModal');
                // 里程碑与「第一次」共用一张表，两边都要跟着刷新
                loadMilestonesPage();
                loadFirstsRecords();
            } else {
                showToast.error(res.message || '保存失败');
            }
        });
    });

    // 分类筛选
    document.querySelectorAll('.milestone-cat-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.milestone-cat-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentMilestoneCategory = btn.dataset.cat;
            renderMilestones();
        });
    });
}

function loadMilestonesPage() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/milestones`).then(res => {
        if (!res.success) return;
        App.milestones = res.data;
        renderMilestones();
    });
}

function renderMilestones() {
    const list = document.getElementById('milestonesTimeline');
    let milestones = App.milestones || [];

    if (currentMilestoneCategory === 'first') {
        // 「第一次」不是分类，而是 is_first 标记
        milestones = milestones.filter(m => m.is_first === 1 || m.is_first === true);
    } else if (currentMilestoneCategory !== 'all') {
        milestones = milestones.filter(m => m.category === currentMilestoneCategory);
    }

    if (milestones.length === 0) {
        list.innerHTML = '<p class="empty-tip">暂无里程碑记录</p>';
        return;
    }

    const catIcons = { motor: '', language: '', social: '', cognitive: '', other: '' };
    const catNames = { motor: '运动', language: '语言', social: '社交', cognitive: '认知', other: '其他' };

    list.innerHTML = milestones.map(m => `
        <div class="milestone-card ${m.category || 'other'}" data-id="${m.id}">
            <div class="milestone-icon">${catIcons[m.category] || ''}</div>
            <div class="milestone-content">
                <div class="milestone-title">${escapeHtml(m.title)}</div>
                ${m.description ? `<div class="milestone-desc">${escapeHtml(m.description)}</div>` : ''}
                <div class="milestone-date">${escapeHtml(m.achieved_date)} · ${catNames[m.category] || '其他'}${m.is_first ? ' · 第一次' : ''}</div>
            </div>
            <div class="milestones-actions">
                <button class="milestone-action-btn" onclick="deleteGrowthMilestone(${m.id}, event)">X</button>
            </div>
        </div>
    `).join('');

    // 点击编辑
    list.querySelectorAll('.milestone-card').forEach(card => {
        card.addEventListener('click', (e) => {
            if (e.target.closest('.milestone-actions')) return;
            const id = card.dataset.id;
            const m = App.milestones.find(x => x.id == id);
            if (!m) return;

            document.getElementById('milestoneId').value = m.id;
            document.getElementById('milestoneTitle').value = m.title;
            document.getElementById('milestoneDate').value = m.achieved_date;
            document.getElementById('milestoneCategory').value = m.category || 'other';
            document.getElementById('milestoneDesc').value = m.description || '';
            document.getElementById('milestoneModalTitle').textContent = '编辑里程碑';
            showModal('milestoneModal');
        });
    });
}

// 成长里程碑页的删除。注意不要复用 deleteMilestone()——那个删的是健康档案的
// milestone_details 表，两张表不通用，混用会删错数据。
function deleteGrowthMilestone(id, event) {
    if (event) event.stopPropagation();
    if (!confirm('确定删除这条里程碑记录吗？')) return;
    api(`/api/babies/${App.currentBaby}/milestones/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast('已删除');
            // 里程碑与「第一次」共用一张表，两边都要跟着刷新
            loadMilestonesPage();
            loadFirstsRecords();
        } else {
            showToast.error(res.message || '删除失败');
        }
    });
}

// ==================== 睡眠分析 ====================

function initSleepAnalysis() {
    const range = document.getElementById('sleepAnalysisRange');
    if (range) range.addEventListener('change', loadSleepAnalysis);

    // 转屏 / 改窗口大小后画布宽度会变，得按新宽度重画，否则图被拉扁或留白。
    // 只在睡眠分析视图可见时才重画（别的页面画了也看不见，白费一次请求）。
    let resizeTimer = null;
    window.addEventListener('resize', () => {
        const view = document.getElementById('growthViewSleepAnalysis');
        if (!view || view.style.display === 'none') return;
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => loadSleepAnalysis(), 300);
    });
}

function loadSleepAnalysis() {
    if (!App.currentBaby) return;
    const rangeSelect = document.getElementById('sleepAnalysisRange');
    const days = rangeSelect ? rangeSelect.value : 7;

    // 加载睡眠分析数据
    api(`/api/babies/${App.currentBaby}/sleep-analysis?days=${days}`).then(res => {
        if (!res.success) return;
        const data = res.data;

        // 统计卡片
        const avgDuration = data.averages.avg_duration;
        const avgNap = data.averages.avg_nap;
        const avgNight = data.averages.avg_night;
        const totalSessions = data.averages.total_sessions;
        const avgBedtime = data.avg_bedtime || '--';

        document.getElementById('sleepAnalysisStats').innerHTML = `
            <div class="sleep-stat-card">
                <span class="sleep-stat-value">${formatMinutes(avgDuration)}</span>
                <span class="sleep-stat-label">平均总睡眠</span>
            </div>
            <div class="sleep-stat-card">
                <span class="sleep-stat-value">${formatMinutes(avgNight)}</span>
                <span class="sleep-stat-label">平均夜间睡眠</span>
            </div>
            <div class="sleep-stat-card">
                <span class="sleep-stat-value">${formatMinutes(avgNap)}</span>
                <span class="sleep-stat-label">平均小憩</span>
            </div>
            <div class="sleep-stat-card">
                <span class="sleep-stat-value">${avgBedtime}</span>
                <span class="sleep-stat-label">平均入睡时间</span>
            </div>
        `;

        renderSleepGoal(data.reference);

        // 睡眠质量分布
        const quality = data.quality_distribution || { good: 0, normal: 0, poor: 0 };
        const qualityTotal = quality.good + quality.normal + quality.poor;
        const qualityHtml = qualityTotal > 0 ? `
            <div class="sleep-quality-bar">
                ${quality.good > 0 ? `<div class="sleep-quality-good" style="width:${quality.good / qualityTotal * 100}%"></div>` : ''}
                ${quality.normal > 0 ? `<div class="sleep-quality-normal" style="width:${quality.normal / qualityTotal * 100}%"></div>` : ''}
                ${quality.poor > 0 ? `<div class="sleep-quality-poor" style="width:${quality.poor / qualityTotal * 100}%"></div>` : ''}
            </div>
            <div class="sleep-quality-legend">
                <span><i class="dot good"></i>好 ${quality.good}次</span>
                <span><i class="dot normal"></i>一般 ${quality.normal}次</span>
                <span><i class="dot poor"></i>差 ${quality.poor}次</span>
            </div>
        ` : '<p class="empty-tip">暂无质量数据</p>';

        // 睡眠建议
        let tips = '';
        if (avgDuration < 600 && data.days >= 3) {
            tips = '💡 宝宝平均睡眠时间偏少，建议关注睡眠环境舒适度';
        } else if (data.bedtime_variance && data.bedtime_variance > 60) {
            tips = '💡 入睡时间波动较大，建议建立规律的睡前程序';
        } else if (quality.poor > quality.good) {
            tips = '💡 睡眠质量较差的情况较多，注意观察是否有不适';
        } else if (avgDuration >= 600 && avgDuration <= 960) {
            tips = '✅ 宝宝睡眠状况良好，继续保持！';
        } else {
            tips = '💡 继续记录睡眠数据，获取更精准的分析';
        }

        document.getElementById('sleepPredictionContent').innerHTML = `
            <div class="sleep-quality-section">
                <h4>睡眠质量分布</h4>
                ${qualityHtml}
            </div>
            <div class="sleep-tips-section">
                <h4>睡眠建议</h4>
                <p>${tips}</p>
            </div>
        `;

        // 绘制图表
        drawSleepChart(data.daily);

        // 每日详情
        const detail = document.getElementById('sleepAnalysisDetail');
        if (data.daily.length === 0) {
            detail.innerHTML = '<p class="empty-tip">暂无睡眠数据</p>';
            return;
        }

        detail.innerHTML = data.daily.map(d => {
            const total = d.total || 0;
            const night = d.night_total || 0;
            const nap = d.nap_total || 0;
            const nightPct = total > 0 ? (night / total * 100) : 0;
            const napPct = total > 0 ? (nap / total * 100) : 0;
            const dateParts = d.date.split('-');
            const label = `${parseInt(dateParts[1])}/${parseInt(dateParts[2])}`;
            // 质量标签由后端算好（analytics.SLEEP_QUALITY_LABELS），前端不再自己映射，
            // 免得前后端两处对照表改一边漏一边
            const qualityLabel = d.quality_label || '';

            return `
                <div class="sleep-daily-item">
                    <span class="sleep-daily-date">${label}</span>
                    <div class="sleep-daily-bar">
                        <div class="sleep-daily-night" style="width: ${nightPct}%"></div>
                        <div class="sleep-daily-nap" style="width: ${napPct}%"></div>
                    </div>
                    <span class="sleep-daily-total">${formatMinutes(total)}</span>
                    ${qualityLabel ? `<span class="sleep-daily-quality">${qualityLabel}</span>` : ''}
                </div>
            `;
        }).join('');
    });

    // 加载24小时睡眠分布
    api(`/api/babies/${App.currentBaby}/pattern/sleep?days=${days}`).then(res => {
        if (res.success) {
            drawSleepPatternChart(res.data);
        }
    });
}

/**
 * 让 canvas 按父容器宽度自适应，并按设备像素比放大。
 *
 * 原先两张图都写死 width=700，手机上超出屏宽会被切掉右半边；
 * 但只改 style.width 会让画布被拉伸得模糊。这里同时做两件事：
 * ① 把画布真实像素设为 CSS 宽度 × dpr（高清）；
 * ② setTransform 缩放后，绘图代码继续用 CSS 像素算坐标，不用每个 x/y 都乘 dpr。
 *
 * 返回 {ctx, W, H}，W/H 是 CSS 像素，可直接拿去算坐标。
 */
function fitCanvas(canvas, cssHeight) {
    if (!canvas) return null;
    const parent = canvas.parentElement;
    // clientWidth 含 padding，直接用会让画布比内容区宽、把卡片撑破，这里减掉
    let cssW = 0;
    if (parent) {
        const st = window.getComputedStyle(parent);
        cssW = parent.clientWidth
            - (parseFloat(st.paddingLeft) || 0)
            - (parseFloat(st.paddingRight) || 0);
    }
    // 父容器还没布局出来时（视图刚显示、宽度为 0）退回一个最小可用宽度
    if (!cssW) cssW = canvas.clientWidth || 700;
    cssW = Math.max(260, Math.min(cssW, 1200));

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssHeight * dpr);
    canvas.style.width = cssW + 'px';
    canvas.style.height = cssHeight + 'px';

    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, W: cssW, H: cssHeight };
}

/** 睡眠目标对比卡：按月龄建议时长看近 N 天达不达标。
 *  建议值与「达标/偏少/偏多」的判定都由后端给（analytics._build_sleep_reference，
 *  复用 ai_engine.SLEEP_GUIDELINES 那套），前端只负责画，不自己判。 */
function renderSleepGoal(ref) {
    const card = document.getElementById('sleepGoalCard');
    if (!card) return;

    // 没有生日算不出月龄、或这段时间压根没记录，就不显示这张卡
    if (!ref || !ref.suggested_hours || ref.status === 'none') {
        card.style.display = 'none';
        return;
    }
    card.style.display = '';

    const badge = document.getElementById('sleepGoalBadge');
    const band = document.getElementById('sleepGoalBand');
    const fill = document.getElementById('sleepGoalFill');
    const mark = document.getElementById('sleepGoalMark');
    const text = document.getElementById('sleepGoalText');
    if (!badge || !band || !fill || !mark || !text) return;

    const suggested = ref.suggested_hours;
    const actual = ref.actual_hours || 0;
    // 刻度最大值：取「建议值 ×1.6」和「实际值」里大的那个，保证两者都画得下
    const scaleMax = Math.max(suggested * 1.6, actual * 1.1, 1);
    const pct = v => Math.max(0, Math.min(100, (v / scaleMax) * 100));

    const STATUS = {
        ok: { label: '达标', cls: 'ok' },
        low: { label: '偏少', cls: 'low' },
        high: { label: '偏多', cls: 'high' },
    };
    const st = STATUS[ref.status] || STATUS.ok;
    badge.textContent = st.label;
    badge.className = 'sleep-goal-badge ' + st.cls;

    // 达标区间：建议值 ±2 小时（与后端 SLEEP_TOLERANCE_HOURS 一致）
    band.style.left = pct(suggested - 2) + '%';
    band.style.width = (pct(suggested + 2) - pct(suggested - 2)) + '%';
    fill.style.width = pct(actual) + '%';
    fill.className = 'sleep-goal-fill ' + st.cls;
    mark.style.left = pct(suggested) + '%';

    const diff = Math.abs(actual - suggested);
    let tail;
    if (ref.status === 'ok') {
        tail = `在建议范围内（差 ${diff.toFixed(1)} 小时）`;
    } else {
        tail = `${ref.status === 'low' ? '比建议少' : '比建议多'} ${diff.toFixed(1)} 小时`;
    }
    const ageText = ref.age_months != null ? `${ref.age_months} 个月` : '当前月龄';
    const extra = [];
    if (ref.nap_times) extra.push(`白天小睡约 ${ref.nap_times} 次`);
    if (ref.night_stretch) extra.push(`夜间连续约 ${escapeHtml(String(ref.night_stretch))}`);

    text.innerHTML = `
        <span class="sleep-goal-main">近 ${ref.days || 7} 天日均 <strong>${actual.toFixed(1)}</strong> 小时 ·
        建议 ${suggested} 小时 · ${tail}</span>
        <span class="sleep-goal-sub">${escapeHtml(ageText)}参考${extra.length ? ' · ' + extra.join(' · ') : ''}</span>
    `;
}

function drawSleepPatternChart(patternData) {
    const canvas = document.getElementById('sleepPatternCanvas');
    if (!canvas) return;
    const fit = fitCanvas(canvas, 200);
    if (!fit) return;
    const { ctx, W, H } = fit;

    ctx.clearRect(0, 0, W, H);

    if (!patternData || patternData.length === 0) {
        ctx.fillStyle = '#b2bec3';
        ctx.font = '14px "PingFang SC", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('暂无数据', W / 2, H / 2);
        return;
    }

    const padding = { top: 20, right: 20, bottom: 30, left: 40 };
    const chartW = W - padding.left - padding.right;
    const chartH = H - padding.top - padding.bottom;

    // 找出最大值
    const maxTotal = Math.max(...patternData.map(d => d.total || 0), 1);
    const yMax = Math.ceil(maxTotal / 30) * 30 + 30;

    // 绘制网格线
    ctx.strokeStyle = '#eee';
    ctx.lineWidth = 1;
    const ySteps = 4;
    for (let i = 0; i <= ySteps; i++) {
        const y = padding.top + (chartH / ySteps) * i;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(W - padding.right, y);
        ctx.stroke();

        // Y轴标签
        const val = yMax - (yMax / ySteps) * i;
        ctx.fillStyle = '#b2bec3';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(`${Math.round(val)}m`, padding.left - 5, y + 3);
    }

    // 绘制柱状图
    const barWidth = Math.min(chartW / 24 * 0.7, 20);
    const gap = chartW / 24;
    // 窄屏放不下 24 个小时刻度，隔 6 小时标一次，不然数字糊成一片
    const hourStep = W < 380 ? 6 : 3;

    patternData.forEach((d, i) => {
        const x = padding.left + gap * i + (gap - barWidth) / 2;
        const night = d.night || 0;
        const nap = d.nap || 0;

        const nightH = (night / yMax) * chartH;
        const napH = (nap / yMax) * chartH;
        const yBase = padding.top + chartH;

        // 夜间睡眠（底部）
        if (nightH > 0) {
            ctx.fillStyle = '#6c5ce7';
            ctx.fillRect(x, yBase - nightH, barWidth, nightH);
        }

        // 小憩（顶部）
        if (napH > 0) {
            ctx.fillStyle = '#fdcb6e';
            ctx.fillRect(x, yBase - nightH - napH, barWidth, napH);
        }

        // X轴标签
        if (i % hourStep === 0) {
            ctx.fillStyle = '#636e72';
            ctx.font = '9px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(`${i}时`, x + barWidth / 2, H - padding.bottom + 12);
        }
    });

    // 图例
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'left';
    const legendY = 12;
    ctx.fillStyle = '#6c5ce7';
    ctx.fillRect(padding.left, legendY - 6, 10, 10);
    ctx.fillStyle = '#636e72';
    ctx.fillText('夜间', padding.left + 14, legendY + 3);

    ctx.fillStyle = '#fdcb6e';
    ctx.fillRect(padding.left + 50, legendY - 6, 10, 10);
    ctx.fillStyle = '#636e72';
    ctx.fillText('小憩', padding.left + 64, legendY + 3);
}

function formatMinutes(mins) {
    if (!mins || mins === 0) return '0m';
    const h = Math.floor(mins / 60);
    const m = Math.round(mins % 60);
    if (h === 0) return `${m}m`;
    if (m === 0) return `${h}h`;
    return `${h}h${m}m`;
}

function drawSleepChart(dailyData) {
    const canvas = document.getElementById('sleepAnalysisCanvas');
    if (!canvas) return;
    const fit = fitCanvas(canvas, 300);
    if (!fit) return;
    const { ctx, W, H } = fit;

    ctx.clearRect(0, 0, W, H);

    if (!dailyData || dailyData.length === 0) {
        ctx.fillStyle = '#b2bec3';
        ctx.font = '14px "PingFang SC", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('暂无数据', W / 2, H / 2);
        return;
    }

    const padding = { top: 30, right: 20, bottom: 40, left: 50 };
    const chartW = W - padding.left - padding.right;
    const chartH = H - padding.top - padding.bottom;

    // 找出最大值
    const maxTotal = Math.max(...dailyData.map(d => d.total || 0), 1);
    const yMax = Math.ceil(maxTotal / 60) * 60 + 60; // 向上取整到小时

    // 绘制网格线
    ctx.strokeStyle = '#eee';
    ctx.lineWidth = 1;
    const ySteps = 4;
    for (let i = 0; i <= ySteps; i++) {
        const y = padding.top + (chartH / ySteps) * i;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(W - padding.right, y);
        ctx.stroke();

        // Y轴标签
        const val = yMax - (yMax / ySteps) * i;
        ctx.fillStyle = '#b2bec3';
        ctx.font = '11px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(formatMinutes(val), padding.left - 8, y + 4);
    }

    // 绘制柱状图
    const barWidth = Math.min(chartW / dailyData.length * 0.6, 40);
    const gap = chartW / dailyData.length;
    // 每个日期标签约占 34px，据此算隔几天标一次（至少每天标）
    const dateStep = Math.max(1, Math.ceil(dailyData.length / Math.max(1, Math.floor(chartW / 34))));

    dailyData.forEach((d, i) => {
        const x = padding.left + gap * i + (gap - barWidth) / 2;
        const total = d.total || 0;
        const night = d.night_total || 0;
        const nap = d.nap_total || 0;

        const totalH = (total / yMax) * chartH;
        const nightH = (night / yMax) * chartH;
        const napH = (nap / yMax) * chartH;

        const yBase = padding.top + chartH;

        // 夜间睡眠（底部）
        if (nightH > 0) {
            const nightGrad = ctx.createLinearGradient(0, yBase - nightH, 0, yBase);
            nightGrad.addColorStop(0, '#6c5ce7');
            nightGrad.addColorStop(1, '#a29bfe');
            ctx.fillStyle = nightGrad;
            ctx.fillRect(x, yBase - nightH, barWidth, nightH);
        }

        // 小憩（顶部）
        if (napH > 0) {
            const napGrad = ctx.createLinearGradient(0, yBase - nightH - napH, 0, yBase - nightH);
            napGrad.addColorStop(0, '#fdcb6e');
            napGrad.addColorStop(1, '#ffeaa7');
            ctx.fillStyle = napGrad;
            ctx.fillRect(x, yBase - nightH - napH, barWidth, napH);
        }

        // X轴标签：30 天数据在窄屏上会标不下，按宽度算出隔几天标一次
        if (i % dateStep === 0) {
            const dateParts = d.date.split('-');
            const label = `${parseInt(dateParts[1])}/${parseInt(dateParts[2])}`;
            ctx.fillStyle = '#636e72';
            ctx.font = '10px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(label, x + barWidth / 2, H - padding.bottom + 16);
        }
    });

    // 图例
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'left';
    const legendY = 15;
    ctx.fillStyle = '#6c5ce7';
    ctx.fillRect(padding.left, legendY - 8, 12, 12);
    ctx.fillStyle = '#636e72';
    ctx.fillText('夜间', padding.left + 16, legendY + 2);

    ctx.fillStyle = '#fdcb6e';
    ctx.fillRect(padding.left + 60, legendY - 8, 12, 12);
    ctx.fillStyle = '#636e72';
    ctx.fillText('小憩', padding.left + 76, legendY + 2);
}

// ==================== 数据导入导出 ====================

function exportAllData() {
    window.open(App.apiBase + '/api/export-all', '_blank');
    showToast('全部数据备份中...');
}

// ==================== 批量记录 ====================

let batchRecordType = 'feeding';
let batchRecords = [];

function initBatchRecord() {
    document.getElementById('batchRecordBtn').addEventListener('click', openBatchModal);
    document.getElementById('closeBatchRecordModal').addEventListener('click', () => hideModal('batchRecordModal'));
    document.getElementById('cancelBatchForm').addEventListener('click', () => hideModal('batchRecordModal'));

    // 类型切换
    document.querySelectorAll('.batch-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.batch-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            batchRecordType = tab.dataset.type;
            batchRecords = [];
            renderBatchRows();
        });
    });

    document.getElementById('addBatchRowBtn').addEventListener('click', () => {
        batchRecords.push(createEmptyRecord(batchRecordType));
        renderBatchRows();
    });

    document.getElementById('submitBatchBtn').addEventListener('click', submitBatchRecords);
}

// ==================== 体温记录 ====================

function initTemperature() {
    document.getElementById('addTempBtn').addEventListener('click', () => {
        document.getElementById('temperatureForm').reset();
        const now = new Date();
        const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
        document.getElementById('tempTime').value = local;
        showModal('temperatureModal');
    });
    document.getElementById('closeTemperatureModal').addEventListener('click', () => hideModal('temperatureModal'));
    document.getElementById('cancelTempForm').addEventListener('click', () => hideModal('temperatureModal'));

    document.getElementById('temperatureForm').addEventListener('submit', function(e) {
        e.preventDefault();
        if (!App.currentBaby) return;

        const now = new Date();
        const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

        const data = {
            temperature: parseFloat(document.getElementById('tempValue').value),
            measure_method: document.getElementById('tempMethod').value,
            measure_time: (document.getElementById('tempTime').value || local).replace('T', ' ') + ':00',
            note: document.getElementById('tempNote').value
        };

        api(`/api/babies/${App.currentBaby}/temperatures`, {
            method: 'POST',
            body: JSON.stringify(data)
        }).then(res => {
            if (res.success) {
                showToast(res.message);
                hideModal('temperatureModal');
                loadTemperaturePage();
            } else {
                showToast.error(res.message || '保存失败');
            }
        });
    });

    // 手机横竖屏/窗口缩放时按新宽度重画（画布尺寸是按容器算的）。
    // 只在体温 Tab 正显示时重画，其它 Tab 上画了也是空白。
    let _tempResizeTimer = null;
    window.addEventListener('resize', () => {
        clearTimeout(_tempResizeTimer);
        _tempResizeTimer = setTimeout(() => {
            const view = document.getElementById('tab-temperature');
            if (!view || !view.classList.contains('active')) return;
            loadTemperaturePage();
        }, 300);
    });
}

function loadTemperaturePage() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/temperatures`).then(res => {
        if (!res.success) return;
        const temps = res.data;

        if (temps.length === 0) {
            document.getElementById('temperatureSummary').innerHTML = '<p class="empty-tip">暂无体温记录</p>';
            document.getElementById('temperatureList').innerHTML = '';
            return;
        }

        // 统计
        const latest = temps[0];
        const latestVal = parseFloat(latest.temperature);
        const isFever = latest.is_fever === 1;
        const recentFever = temps.filter(t => t.is_fever === 1).length;
        const avg = temps.length > 0 ? (temps.reduce((s, t) => s + t.temperature, 0) / temps.length).toFixed(1) : '--';

        document.getElementById('temperatureSummary').innerHTML = `
            <div class="temp-stat-card ${isFever ? 'fever' : 'normal'}">
                <span class="temp-stat-value">${latestVal}°C</span>
                <span class="temp-stat-label">最新体温</span>
            </div>
            <div class="temp-stat-card">
                <span class="temp-stat-value">${avg}°C</span>
                <span class="temp-stat-label">平均值</span>
            </div>
            <div class="temp-stat-card ${recentFever > 0 ? 'fever' : 'normal'}">
                <span class="temp-stat-value">${recentFever}</span>
                <span class="temp-stat-label">近期发热次数</span>
            </div>
        `;

        // 绘制图表
        drawTemperatureChart(temps.slice(0, 14).reverse());

        // 列表
        const list = document.getElementById('temperatureList');
        list.innerHTML = temps.slice(0, 10).map(t => `
            <div class="temp-record-item">
                <div class="temp-record-left">
                    <span class="temp-record-value ${t.is_fever == 1 ? 'fever' : 'normal'}">${escapeHtml(t.temperature)}°C</span>
                    <div>
                        <div class="temp-record-meta">${escapeHtml(t.measure_time)}</div>
                        <div class="temp-record-meta">${t.measure_method === 'ear' ? '耳温' : t.measure_method === 'armpit' ? '腋下' : t.measure_method === 'oral' ? '口腔' : '肛温'}${t.note ? ' · ' + escapeHtml(t.note) : ''}</div>
                    </div>
                </div>
                <span class="temp-record-badge ${t.is_fever == 1 ? 'fever' : 'normal'}">${t.is_fever == 1 ? '[!] 发热' : '正常'}</span>
                <button class="temp-record-delete" onclick="editTemperature(${t.id})" title="编辑">✎</button>
                <button class="temp-record-delete" onclick="deleteTemperature(${t.id})">X</button>
            </div>
        `).join('');
    });
}

// 编辑体温记录（通用弹窗，与 editSleep 同范式）
function editTemperature(id) {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/temperatures/${id}`).then(res => {
        if (!res.success) return showToast.error('加载失败');
        const r = res.data;
        const timeVal = (r.measure_time || '').slice(0, 16).replace(' ', 'T');
        showModal(`
            <div class="modal-header"><h3>编辑体温记录</h3></div>
            <div class="modal-body">
                <div class="form-group">
                    <label>体温值 (°C)</label>
                    <input type="number" id="editTempValue" step="0.1" min="35" max="42" value="${r.temperature}" required>
                </div>
                <div class="form-group">
                    <label>测量方式</label>
                    <select id="editTempMethod">
                        <option value="ear" ${r.measure_method === 'ear' ? 'selected' : ''}>耳温枪</option>
                        <option value="armpit" ${r.measure_method === 'armpit' ? 'selected' : ''}>腋下</option>
                        <option value="oral" ${r.measure_method === 'oral' ? 'selected' : ''}>口腔</option>
                        <option value="rectal" ${r.measure_method === 'rectal' ? 'selected' : ''}>肛温</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>测量时间</label>
                    <input type="datetime-local" id="editTempTime" value="${timeVal}">
                </div>
                <div class="form-group">
                    <label>备注</label>
                    <input type="text" id="editTempNote" value="${escapeHtml(r.note || '')}" placeholder="可选">
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="closeModal()">取消</button>
                <button class="btn btn-primary" onclick="saveTemperatureEdit(${r.id})">保存</button>
            </div>
        `);
    });
}

function saveTemperatureEdit(id) {
    const now = new Date();
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    const data = {
        temperature: parseFloat(document.getElementById('editTempValue')?.value),
        measure_method: document.getElementById('editTempMethod')?.value || 'ear',
        measure_time: (document.getElementById('editTempTime')?.value || local).replace('T', ' ') + ':00',
        note: document.getElementById('editTempNote')?.value || ''
    };
    api(`/api/babies/${App.currentBaby}/temperatures/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            closeModal();
            showToast.success(res.message || '已更新');
            loadTemperaturePage();
        } else {
            showToast.error(res.message || '更新失败');
        }
    });
}

function drawTemperatureChart(temps) {
    const canvas = document.getElementById('temperatureCanvas');
    if (!canvas) return;
    // 体温页已并入健康档案的「体温」Tab：Tab 没显示时 canvas 宽高是 0，
    // 画出来是一张空白图，而切过来时又会重新加载，所以这里直接跳过。
    if (canvas.offsetWidth === 0 && canvas.offsetHeight === 0) return;
    // 手机端按容器宽度重算画布（原固定 700px 会横向溢出）
    const fitted = fitCanvas(canvas, 250);
    if (!fitted) return;
    const { ctx, W, H } = fitted;

    ctx.clearRect(0, 0, W, H);

    if (!temps || temps.length === 0) {
        ctx.fillStyle = '#b2bec3';
        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('暂无数据', W / 2, H / 2);
        return;
    }

    const padding = { top: 25, right: 20, bottom: 35, left: 40 };
    const chartW = W - padding.left - padding.right;
    const chartH = H - padding.top - padding.bottom;

    // Y轴范围
    const minTemp = 35.5;
    const maxTemp = 40;

    // 发热阈值线
    const feverY = padding.top + chartH * (1 - (37.5 - minTemp) / (maxTemp - minTemp));

    // 绘制发热区域
    ctx.fillStyle = 'rgba(255, 235, 238, 0.3)';
    ctx.fillRect(padding.left, padding.top, chartW, feverY - padding.top);

    // 发热阈值线
    ctx.strokeStyle = '#EF9A9A';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(padding.left, feverY);
    ctx.lineTo(W - padding.right, feverY);
    ctx.stroke();
    ctx.setLineDash([]);

    // Y轴标签
    ctx.fillStyle = '#b2bec3';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'right';
    for (let t = 36; t <= 39; t++) {
        const y = padding.top + chartH * (1 - (t - minTemp) / (maxTemp - minTemp));
        ctx.fillText(t + '°', padding.left - 5, y + 3);
    }

    // 绘制数据线
    const step = chartW / Math.max(temps.length - 1, 1);

    // 连线
    ctx.strokeStyle = '#6c5ce7';
    ctx.lineWidth = 2;
    ctx.beginPath();
    temps.forEach((t, i) => {
        const x = padding.left + step * i;
        const y = padding.top + chartH * (1 - (t.temperature - minTemp) / (maxTemp - minTemp));
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // 数据点
    temps.forEach((t, i) => {
        const x = padding.left + step * i;
        const y = padding.top + chartH * (1 - (t.temperature - minTemp) / (maxTemp - minTemp));

        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fillStyle = t.is_fever == 1 ? '#EF5350' : '#6c5ce7';
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        ctx.stroke();
    });

    // X轴标签：按可用宽度算能放几个，窄屏自动稀化，避免叠在一起
    const maxLabels = Math.max(3, Math.floor(chartW / 44));
    const labelEvery = Math.ceil(temps.length / maxLabels);
    temps.forEach((t, i) => {
        if (i % labelEvery !== 0) return;
        const x = padding.left + step * i;
        const dateParts = t.measure_time.split(' ')[0].split('-');
        ctx.fillStyle = '#636e72';
        ctx.font = '9px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(`${dateParts[1]}/${dateParts[2]}`, x, H - padding.bottom + 14);
    });
}

function deleteTemperature(id) {
    if (!confirm('确定删除这条体温记录？')) return;
    api(`/api/temperatures/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast.success('已删除');
            loadTemperaturePage();
        }
    });
}

// ==================== 用药记录 ====================

function initMedication() {
    document.getElementById('addMedicationBtn')?.addEventListener('click', () => {
        document.getElementById('medicationForm').reset();
        const now = new Date();
        const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
        document.getElementById('medTime').value = local;
        document.getElementById('medInterval').value = 0;
        showModal('medicationModal');
    });
    document.getElementById('closeMedicationModal').addEventListener('click', () => hideModal('medicationModal'));
    document.getElementById('cancelMedicationForm').addEventListener('click', () => hideModal('medicationModal'));

    document.getElementById('medicationForm').addEventListener('submit', function(e) {
        e.preventDefault();
        if (!App.currentBaby) return;
        const now = new Date();
        const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
        const data = {
            medication_name: document.getElementById('medName').value,
            dosage: document.getElementById('medDosage').value,
            dosage_unit: document.getElementById('medDosageUnit').value,
            measure_time: (document.getElementById('medTime').value || local).replace('T', ' ') + ':00',
            next_dose_interval: parseInt(document.getElementById('medInterval').value) || 0,
            note: document.getElementById('medNote').value
        };
        api(`/api/babies/${App.currentBaby}/medications`, { method: 'POST', body: JSON.stringify(data) }).then(res => {
            if (res.success) {
                showToast(res.message);
                hideModal('medicationModal');
                loadMedicationPage();
            } else { showToast.error(res.message || '保存失败'); }
        });
    });
}

function loadMedicationPage() {
    if (!App.currentBaby) return;
    // medicationList / medicationSummary 在 index.html 中不存在（用药列表页未落地），无此判断会在渲染时抛异常
    if (!document.getElementById('medicationList')) return;
    api(`/api/babies/${App.currentBaby}/medications`).then(res => {
        if (!res.success) return;
        const meds = res.data;
        if (meds.length === 0) {
            document.getElementById('medicationSummary').innerHTML = '<p class="empty-tip">暂无用药记录</p>';
            document.getElementById('medicationList').innerHTML = '';
            return;
        }
        const today = getToday();
        const todayMeds = meds.filter(m => m.measure_time.startsWith(today)).length;
        document.getElementById('medicationSummary').innerHTML = `
            <div class="temp-stat-card"><span class="temp-stat-value">${todayMeds}</span><span class="temp-stat-label">今日用药</span></div>
            <div class="temp-stat-card"><span class="temp-stat-value">${meds.length}</span><span class="temp-stat-label">总记录数</span></div>`;
        document.getElementById('medicationList').innerHTML = meds.map(m => {
            let nextBadge = '';
            if (m.next_dose_time) {
                const nextTime = new Date(m.next_dose_time.replace(' ', 'T'));
                const isOverdue = nextTime < new Date();
                nextBadge = `<span class="medication-next ${isOverdue ? 'overdue' : ''}">${isOverdue ? '该用药了' : m.next_dose_time.slice(5, 16)}</span>`;
            }
            return `<div class="medication-card"><div class="medication-icon"></div><div class="medication-info"><div class="medication-name">${escapeHtml(m.medication_name)} ${m.dosage ? escapeHtml(m.dosage) + escapeHtml(m.dosage_unit) : ''}</div><div class="medication-meta">${escapeHtml(m.measure_time)}${m.note ? ' · ' + escapeHtml(m.note) : ''}</div></div>${nextBadge}<button class="temp-record-delete" onclick="deleteMedication(${m.id})">X</button></div>`;
        }).join('');
    });
}

function deleteMedication(id) {
    if (!confirm('确定删除？')) return;
    api(`/api/medications/${id}`, { method: 'DELETE' }).then(res => { if (res.success) { showToast.success('已删除'); loadMedicationPage(); } });
}

// ==================== 趴睡训练 ====================
// 实现主体在文件后段（initTummytimePage / loadTummytimeRecords / submitTummytimeRecord 等），
// 此处只保留统一的页面级入口，供 loadPageData 与保存/删除后的刷新调用。

function loadTummyTimePage() {
    loadTummytimeRecords();
    loadTummytimeGoal();
}

// ==================== 辅食过敏测试 ====================

function initAllergyTest() {
    // 本套过敏测试实现（allergyTestModal / allergyTestForm / allergyDate 等）在 index.html 中并不存在，
    // 实际生效的是 allergyModal + initAllergyPage。无此哨兵判断会在后续绑定抛异常。
    if (!document.getElementById('allergyTestModal')) return;
    document.getElementById('addAllergyTestBtn')?.addEventListener('click', () => {
        document.getElementById('allergyTestForm').reset();
        document.getElementById('allergyTestId').value = '';
        document.getElementById('allergyModalTitle').textContent = '添加过敏测试';
        document.getElementById('allergyDate').value = getToday();
        document.getElementById('allergyDay').value = 1;
        document.getElementById('allergyStatus').value = 'testing';
        showModal('allergyTestModal');
    });
    document.getElementById('closeAllergyTestModal')?.addEventListener('click', () => hideModal('allergyTestModal'));
    document.getElementById('cancelAllergyTestForm')?.addEventListener('click', () => hideModal('allergyTestModal'));

    document.getElementById('allergyTestForm').addEventListener('submit', function(e) {
        e.preventDefault();
        if (!App.currentBaby) return;
        const testId = document.getElementById('allergyTestId').value;
        const data = {
            food_name: document.getElementById('allergyFood').value,
            test_date: document.getElementById('allergyDate').value,
            day_number: parseInt(document.getElementById('allergyDay').value) || 1,
            has_reaction: document.getElementById('allergyStatus').value === 'failed' ? 1 : 0,
            reaction_detail: document.getElementById('allergyReaction').value,
            status: document.getElementById('allergyStatus').value,
            note: document.getElementById('allergyNote').value
        };
        const url = testId ? `/api/allergy-tests/${testId}` : `/api/babies/${App.currentBaby}/allergy-tests`;
        const method = testId ? 'PUT' : 'POST';
        api(url, { method, body: JSON.stringify(data) }).then(res => {
            if (res.success) {
                showToast(testId ? '已更新' : '添加成功');
                hideModal('allergyTestModal');
                loadAllergyPage();
            } else { showToast.error(res.message || '保存失败'); }
        });
    });
}

function loadAllergyPage() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/allergy-tests`).then(res => {
        if (!res.success) return;
        const tests = res.data;
        const list = document.getElementById('allergyList');
        if (!list) return;
        if (tests.length === 0) { list.innerHTML = '<p class="empty-tip">暂无过敏测试记录</p>'; return; }
        list.innerHTML = tests.map(t =>
            `<div class="allergy-card ${t.status}" data-id="${t.id}"><div class="allergy-icon">${t.status === 'passed' ? '' : t.status === 'failed' ? '' : ''}</div><div class="allergy-info"><div class="allergy-food">${escapeHtml(t.food_name)}</div><div class="allergy-meta">第${t.day_number}天 · ${escapeHtml(t.test_date)}${t.reaction_detail ? ' · ' + escapeHtml(t.reaction_detail) : ''}</div></div><span class="allergy-status-badge ${t.status}">${t.status === 'passed' ? '通过' : t.status === 'failed' ? '过敏' : '测试中'}</span><button class="temp-record-delete" onclick="deleteAllergyTest(${t.id}, event)">X</button></div>`
        ).join('');
        list.querySelectorAll('.allergy-card').forEach(card => {
            card.addEventListener('click', (e) => {
                if (e.target.closest('.temp-record-delete')) return;
                const test = tests.find(t => t.id == card.dataset.id);
                if (!test) return;
                document.getElementById('allergyTestId').value = test.id;
                document.getElementById('allergyFood').value = test.food_name;
                document.getElementById('allergyDate').value = test.test_date;
                document.getElementById('allergyDay').value = test.day_number || 1;
                document.getElementById('allergyStatus').value = test.status || 'testing';
                document.getElementById('allergyReaction').value = test.reaction_detail || '';
                document.getElementById('allergyNote').value = test.note || '';
                document.getElementById('allergyModalTitle').textContent = '编辑过敏测试';
                showModal('allergyTestModal');
            });
        });
    });
}

// ==================== 24小时节律图 ====================

let currentPatternType = 'feeding';

function initPattern() {
    // 事件只绑定一次，否则每次进入页面都会多挂一层监听，点一次按钮触发多次请求
    if (!App.patternInitialized) {
        App.patternInitialized = true;
        document.querySelectorAll('.pattern-type-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.pattern-type-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                currentPatternType = btn.dataset.type;
                loadPatternData();
            });
        });
    }

    loadPatternData();
}

// ==================== ASQ发育筛查 ====================

function initAsqPage() {
    const addAsqBtn = document.getElementById('addAsqBtn');
    if (addAsqBtn) {
        addAsqBtn.addEventListener('click', showAsqModal);
    }
    loadAsqScreenings();
}

function loadAsqScreenings() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/asq`).then(res => {
        if (!res.success) return;
        const container = document.getElementById('asqList');
        if (res.data.length === 0) {
            container.innerHTML = '<p class="empty-tip">暂无筛查记录</p>';
            return;
        }
        container.innerHTML = res.data.map(r => {
            const resultMap = { normal: '正常', monitor: '需关注', refer: '建议转介' };
            const resultClass = r.result === 'normal' ? 'good' : r.result === 'monitor' ? 'warn' : 'alert';
            return `
                <div class="asq-item ${resultClass}">
                    <button class="asq-delete" onclick="deleteAsq(${r.id})">X</button>
                    <div class="asq-item-header">
                        <span class="asq-age">${r.age_months}月龄</span>
                        <span class="asq-date">${r.screening_date}</span>
                    </div>
                    <div class="asq-scores">
                        <div class="asq-score-item"><span>沟通</span><strong>${r.communication_score}</strong></div>
                        <div class="asq-score-item"><span>大运动</span><strong>${r.gross_motor_score}</strong></div>
                        <div class="asq-score-item"><span>精细运动</span><strong>${r.fine_motor_score}</strong></div>
                        <div class="asq-score-item"><span>解决问题</span><strong>${r.problem_solving_score}</strong></div>
                        <div class="asq-score-item"><span>个人社交</span><strong>${r.personal_social_score}</strong></div>
                    </div>
                    <div class="asq-total">
                        <span class="asq-total-label">总分</span>
                        <span class="asq-total-value">${r.total_score}</span>
                        <span class="asq-result ${resultClass}">${resultMap[r.result]}</span>
                    </div>
                </div>
            `;
        }).join('');
    });
}

function showAsqModal() {
    if (!App.currentBaby) return;
    const html = `
        <form id="asqForm">
            <div class="form-group">
                <label>筛查月龄</label>
                <select id="asqAgeMonths">
                    <option value="2">2个月</option>
                    <option value="4">4个月</option>
                    <option value="6">6个月</option>
                    <option value="8">8个月</option>
                    <option value="10">10个月</option>
                    <option value="12" selected>12个月</option>
                    <option value="14">14个月</option>
                    <option value="16">16个月</option>
                    <option value="18">18个月</option>
                    <option value="20">20个月</option>
                    <option value="22">22个月</option>
                    <option value="24">24个月</option>
                    <option value="27">27个月</option>
                    <option value="30">30个月</option>
                    <option value="33">33个月</option>
                    <option value="36">36个月</option>
                </select>
            </div>
            <div class="asq-form-scores">
                <div class="form-group">
                    <label>沟通 (0-60)</label>
                    <input type="number" id="asqComm" min="0" max="60" value="50">
                </div>
                <div class="form-group">
                    <label>大运动 (0-60)</label>
                    <input type="number" id="asqGross" min="0" max="60" value="50">
                </div>
                <div class="form-group">
                    <label>精细运动 (0-60)</label>
                    <input type="number" id="asqFine" min="0" max="60" value="50">
                </div>
                <div class="form-group">
                    <label>解决问题 (0-60)</label>
                    <input type="number" id="asqProblem" min="0" max="60" value="50">
                </div>
                <div class="form-group">
                    <label>个人社交 (0-60)</label>
                    <input type="number" id="asqPersonal" min="0" max="60" value="50">
                </div>
            </div>
            <div class="form-group">
                <label>备注</label>
                <input type="text" id="asqNote" placeholder="可选">
            </div>
            <div class="form-actions">
                <button type="button" class="btn btn-cancel" id="cancelAsqForm">取消</button>
                <button type="submit" class="btn btn-primary">保存</button>
            </div>
        </form>
    `;
    document.getElementById('asqModalBody').innerHTML = html;
    showModal('asqModal');

    document.getElementById('cancelAsqForm')?.addEventListener('click', () => hideModal('asqModal'));
    document.getElementById('asqForm')?.addEventListener('submit', function(e) {
        e.preventDefault();
        const data = {
            baby_id: App.currentBaby,
            age_months: parseInt(document.getElementById('asqAgeMonths')?.value) || 0,
            communication_score: parseInt(document.getElementById('asqComm')?.value) || 0,
            gross_motor_score: parseInt(document.getElementById('asqGross')?.value) || 0,
            fine_motor_score: parseInt(document.getElementById('asqFine')?.value) || 0,
            problem_solving_score: parseInt(document.getElementById('asqProblem')?.value) || 0,
            personal_social_score: parseInt(document.getElementById('asqPersonal')?.value) || 0,
            note: document.getElementById('asqNote')?.value || ''
        };
        api(`/api/babies/${App.currentBaby}/asq`, {
            method: 'POST',
            body: JSON.stringify(data)
        }).then(res => {
            if (res.success) {
                hideModal('asqModal');
                showToast('筛查记录已添加');
                loadAsqScreenings();
            } else {
                showToast.error(res.message || '添加失败');
            }
        });
    });
}

function deleteAsq(id) {
    if (!confirm('确定删除这条筛查记录？')) return;
    api(`/api/asq/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast.success('已删除');
            loadAsqScreenings();
        }
    });
}

// ==================== 出牙追踪 ====================

function initTeethingPage() {
    const addToothBtn = document.getElementById('addToothBtn');
    if (addToothBtn) {
        addToothBtn.addEventListener('click', showToothModal);
    }
    loadTeethingRecords();
}

function loadTeethingRecords() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/teething`).then(res => {
        if (!res.success) return;
        const container = document.getElementById('teethingList');
        if (!container) return;
        if (res.data.length === 0) {
            container.innerHTML = '<p class="empty-tip">暂无出牙记录</p>';
        } else {
            container.innerHTML = res.data.map(r => `
                <div class="teething-item">
                    <button class="teething-delete" onclick="editTooth(${r.id})" title="编辑">✎</button>
                    <button class="teething-delete" onclick="deleteTooth(${r.id})">X</button>
                    <div class="tooth-icon"></div>
                    <div class="tooth-info">
                        <span class="tooth-name">${escapeHtml(r.tooth_name)}</span>
                        <span class="tooth-pos">${r.position === 'upper' ? '上' : '下'}${r.side === 'left' ? '左' : r.side === 'right' ? '右' : '中'}</span>
                    </div>
                    <span class="tooth-date">${escapeHtml(r.erupt_date)}</span>
                </div>
            `).join('');
        }
        renderTeethDiagram(res.data);
    });
}

// 编辑出牙记录（通用弹窗，与 editSleep 同范式）
function editTooth(id) {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/teething/${id}`).then(res => {
        if (!res.success) return showToast.error('加载失败');
        const r = res.data;
        showModal(`
            <div class="modal-header"><h3>编辑出牙记录</h3></div>
            <div class="modal-body">
                <div class="form-group">
                    <label>牙齿名称</label>
                    <input type="text" id="editToothName" value="${escapeHtml(r.tooth_name || '')}" required>
                </div>
                <div class="form-group-row">
                    <div class="form-group">
                        <label>位置</label>
                        <select id="editToothPosition">
                            <option value="upper" ${r.position === 'upper' ? 'selected' : ''}>上排</option>
                            <option value="lower" ${r.position === 'lower' ? 'selected' : ''}>下排</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>侧向</label>
                        <select id="editToothSide">
                            <option value="left" ${r.side === 'left' ? 'selected' : ''}>左侧</option>
                            <option value="center" ${r.side === 'center' ? 'selected' : ''}>中间</option>
                            <option value="right" ${r.side === 'right' ? 'selected' : ''}>右侧</option>
                        </select>
                    </div>
                </div>
                <div class="form-group">
                    <label>萌出日期</label>
                    <input type="date" id="editToothDate" value="${(r.erupt_date || '').slice(0, 10)}" required>
                </div>
                <div class="form-group">
                    <label>备注</label>
                    <input type="text" id="editToothNote" value="${escapeHtml(r.note || '')}" placeholder="可选">
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="closeModal()">取消</button>
                <button class="btn btn-primary" onclick="saveToothEdit(${r.id})">保存</button>
            </div>
        `);
    });
}

function saveToothEdit(id) {
    const data = {
        baby_id: App.currentBaby,
        tooth_name: document.getElementById('editToothName')?.value || '',
        erupt_date: document.getElementById('editToothDate')?.value || '',
        position: document.getElementById('editToothPosition')?.value || '',
        side: document.getElementById('editToothSide')?.value || '',
        note: document.getElementById('editToothNote')?.value || ''
    };
    api(`/api/babies/${App.currentBaby}/teething/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            closeModal();
            showToast.success('已更新');
            loadTeethingRecords();
        } else {
            showToast.error(res.message || '更新失败');
        }
    });
}

function renderTeethDiagram(records) {
    // 乳牙名称映射
    const toothNames = {
        upper: ['右上中切牙', '右上侧切牙', '右上尖牙', '右上第一磨牙', '右上第二磨牙',
                '左上中切牙', '左上侧切牙', '左上尖牙', '左上第一磨牙', '左上第二磨牙'],
        lower: ['右下中切牙', '右下侧切牙', '右下尖牙', '右下第一磨牙', '右下第二磨牙',
                '左下中切牙', '左下侧切牙', '左下尖牙', '左下第一磨牙', '左下第二磨牙']
    };

    ['upper', 'lower'].forEach(pos => {
        ['Right', 'Left'].forEach(side => {
            const container = document.getElementById(pos + side);
            if (!container) return;
            const teeth = toothNames[pos].filter((_, i) => {
                if (side === 'Right') return i < 5;
                return i >= 5;
            });
            container.innerHTML = teeth.map((name, i) => {
                const erupted = records.some(r => r.tooth_name === name);
                return `<div class="tooth-cell ${erupted ? 'erupted' : ''}" title="${name}">${erupted ? '' : '○'}</div>`;
            }).join('');
        });
    });
}

function showToothModal() {
    if (!App.currentBaby) return;
    const html = `
        <form id="toothForm">
            <div class="form-group">
                <label>牙齿名称</label>
                <select id="toothName">
                    <option value="">选择牙齿</option>
                    <optgroup label="上排">
                        <option value="右上中切牙">右上中切牙</option>
                        <option value="右上侧切牙">右上侧切牙</option>
                        <option value="右上尖牙">右上尖牙</option>
                        <option value="右上第一磨牙">右上第一磨牙</option>
                        <option value="右上第二磨牙">右上第二磨牙</option>
                        <option value="左上中切牙">左上中切牙</option>
                        <option value="左上侧切牙">左上侧切牙</option>
                        <option value="左上尖牙">左上尖牙</option>
                        <option value="左上第一磨牙">左上第一磨牙</option>
                        <option value="左上第二磨牙">左上第二磨牙</option>
                    </optgroup>
                    <optgroup label="下排">
                        <option value="右下中切牙">右下中切牙</option>
                        <option value="右下侧切牙">右下侧切牙</option>
                        <option value="右下尖牙">右下尖牙</option>
                        <option value="右下第一磨牙">右下第一磨牙</option>
                        <option value="右下第二磨牙">右下第二磨牙</option>
                        <option value="左下中切牙">左下中切牙</option>
                        <option value="左下侧切牙">左下侧切牙</option>
                        <option value="左下尖牙">左下尖牙</option>
                        <option value="左下第一磨牙">左下第一磨牙</option>
                        <option value="左下第二磨牙">左下第二磨牙</option>
                    </optgroup>
                </select>
            </div>
            <div class="form-group-row">
                <div class="form-group">
                    <label>位置</label>
                    <select id="toothPosition">
                        <option value="upper">上排</option>
                        <option value="lower">下排</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>侧向</label>
                    <select id="toothSide">
                        <option value="left">左侧</option>
                        <option value="center">中间</option>
                        <option value="right">右侧</option>
                    </select>
                </div>
            </div>
            <div class="form-group">
                <label>萌出日期</label>
                <input type="date" id="toothDate" required>
            </div>
            <div class="form-group">
                <label>备注</label>
                <input type="text" id="toothNote" placeholder="可选">
            </div>
            <div class="form-actions">
                <button type="button" class="btn btn-cancel" id="cancelToothForm">取消</button>
                <button type="submit" class="btn btn-primary">保存</button>
            </div>
        </form>
    `;
    document.getElementById('toothModalBody').innerHTML = html;
    showModal('toothModal');

    // 设置默认日期
    const today = new Date();
    const toothDateEl = document.getElementById('toothDate');
    if (toothDateEl) toothDateEl.value = getToday();

    // 牙齿名称变化时自动更新位置和侧向
    document.getElementById('toothName')?.addEventListener('change', function() {
        const name = this.value;
        const toothPosition = document.getElementById('toothPosition');
        const toothSide = document.getElementById('toothSide');
        if (name.startsWith('右上') || name.startsWith('左上')) {
            if (toothPosition) toothPosition.value = 'upper';
        } else {
            if (toothPosition) toothPosition.value = 'lower';
        }
        if (name.includes('右')) {
            if (toothSide) toothSide.value = 'right';
        } else if (name.includes('左')) {
            if (toothSide) toothSide.value = 'left';
        } else {
            if (toothSide) toothSide.value = 'center';
        }
    });

    document.getElementById('cancelToothForm')?.addEventListener('click', () => hideModal('toothModal'));
    document.getElementById('toothForm')?.addEventListener('submit', function(e) {
        e.preventDefault();
        const data = {
            baby_id: App.currentBaby,
            tooth_name: document.getElementById('toothName')?.value || '',
            erupt_date: document.getElementById('toothDate')?.value || '',
            position: document.getElementById('toothPosition')?.value || '',
            side: document.getElementById('toothSide')?.value || '',
            note: document.getElementById('toothNote')?.value || ''
        };
        api(`/api/babies/${App.currentBaby}/teething`, {
            method: 'POST',
            body: JSON.stringify(data)
        }).then(res => {
            if (res.success) {
                hideModal('toothModal');
                showToast('出牙记录已添加');
                loadTeethingRecords();
            } else {
                showToast.error(res.message || '添加失败');
            }
        });
    });
}

function deleteTooth(id) {
    if (!confirm('确定删除这条出牙记录？')) return;
    api(`/api/teething/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast.success('已删除');
            loadTeethingRecords();
        }
    });
}

// ==================== 体检记录 ====================

function initCheckupPage() {
    const addCheckupBtn = document.getElementById('addCheckupBtn');
    if (addCheckupBtn) {
        addCheckupBtn.addEventListener('click', showCheckupModal);
    }
    loadCheckupRecords();
}

function showCheckupModal() {
    if (!App.currentBaby) return;
    const today = getToday();
    const html = `
        <form id="checkupForm">
            <div class="form-group">
                <label>记录日期 *</label>
                <input type="date" id="checkupDate" value="${today}" required>
            </div>
            <div class="form-group">
                <label>标题 *</label>
                <input type="text" id="checkupTitle" placeholder="如：6月龄健康体检" required>
            </div>
            <div class="form-group">
                <label>类型</label>
                <select id="checkupType">
                    <option value="routine">常规体检</option>
                    <option value="vaccine">疫苗接种</option>
                    <option value="dental">口腔检查</option>
                    <option value="eye">视力检查</option>
                    <option value="blood">血常规</option>
                    <option value="other">其他</option>
                </select>
            </div>
            <div class="form-group-row">
                <div class="form-group">
                    <label>身高 (cm)</label>
                    <input type="number" step="0.1" id="checkupHeight" placeholder="可选">
                </div>
                <div class="form-group">
                    <label>体重 (kg)</label>
                    <input type="number" step="0.01" id="checkupWeight" placeholder="可选">
                </div>
            </div>
            <div class="form-group-row">
                <div class="form-group">
                    <label>头围 (cm)</label>
                    <input type="number" step="0.1" id="checkupHead" placeholder="可选">
                </div>
                <div class="form-group">
                    <label>BMI</label>
                    <input type="number" step="0.1" id="checkupBmi" placeholder="自动计算">
                </div>
            </div>
            <div class="form-group-row">
                <div class="form-group">
                    <label>医院</label>
                    <input type="text" id="checkupHospital" placeholder="可选">
                </div>
                <div class="form-group">
                    <label>医生</label>
                    <input type="text" id="checkupDoctor" placeholder="可选">
                </div>
            </div>
            <div class="form-group">
                <label>诊断结果</label>
                <textarea id="checkupDiagnosis" rows="2" placeholder="可选"></textarea>
            </div>
            <div class="form-group">
                <label>医嘱建议</label>
                <textarea id="checkupAdvice" rows="2" placeholder="可选"></textarea>
            </div>
            <div class="form-group">
                <label>下次就诊日期</label>
                <input type="date" id="checkupNext" placeholder="可选">
            </div>
            <div class="form-group">
                <label>备注</label>
                <input type="text" id="checkupNote" placeholder="可选">
            </div>
            <div class="form-actions">
                <button type="button" class="btn btn-cancel" id="cancelCheckupForm">取消</button>
                <button type="submit" class="btn btn-primary">保存</button>
            </div>
        </form>
    `;
    const checkupBody = document.getElementById('checkupModalBody');
    if (checkupBody) checkupBody.innerHTML = html;
    showModal('checkupModal');

    document.getElementById('cancelCheckupForm')?.addEventListener('click', () => hideModal('checkupModal'));
    document.getElementById('checkupForm')?.addEventListener('submit', function(e) {
        e.preventDefault();
        const heightVal = document.getElementById('checkupHeight')?.value || null;
        const weightVal = document.getElementById('checkupWeight')?.value || null;
        let bmiVal = document.getElementById('checkupBmi')?.value || null;
        // 自动计算 BMI
        if (!bmiVal && heightVal && weightVal) {
            try {
                const h = parseFloat(heightVal) / 100;
                bmiVal = (parseFloat(weightVal) / (h * h)).toFixed(1);
            } catch(e) {}
        }
        const data = {
            record_date: document.getElementById('checkupDate')?.value || '',
            record_type: document.getElementById('checkupType')?.value || 'routine',
            title: document.getElementById('checkupTitle')?.value || '',
            height: heightVal,
            weight: weightVal,
            head_circumference: document.getElementById('checkupHead')?.value || null,
            bmi: bmiVal,
            hospital: document.getElementById('checkupHospital')?.value || '',
            doctor: document.getElementById('checkupDoctor')?.value || '',
            diagnosis: document.getElementById('checkupDiagnosis')?.value || '',
            advice: document.getElementById('checkupAdvice')?.value || '',
            next_visit_date: document.getElementById('checkupNext')?.value || '',
            note: document.getElementById('checkupNote')?.value || ''
        };
        api(`/api/health/records/${App.currentBaby}`, {
            method: 'POST',
            body: JSON.stringify(data)
        }).then(res => {
            if (res.success) {
                hideModal('checkupModal');
                showToast.success('体检记录已保存');
                loadCheckupRecords();
            } else {
                showToast.error(res.message || '保存失败');
            }
        }).catch(() => {
            showToast.error('网络错误，请重试');
        });
    });
}

// ==================== 辅食添加记录 ====================

function initSolidFoodPage() {
    const addBtn = document.getElementById('addSolidFoodBtn');
    if (addBtn) {
        addBtn.addEventListener('click', showSolidFoodModal);
    }
    // 分类筛选
    document.querySelectorAll('.sf-cat-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.sf-cat-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadSolidFoodRecords(btn.dataset.cat);
        });
    });
    loadSolidFoodRecords();
    loadSolidFoodStats();
}

function loadSolidFoodRecords(category) {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/solid-food`).then(res => {
        if (!res.success) return;
        let records = res.data;
        if (category && category !== 'all') {
            records = records.filter(r => r.food_category === category);
        }
        const container = document.getElementById('solidfoodList');
        if (!container) return;
        if (records.length === 0) {
            container.innerHTML = '<p class="empty-tip">暂无辅食记录</p>';
            return;
        }
        const catMap = { vegetable: '', fruit: '', meat: '', grain: '', dairy: '', other: '' };
        const reactionMap = { none: ' 无反应', mild: '[!] 轻微反应', severe: ' 严重反应' };
        container.innerHTML = records.map(r => `
            <div class="sf-item ${r.reaction !== 'none' ? 'has-reaction' : ''}">
                <button class="sf-delete" onclick="editSolidFood(${r.id})" title="编辑">✎</button>
                <button class="sf-delete" onclick="deleteSolidFood(${r.id})">X</button>
                <div class="sf-icon">${catMap[r.food_category] || ''}</div>
                <div class="sf-info">
                    <span class="sf-name">${escapeHtml(r.food_name)} ${r.is_favorite ? '' : ''}</span>
                    <span class="sf-date">${escapeHtml(r.first_try_date)} ${r.amount ? '(' + escapeHtml(r.amount) + ')' : ''}</span>
                </div>
                <span class="sf-reaction ${escapeHtml(r.reaction)}">${reactionMap[r.reaction] || ''}</span>
            </div>
        `).join('');
    });
}

// 编辑辅食记录（通用弹窗，与 editSleep 同范式）
function editSolidFood(id) {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/solid-food/${id}`).then(res => {
        if (!res.success) return showToast.error('加载失败');
        const r = res.data;
        showModal(`
            <div class="modal-header"><h3>编辑辅食记录</h3></div>
            <div class="modal-body">
                <div class="form-group">
                    <label>食物名称</label>
                    <input type="text" id="editSfName" value="${escapeHtml(r.food_name || '')}" required>
                </div>
                <div class="form-group">
                    <label>食物类别</label>
                    <select id="editSfCategory">
                        <option value="vegetable" ${r.food_category === 'vegetable' ? 'selected' : ''}>蔬菜</option>
                        <option value="fruit" ${r.food_category === 'fruit' ? 'selected' : ''}>水果</option>
                        <option value="meat" ${r.food_category === 'meat' ? 'selected' : ''}>肉类</option>
                        <option value="grain" ${r.food_category === 'grain' ? 'selected' : ''}>谷物</option>
                        <option value="dairy" ${r.food_category === 'dairy' ? 'selected' : ''}>奶制品</option>
                        <option value="other" ${r.food_category === 'other' ? 'selected' : ''}>其他</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>首次尝试日期</label>
                    <input type="date" id="editSfDate" value="${(r.first_try_date || '').slice(0, 10)}" required>
                </div>
                <div class="form-group">
                    <label>分量</label>
                    <input type="text" id="editSfAmount" value="${escapeHtml(r.amount || '')}" placeholder="如：1勺、30g">
                </div>
                <div class="form-group">
                    <label>反应</label>
                    <select id="editSfReaction">
                        <option value="none" ${r.reaction === 'none' ? 'selected' : ''}>无反应</option>
                        <option value="mild" ${r.reaction === 'mild' ? 'selected' : ''}>轻微反应</option>
                        <option value="severe" ${r.reaction === 'severe' ? 'selected' : ''}>严重反应</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>反应详情</label>
                    <input type="text" id="editSfReactionDetail" value="${escapeHtml(r.reaction_detail || '')}" placeholder="可选">
                </div>
                <div class="form-group">
                    <label>
                        <input type="checkbox" id="editSfFavorite" ${r.is_favorite ? 'checked' : ''}> 宝宝最爱
                    </label>
                </div>
                <div class="form-group">
                    <label>备注</label>
                    <input type="text" id="editSfNote" value="${escapeHtml(r.note || '')}" placeholder="可选">
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="closeModal()">取消</button>
                <button class="btn btn-primary" onclick="saveSolidFoodEdit(${r.id})">保存</button>
            </div>
        `);
    });
}

function saveSolidFoodEdit(id) {
    const data = {
        baby_id: App.currentBaby,
        food_name: document.getElementById('editSfName')?.value || '',
        food_category: document.getElementById('editSfCategory')?.value || 'vegetable',
        first_try_date: document.getElementById('editSfDate')?.value || '',
        amount: document.getElementById('editSfAmount')?.value || '',
        reaction: document.getElementById('editSfReaction')?.value || 'none',
        reaction_detail: document.getElementById('editSfReactionDetail')?.value || '',
        is_favorite: document.getElementById('editSfFavorite')?.checked ? 1 : 0,
        note: document.getElementById('editSfNote')?.value || ''
    };
    api(`/api/babies/${App.currentBaby}/solid-food/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            closeModal();
            showToast.success('已更新');
            loadSolidFoodRecords();
            loadSolidFoodStats();
        } else {
            showToast.error(res.message || '更新失败');
        }
    });
}

function loadSolidFoodStats() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/solid-food/stats`).then(res => {
        if (!res.success) return;
        const data = res.data;
        const container = document.getElementById('solidfoodStats');
        if (!container) return;
        container.innerHTML = `
            <div class="sf-stat-card">
                <span class="sf-stat-value">${data.total}</span>
                <span class="sf-stat-label">已尝试食物</span>
            </div>
            <div class="sf-stat-card">
                <span class="sf-stat-value">${data.favorites}</span>
                <span class="sf-stat-label">宝宝最爱</span>
            </div>
            <div class="sf-stat-card">
                <span class="sf-stat-value">${data.reactions}</span>
                <span class="sf-stat-label">过敏/不良反应</span>
            </div>
        `;
    });
}

function showSolidFoodModal() {
    if (!App.currentBaby) return;
    const html = `
        <form id="solidFoodForm">
            <div class="form-group">
                <label>食物名称</label>
                <input type="text" id="sfName" required placeholder="如：胡萝卜泥">
            </div>
            <div class="form-group">
                <label>食物类别</label>
                <select id="sfCategory">
                    <option value="vegetable"> 蔬菜</option>
                    <option value="fruit"> 水果</option>
                    <option value="meat"> 肉类</option>
                    <option value="grain"> 谷物</option>
                    <option value="dairy"> 奶制品</option>
                    <option value="other"> 其他</option>
                </select>
            </div>
            <div class="form-group">
                <label>首次尝试日期</label>
                <input type="date" id="sfDate" required>
            </div>
            <div class="form-group">
                <label>分量</label>
                <input type="text" id="sfAmount" placeholder="如：1勺、30g">
            </div>
            <div class="form-group">
                <label>反应</label>
                <select id="sfReaction">
                    <option value="none"> 无反应</option>
                    <option value="mild">[!] 轻微反应</option>
                    <option value="severe"> 严重反应</option>
                </select>
            </div>
            <div class="form-group">
                <label>反应详情</label>
                <input type="text" id="sfReactionDetail" placeholder="可选">
            </div>
            <div class="form-group">
                <label>
                    <input type="checkbox" id="sfFavorite"> 宝宝最爱 
                </label>
            </div>
            <div class="form-group">
                <label>备注</label>
                <input type="text" id="sfNote" placeholder="可选">
            </div>
            <div class="form-actions">
                <button type="button" class="btn btn-cancel" id="cancelSfForm">取消</button>
                <button type="submit" class="btn btn-primary">保存</button>
            </div>
        </form>
    `;
    document.getElementById('sfModalBody').innerHTML = html;
    showModal('sfModal');

    const sfDateEl = document.getElementById('sfDate');
    if (sfDateEl) sfDateEl.value = getToday();
    document.getElementById('cancelSfForm')?.addEventListener('click', () => hideModal('sfModal'));
    document.getElementById('solidFoodForm')?.addEventListener('submit', function(e) {
        e.preventDefault();
        const data = {
            baby_id: App.currentBaby,
            food_name: document.getElementById('sfName')?.value || '',
            food_category: document.getElementById('sfCategory')?.value || '',
            first_try_date: document.getElementById('sfDate')?.value || '',
            amount: document.getElementById('sfAmount')?.value || '',
            reaction: document.getElementById('sfReaction')?.value || '',
            reaction_detail: document.getElementById('sfReactionDetail')?.value || '',
            is_favorite: document.getElementById('sfFavorite')?.checked ? 1 : 0,
            note: document.getElementById('sfNote')?.value || ''
        };
        api(`/api/babies/${App.currentBaby}/solid-food`, {
            method: 'POST',
            body: JSON.stringify(data)
        }).then(res => {
            if (res.success) {
                hideModal('sfModal');
                showToast('辅食记录已添加');
                loadSolidFoodRecords();
                loadSolidFoodStats();
            } else {
                showToast.error(res.message || '添加失败');
            }
        });
    });
}

function deleteSolidFood(id) {
    if (!confirm('确定删除这条辅食记录？')) return;
    api(`/api/solid-food/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast.success('已删除');
            loadSolidFoodRecords();
            loadSolidFoodStats();
        }
    });
}

// ==================== 发育飞跃期 ====================

function initLeapPage() {
    const addBtn = document.getElementById('addLeapBtn');
    if (addBtn) {
        addBtn.addEventListener('click', showLeapModal);
    }
    loadLeapRecords();
}

function loadLeapRecords() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/leaps`).then(res => {
        if (!res.success) return;
        const records = res.data;
        // 获取预测
        api(`/api/babies/${App.currentBaby}/leaps/predict`).then(predRes => {
            if (!predRes.success) return;
            const predictions = predRes.data;
            const container = document.getElementById('leapTimeline');

            if (predictions.length === 0) {
                container.innerHTML = '<p class="empty-tip">请先设置宝宝生日</p>';
                return;
            }

            container.innerHTML = predictions.map(p => {
                const record = records.find(r => r.leap_number === p.leap_number);
                const status = record ? (record.is_completed ? 'completed' : 'ongoing') : 'upcoming';
                const statusText = record ? (record.is_completed ? '已完成' : '进行中') : '即将到来';
                return `
                    <div class="leap-item ${status}">
                        <div class="leap-number">第${p.leap_number}次</div>
                        <div class="leap-info">
                            <div class="leap-desc">${p.description}</div>
                            <div class="leap-dates">${p.start_date} ~ ${p.end_date}</div>
                        </div>
                        <div class="leap-status">${statusText}</div>
                        ${record ? `<button class="leap-delete" onclick="deleteLeap(${record.id})">X</button>` : ''}
                    </div>
                `;
            }).join('');
        });
    });
}

function showLeapModal() {
    if (!App.currentBaby) return;
    const html = `
        <form id="leapForm">
            <div class="form-group">
                <label>飞跃期数</label>
                <select id="leapNumber">
                    <option value="1">第1次飞跃</option>
                    <option value="2">第2次飞跃</option>
                    <option value="3">第3次飞跃</option>
                    <option value="4">第4次飞跃</option>
                    <option value="5">第5次飞跃</option>
                    <option value="6">第6次飞跃</option>
                    <option value="7">第7次飞跃</option>
                    <option value="8">第8次飞跃</option>
                    <option value="9">第9次飞跃</option>
                    <option value="10">第10次飞跃</option>
                </select>
            </div>
            <div class="form-group-row">
                <div class="form-group">
                    <label>开始日期</label>
                    <input type="date" id="leapStartDate" required>
                </div>
                <div class="form-group">
                    <label>结束日期</label>
                    <input type="date" id="leapEndDate">
                </div>
            </div>
            <div class="form-group">
                <label>
                    <input type="checkbox" id="leapCompleted"> 已完成飞跃
                </label>
            </div>
            <div class="form-group">
                <label>备注</label>
                <input type="text" id="leapNote" placeholder="记录宝宝的新能力">
            </div>
            <div class="form-actions">
                <button type="button" class="btn btn-cancel" id="cancelLeapForm">取消</button>
                <button type="submit" class="btn btn-primary">保存</button>
            </div>
        </form>
    `;
    document.getElementById('leapModalBody').innerHTML = html;
    showModal('leapModal');

    const leapStartDateEl = document.getElementById('leapStartDate');
    if (leapStartDateEl) leapStartDateEl.value = getToday();
    document.getElementById('cancelLeapForm')?.addEventListener('click', () => hideModal('leapModal'));
    document.getElementById('leapForm')?.addEventListener('submit', function(e) {
        e.preventDefault();
        const data = {
            baby_id: App.currentBaby,
            leap_number: parseInt(document.getElementById('leapNumber')?.value) || 0,
            start_date: document.getElementById('leapStartDate')?.value || '',
            end_date: document.getElementById('leapEndDate')?.value || null,
            is_completed: document.getElementById('leapCompleted')?.checked ? 1 : 0,
            note: document.getElementById('leapNote')?.value || ''
        };
        api(`/api/babies/${App.currentBaby}/leaps`, {
            method: 'POST',
            body: JSON.stringify(data)
        }).then(res => {
            if (res.success) {
                hideModal('leapModal');
                showToast('飞跃期记录已添加');
                loadLeapRecords();
            } else {
                showToast.error(res.message || '添加失败');
            }
        });
    });
}

function deleteLeap(id) {
    if (!confirm('确定删除这条飞跃期记录？')) return;
    api(`/api/leaps/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast.success('已删除');
            loadLeapRecords();
        }
    });
}

// ==================== 囟门追踪 ====================

function initFontanellePage() {
    const addBtn = document.getElementById('addFontanelleBtn');
    if (addBtn) {
        addBtn.addEventListener('click', showFontanelleModal);
    }
    loadFontanelleRecords();
}

function loadFontanelleRecords() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/fontanelle`).then(res => {
        if (!res.success) return;
        const container = document.getElementById('fontanelleList');
        if (res.data.length === 0) {
            container.innerHTML = '<p class="empty-tip">暂无囟门记录</p>';
            document.getElementById('fontanelleChart').style.display = 'none';
            return;
        }
        container.innerHTML = res.data.map(r => {
            const antStatus = { open: '未闭', closing: '闭合中', closed: '已闭合' };
            const postStatus = { open: '未闭', closing: '闭合中', closed: '已闭合' };
            return `
                <div class="fontanelle-item">
                    <button class="fontanelle-delete" onclick="deleteFontanelle(${r.id})">X</button>
                    <div class="fontanelle-date">${escapeHtml(r.check_date)}</div>
                    <div class="fontanelle-values">
                        ${r.anterior_size ? `<span class="fontanelle-size">前囟: ${escapeHtml(String(r.anterior_size))}cm</span>` : ''}
                        <span class="fontanelle-status anterior-${r.anterior_status}">前: ${antStatus[r.anterior_status] || r.anterior_status}</span>
                        <span class="fontanelle-status posterior-${r.posterior_status}">后: ${postStatus[r.posterior_status] || r.posterior_status}</span>
                    </div>
                    ${r.note ? `<div class="fontanelle-note">${escapeHtml(r.note)}</div>` : ''}
                </div>
            `;
        }).join('');

        // 绘制前囟大小趋势图
        drawFontanelleChart(res.data);
    });
}

function drawFontanelleChart(records) {
    const canvas = document.getElementById('fontanelleChart');
    if (!canvas) return;

    // 过滤有前囟大小数据的记录
    const dataPoints = records
        .filter(r => r.anterior_size && r.anterior_size > 0)
        .sort((a, b) => new Date(a.check_date) - new Date(b.check_date));

    if (dataPoints.length === 0) {
        canvas.style.display = 'none';
        return;
    }

    canvas.style.display = 'block';
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = 200 * dpr;
    ctx.scale(dpr, dpr);
    const width = rect.width;
    const height = 200;

    ctx.clearRect(0, 0, width, height);

    const padding = { top: 20, right: 20, bottom: 30, left: 40 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    // 计算范围
    const sizes = dataPoints.map(r => parseFloat(r.anterior_size));
    const maxSize = Math.max(...sizes, 3);
    const minSize = Math.min(...sizes, 0);

    // 绘制坐标轴
    ctx.strokeStyle = '#E0E0E0';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padding.left, padding.top);
    ctx.lineTo(padding.left, height - padding.bottom);
    ctx.lineTo(width - padding.right, height - padding.bottom);
    ctx.stroke();

    // Y轴标签
    ctx.fillStyle = '#999';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
        const val = minSize + (maxSize - minSize) * (i / 4);
        const y = height - padding.bottom - chartH * (i / 4);
        ctx.fillText(val.toFixed(1), padding.left - 5, y + 4);
        ctx.strokeStyle = '#F0F0F0';
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(width - padding.right, y);
        ctx.stroke();
    }

    // 绘制数据线
    if (dataPoints.length > 1) {
        ctx.strokeStyle = '#0288D1';
        ctx.lineWidth = 2;
        ctx.beginPath();
        dataPoints.forEach((r, i) => {
            const x = padding.left + (chartW * i / (dataPoints.length - 1));
            const y = height - padding.bottom - chartH * ((r.anterior_size - minSize) / (maxSize - minSize || 1));
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
    }

    // 绘制数据点
    dataPoints.forEach((r, i) => {
        const x = padding.left + (chartW * i / Math.max(dataPoints.length - 1, 1));
        const y = height - padding.bottom - chartH * ((r.anterior_size - minSize) / (maxSize - minSize || 1));

        ctx.fillStyle = '#0288D1';
        ctx.beginPath();
        ctx.arc(x, y, 5, 0, Math.PI * 2);
        ctx.fill();

        // 数值标签
        ctx.fillStyle = '#333';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(r.anterior_size + 'cm', x, y - 10);

        // X轴日期
        ctx.fillStyle = '#999';
        ctx.fillText(r.check_date.slice(5), x, height - padding.bottom + 15);
    });
}

function showFontanelleModal() {
    if (!App.currentBaby) return;
    const html = `
        <form id="fontanelleForm">
            <div class="form-group">
                <label>检查日期</label>
                <input type="date" id="fontanelleDate" required>
            </div>
            <div class="form-group">
                <label>前囟大小 (cm)</label>
                <input type="number" step="0.1" id="anteriorSize" placeholder="可选">
            </div>
            <div class="form-group-row">
                <div class="form-group">
                    <label>前囟状态</label>
                    <select id="anteriorStatus">
                        <option value="open">未闭</option>
                        <option value="closing">闭合中</option>
                        <option value="closed">已闭合</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>后囟状态</label>
                    <select id="posteriorStatus">
                        <option value="open">未闭</option>
                        <option value="closing">闭合中</option>
                        <option value="closed">已闭合</option>
                    </select>
                </div>
            </div>
            <div class="form-group">
                <label>备注</label>
                <input type="text" id="fontanelleNote" placeholder="可选">
            </div>
            <div class="form-actions">
                <button type="button" class="btn btn-cancel" id="cancelFontanelleForm">取消</button>
                <button type="submit" class="btn btn-primary">保存</button>
            </div>
        </form>
    `;
    document.getElementById('fontanelleModalBody').innerHTML = html;
    showModal('fontanelleModal');

    const fontanelleDateEl = document.getElementById('fontanelleDate');
    if (fontanelleDateEl) fontanelleDateEl.value = getToday();
    document.getElementById('cancelFontanelleForm')?.addEventListener('click', () => hideModal('fontanelleModal'));
    document.getElementById('fontanelleForm')?.addEventListener('submit', function(e) {
        e.preventDefault();
        const data = {
            baby_id: App.currentBaby,
            check_date: document.getElementById('fontanelleDate')?.value || '',
            anterior_size: document.getElementById('anteriorSize')?.value || null,
            anterior_status: document.getElementById('anteriorStatus')?.value || '',
            posterior_status: document.getElementById('posteriorStatus')?.value || '',
            note: document.getElementById('fontanelleNote')?.value || ''
        };
        api(`/api/babies/${App.currentBaby}/fontanelle`, {
            method: 'POST',
            body: JSON.stringify(data)
        }).then(res => {
            if (res.success) {
                hideModal('fontanelleModal');
                showToast('囟门记录已添加');
                loadFontanelleRecords();
            } else {
                showToast.error(res.message || '添加失败');
            }
        });
    });
}

function deleteFontanelle(id) {
    if (!confirm('确定删除这条囟门记录？')) return;
    api(`/api/fontanelle/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast.success('已删除');
            loadFontanelleRecords();
        }
    });
}

function loadPatternData() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/pattern/${currentPatternType}?days=7`).then(res => {
        if (!res.success) return;
        const data = res.data;

        if (currentPatternType === 'feeding') {
            // 统计
            const totalCount = data.reduce((s, d) => s + d.count, 0);
            const peak = data.reduce((max, d) => d.count > max.count ? d : max, data[0]);
            const totalAmount = data.reduce((s, d) => s + d.amount, 0);

            document.getElementById('patternStats').innerHTML = `
                <div class="pattern-stat-card">
                    <span class="pattern-stat-value">${totalCount.toFixed(0)}</span>
                    <span class="pattern-stat-label">7天喂奶次数</span>
                </div>
                <div class="pattern-stat-card">
                    <span class="pattern-stat-value">${peak.hour}:00</span>
                    <span class="pattern-stat-label">高峰时段</span>
                </div>
                <div class="pattern-stat-card">
                    <span class="pattern-stat-value">${totalAmount.toFixed(0)}ml</span>
                    <span class="pattern-stat-label">总奶量</span>
                </div>
                <div class="pattern-stat-card">
                    <span class="pattern-stat-value">${(totalAmount / 7).toFixed(0)}ml</span>
                    <span class="pattern-stat-label">日均奶量</span>
                </div>
            `;

            drawFeedingPattern(data);

            // 分析
            const nightFeeds = data.slice(0, 6).reduce((s, d) => s + d.count, 0);
            const dayFeeds = data.slice(6, 20).reduce((s, d) => s + d.count, 0);
            const insight = nightFeeds > dayFeeds * 0.3
                ? '您的宝宝夜间喂奶较频繁，可尝试逐渐减少夜奶次数。'
                : '宝宝喂奶节律良好，白天喂奶充足。';

            document.getElementById('patternInsight').innerHTML = `
                <strong> 喂养节律分析</strong><br>
                ${insight}<br>
                每日平均喂奶 ${(totalCount / 7).toFixed(1)} 次，总奶量 ${totalAmount.toFixed(0)}ml。
                高峰时段集中在 ${peak.hour}:00 左右。`;

        } else if (currentPatternType === 'sleep') {
            const totalSleep = data.reduce((s, d) => s + d.total, 0);
            const totalNap = data.reduce((s, d) => s + d.nap, 0);
            const totalNight = data.reduce((s, d) => s + d.night, 0);
            const peak = data.reduce((max, d) => d.total > max.total ? d : max, data[0]);

            document.getElementById('patternStats').innerHTML = `
                <div class="pattern-stat-card">
                    <span class="pattern-stat-value">${(totalSleep / 7 / 60).toFixed(1)}h</span>
                    <span class="pattern-stat-label">日均睡眠</span>
                </div>
                <div class="pattern-stat-card">
                    <span class="pattern-stat-value">${(totalNight / 7 / 60).toFixed(1)}h</span>
                    <span class="pattern-stat-label">日均夜间睡眠</span>
                </div>
                <div class="pattern-stat-card">
                    <span class="pattern-stat-value">${(totalNap / 7 / 60).toFixed(1)}h</span>
                    <span class="pattern-stat-label">日均小憩</span>
                </div>
                <div class="pattern-stat-card">
                    <span class="pattern-stat-value">${peak.hour}:00</span>
                    <span class="pattern-stat-label">睡眠高峰</span>
                </div>
            `;

            drawSleepPattern(data);

            const insight = totalNap > totalNight
                ? '宝宝白天睡眠较多，可适当增加白天活动量。'
                : '宝宝睡眠分布合理，夜间睡眠充足。';

            document.getElementById('patternInsight').innerHTML = `
                <strong> 睡眠节律分析</strong><br>
                ${insight}<br>
                每日平均睡眠 ${(totalSleep / 7 / 60).toFixed(1)} 小时，
                夜间 ${(totalNight / 7 / 60).toFixed(1)} 小时，小憩 ${(totalNap / 7 / 60).toFixed(1)} 小时。`;
        }
    });
}

function drawFeedingPattern(data) {
    const canvas = document.getElementById('patternCanvas');
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;

    ctx.clearRect(0, 0, W, H);

    const padding = { top: 30, right: 20, bottom: 40, left: 50 };
    const chartW = W - padding.left - padding.right;
    const chartH = H - padding.top - padding.bottom;

    const maxCount = Math.max(...data.map(d => d.count), 1);

    // Y轴
    ctx.strokeStyle = '#eee';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
        const y = padding.top + (chartH / 4) * i;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(W - padding.right, y);
        ctx.stroke();

        ctx.fillStyle = '#b2bec3';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText((maxCount * (4 - i) / 4).toFixed(1), padding.left - 5, y + 3);
    }

    // 柱状图
    const barWidth = chartW / 24 * 0.7;
    const gap = chartW / 24;

    data.forEach((d, i) => {
        const x = padding.left + gap * i + (gap - barWidth) / 2;
        const barH = (d.count / maxCount) * chartH;
        const y = padding.top + chartH - barH;

        const grad = ctx.createLinearGradient(0, y, 0, y + barH);
        grad.addColorStop(0, '#e85a8f');
        grad.addColorStop(1, '#ffafcc');
        ctx.fillStyle = grad;
        ctx.fillRect(x, y, barWidth, barH);
    });

    // X轴标签
    for (let h = 0; h < 24; h += 3) {
        const x = padding.left + gap * h + gap / 2;
        ctx.fillStyle = '#636e72';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(h + ':00', x, H - padding.bottom + 14);
    }

    // 图例
    ctx.fillStyle = '#e85a8f';
    ctx.fillRect(padding.left, 10, 12, 12);
    ctx.fillStyle = '#636e72';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('平均喂奶次数', padding.left + 16, 20);
}

function drawSleepPattern(data) {
    const canvas = document.getElementById('patternCanvas');
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;

    ctx.clearRect(0, 0, W, H);

    const padding = { top: 30, right: 20, bottom: 40, left: 50 };
    const chartW = W - padding.left - padding.right;
    const chartH = H - padding.top - padding.bottom;

    const maxTotal = Math.max(...data.map(d => d.total / 60), 1);

    // Y轴
    ctx.strokeStyle = '#eee';
    for (let i = 0; i <= 4; i++) {
        const y = padding.top + (chartH / 4) * i;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(W - padding.right, y);
        ctx.stroke();

        ctx.fillStyle = '#b2bec3';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText((maxTotal * (4 - i) / 4).toFixed(1) + 'h', padding.left - 5, y + 3);
    }

    // 柱状图（堆叠）
    const barWidth = chartW / 24 * 0.7;
    const gap = chartW / 24;

    data.forEach((d, i) => {
        const x = padding.left + gap * i + (gap - barWidth) / 2;
        const nightH = (d.night / 60 / maxTotal) * chartH;
        const napH = (d.nap / 60 / maxTotal) * chartH;
        const yBase = padding.top + chartH;

        // 夜间（下方）
        if (nightH > 0) {
            const nightGrad = ctx.createLinearGradient(0, yBase - nightH, 0, yBase);
            nightGrad.addColorStop(0, '#6c5ce7');
            nightGrad.addColorStop(1, '#a29bfe');
            ctx.fillStyle = nightGrad;
            ctx.fillRect(x, yBase - nightH, barWidth, nightH);
        }

        // 小憩（上方）
        if (napH > 0) {
            const napGrad = ctx.createLinearGradient(0, yBase - nightH - napH, 0, yBase - nightH);
            napGrad.addColorStop(0, '#fdcb6e');
            napGrad.addColorStop(1, '#ffeaa7');
            ctx.fillStyle = napGrad;
            ctx.fillRect(x, yBase - nightH - napH, barWidth, napH);
        }
    });

    // X轴标签
    for (let h = 0; h < 24; h += 3) {
        const x = padding.left + gap * h + gap / 2;
        ctx.fillStyle = '#636e72';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(h + ':00', x, H - padding.bottom + 14);
    }

    // 图例
    ctx.fillStyle = '#6c5ce7';
    ctx.fillRect(padding.left, 10, 12, 12);
    ctx.fillStyle = '#636e72';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('夜间', padding.left + 16, 20);
    ctx.fillStyle = '#fdcb6e';
    ctx.fillRect(padding.left + 55, 10, 12, 12);
    ctx.fillStyle = '#636e72';
    ctx.fillText('小憩', padding.left + 71, 20);
}

function openBatchModal() {
    if (!App.currentBaby) {
        showToast.warning('请先添加宝宝');
        return;
    }
    batchRecordType = 'feeding';
    batchRecords = [createEmptyRecord('feeding')];
    document.querySelectorAll('.batch-tab').forEach(t => t.classList.remove('active'));
    document.querySelector('.batch-tab[data-type="feeding"]').classList.add('active');
    showModal('batchRecordModal');
    renderBatchRows();
}

function createEmptyRecord(type) {
    const now = new Date();
    const dateStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    const timeStr = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;

    if (type === 'feeding') {
        return { type: 'feeding', start_time: `${dateStr} ${timeStr}`, feeding_type: 'breast', amount: '', side: 'left', note: '' };
    } else if (type === 'sleep') {
        return { type: 'sleep', start_time: `${dateStr} ${timeStr}`, end_time: '', duration_minutes: '', is_nap: null, note: '' };
    } else if (type === 'diaper') {
        return { type: 'diaper', change_time: `${dateStr} ${timeStr}`, diaper_type: 'wet', color: '', note: '' };
    } else if (type === 'growth') {
        return { type: 'growth', record_date: dateStr, height: '', weight: '', head_circumference: '', bmi: '', note: '' };
    }
    return { type };
}

function renderBatchRows() {
    const list = document.getElementById('batchRecordList');
    list.innerHTML = batchRecords.map((record, index) => {
        if (batchRecordType === 'feeding') {
            return `
                <div class="batch-record-row" data-index="${index}">
                    <input type="datetime-local" value="${toLocalDatetime(record.start_time)}" data-field="start_time" title="开始时间">
                    <select data-field="feeding_type" title="类型">
                        <option value="breast" ${record.feeding_type === 'breast' ? 'selected' : ''}>母乳</option>
                        <option value="bottle" ${record.feeding_type === 'bottle' ? 'selected' : ''}>奶瓶</option>
                        <option value="solid" ${record.feeding_type === 'solid' ? 'selected' : ''}>辅食</option>
                    </select>
                    <input type="number" placeholder="ml" value="${record.amount}" data-field="amount" title="奶量">
                    <select data-field="side" title="侧">
                        <option value="left" ${record.side === 'left' ? 'selected' : ''}>左</option>
                        <option value="right" ${record.side === 'right' ? 'selected' : ''}>右</option>
                        <option value="both" ${record.side === 'both' ? 'selected' : ''}>双侧</option>
                    </select>
                    <button class="row-remove" onclick="removeBatchRow(${index})">X</button>
                </div>
            `;
        } else if (batchRecordType === 'sleep') {
            return `
                <div class="batch-record-row" data-index="${index}">
                    <input type="datetime-local" value="${toLocalDatetime(record.start_time)}" data-field="start_time" title="开始">
                    <input type="datetime-local" value="${toLocalDatetime(record.end_time)}" data-field="end_time" title="结束">
                    <input type="number" placeholder="分钟" value="${record.duration_minutes}" data-field="duration_minutes" title="时长">
                    <select data-field="is_nap" title="类型">
                        <option value="">自动</option>
                        <option value="1" ${record.is_nap === 1 ? 'selected' : ''}>小憩</option>
                        <option value="0" ${record.is_nap === 0 ? 'selected' : ''}>夜间</option>
                    </select>
                    <button class="row-remove" onclick="removeBatchRow(${index})">X</button>
                </div>
            `;
        } else if (batchRecordType === 'diaper') {
            return `
                <div class="batch-record-row" data-index="${index}">
                    <input type="datetime-local" value="${toLocalDatetime(record.change_time)}" data-field="change_time" title="时间">
                    <select data-field="diaper_type" title="类型">
                        <option value="wet" ${record.diaper_type === 'wet' ? 'selected' : ''}>尿</option>
                        <option value="dirty" ${record.diaper_type === 'dirty' ? 'selected' : ''}>便</option>
                        <option value="both" ${record.diaper_type === 'both' ? 'selected' : ''}>都有</option>
                    </select>
                    <select data-field="color" title="颜色">
                        <option value="">颜色</option>
                        <option value="yellow" ${record.color === 'yellow' ? 'selected' : ''}>黄</option>
                        <option value="brown" ${record.color === 'brown' ? 'selected' : ''}>棕</option>
                        <option value="green" ${record.color === 'green' ? 'selected' : ''}>绿</option>
                        <option value="black" ${record.color === 'black' ? 'selected' : ''}>黑</option>
                    </select>
                    <button class="row-remove" onclick="removeBatchRow(${index})">X</button>
                </div>
            `;
        } else if (batchRecordType === 'growth') {
            return `
                <div class="batch-record-row" data-index="${index}">
                    <input type="date" value="${record.record_date}" data-field="record_date" title="日期">
                    <input type="number" step="0.1" placeholder="身高cm" value="${record.height}" data-field="height">
                    <input type="number" step="0.01" placeholder="体重kg" value="${record.weight}" data-field="weight">
                    <input type="number" step="0.1" placeholder="头围cm" value="${record.head_circumference}" data-field="head_circumference">
                    <button class="row-remove" onclick="removeBatchRow(${index})">X</button>
                </div>
            `;
        }
        return '';
    }).join('');

    // 绑定输入事件
    list.querySelectorAll('.batch-record-row').forEach(row => {
        const index = parseInt(row.dataset.index);
        row.querySelectorAll('input, select').forEach(input => {
            input.addEventListener('change', () => {
                const field = input.dataset.field;
                let value = input.value;
                if (field === 'start_time' || field === 'end_time' || field === 'change_time') {
                    value = value ? value.replace('T', ' ') + ':00' : '';
                }
                batchRecords[index][field] = value;
            });
        });
    });
}

function toLocalDatetime(dt) {
    if (!dt) return '';
    // 将 "2024-01-01 12:00:00" 转为 "2024-01-01T12:00"
    return dt.replace(' ', 'T').substring(0, 16);
}

function removeBatchRow(index) {
    batchRecords.splice(index, 1);
    if (batchRecords.length === 0) {
        batchRecords.push(createEmptyRecord(batchRecordType));
    }
    renderBatchRows();
}

function submitBatchRecords() {
    if (!App.currentBaby || batchRecords.length === 0) return;

    // 过滤空记录
    const validRecords = batchRecords.filter(r => {
        if (r.type === 'feeding') return r.start_time;
        if (r.type === 'sleep') return r.start_time;
        if (r.type === 'diaper') return r.change_time;
        if (r.type === 'growth') return r.record_date;
        return false;
    });

    if (validRecords.length === 0) {
        showToast('请至少填写一条记录');
        return;
    }

    api(`/api/babies/${App.currentBaby}/batch-records`, {
        method: 'POST',
        body: JSON.stringify({ records: validRecords })
    }).then(res => {
        if (res.success) {
            showToast(res.message);
            hideModal('batchRecordModal');
            // 刷新仪表盘
            loadDashboard();
        } else {
            showToast.error(res.message || '保存失败');
        }
    });
}

// ==================== 出牙数据（提前定义避免 TDZ） ====================
const TEETH_DATA = {
    upperRight: [
        {code: '55', name: '第二乳磨牙', type: 'molar'},
        {code: '54', name: '第一乳磨牙', type: 'molar'},
        {code: '53', name: '犬牙', type: 'canine'},
        {code: '52', name: '侧切牙', type: 'incisor'},
        {code: '51', name: '中切牙', type: 'incisor'},
    ],
    upperLeft: [
        {code: '61', name: '中切牙', type: 'incisor'},
        {code: '62', name: '侧切牙', type: 'incisor'},
        {code: '63', name: '犬牙', type: 'canine'},
        {code: '64', name: '第一乳磨牙', type: 'molar'},
        {code: '65', name: '第二乳磨牙', type: 'molar'},
    ],
    lowerRight: [
        {code: '85', name: '第二乳磨牙', type: 'molar'},
        {code: '84', name: '第一乳磨牙', type: 'molar'},
        {code: '83', name: '犬牙', type: 'canine'},
        {code: '82', name: '侧切牙', type: 'incisor'},
        {code: '81', name: '中切牙', type: 'incisor'},
    ],
    lowerLeft: [
        {code: '71', name: '中切牙', type: 'incisor'},
        {code: '72', name: '侧切牙', type: 'incisor'},
        {code: '73', name: '犬牙', type: 'canine'},
        {code: '74', name: '第一乳磨牙', type: 'molar'},
        {code: '75', name: '第二乳磨牙', type: 'molar'},
    ],
};

// ==================== 初始化 ====================

// 主初始化函数（封装以便根据 readyState 决定立即执行或等待 DOMContentLoaded）
// 安全执行初始化函数，单个失败不影响其他
function safeCall(fn, name) {
    try {
        fn();
        console.log('[mainInit] [OK] ' + name);
    } catch (err) {
        console.error('[mainInit] ✗ ' + name + ' 失败:', err);
    }
}

function mainInit() {
    safeCall(initBabySelector, 'initBabySelector');
    safeCall(buildHubPages, 'buildHubPages');
    safeCall(initNavigation, 'initNavigation');
    safeCall(initBottomTabs, 'initBottomTabs');
    safeCall(initQuickActions, 'initQuickActions');
    safeCall(initRecordPages, 'initRecordPages');
    safeCall(initGrowthModal, 'initGrowthModal');
    safeCall(initDiaryModal, 'initDiaryModal');
    safeCall(initKnowledgeCategories, 'initKnowledgeCategories');
    safeCall(initKnowledgeSearch, 'initKnowledgeSearch');
    safeCall(initSettings, 'initSettings');
    safeCall(initTimeline, 'initTimeline');
    safeCall(initGrowthTabs, 'initGrowthTabs');
    safeCall(initTimerBar, 'initTimerBar');
    safeCall(initPhotoModal, 'initPhotoModal');
    safeCall(initVaccineModal, 'initVaccineModal');
    safeCall(initVaccinesPage, 'initVaccinesPage');
    safeCall(initShareCard, 'initShareCard');
    safeCall(initMilestoneModal, 'initMilestoneModal');
    safeCall(initSleepAnalysis, 'initSleepAnalysis');
    safeCall(() => { initDataManagement(); }, 'initDataManagement-1');
    safeCall(initBatchRecord, 'initBatchRecord');
    safeCall(initTemperature, 'initTemperature');
    safeCall(initMedication, 'initMedication');
    // initTummyTime 已并入 initTummytimePage（旧驼峰版是空哨兵函数，已删）
    safeCall(initAllergyTest, 'initAllergyTest');
    safeCall(initAsqPage, 'initAsqPage');
    safeCall(initTeethingPage, 'initTeethingPage');
    safeCall(initCheckupPage, 'initCheckupPage');
    safeCall(initSolidFoodPage, 'initSolidFoodPage');
    safeCall(initLeapPage, 'initLeapPage');
    safeCall(initFontanellePage, 'initFontanellePage');
    safeCall(initSoundsPage, 'initSoundsPage');
    safeCall(initBmiPage, 'initBmiPage');
    safeCall(initRecipesPage, 'initRecipesPage');
    safeCall(initActivitiesPage, 'initActivitiesPage');
    safeCall(initTeethPage, 'initTeethPage');
    safeCall(initHealthPage, 'initHealthPage');
    safeCall(initHealthArchivePage, 'initHealthArchivePage');
    safeCall(initFirstsPage, 'initFirstsPage');
    safeCall(initTummytimePage, 'initTummytimePage');
    safeCall(initAllergyPage, 'initAllergyPage');
    safeCall(initShoppingPage, 'initShoppingPage');
    safeCall(initMedReminderPage, 'initMedReminderPage');
    safeCall(initReportsPage, 'initReportsPage');
    safeCall(() => { initDataManagement(); }, 'initDataManagement-2');
    safeCall(initDarkMode, 'initDarkMode');

    // 返回按钮点击事件
    const backBtn = document.getElementById('backBtn');
    if (backBtn) {
        backBtn.addEventListener('click', goBack);
    }

    // ==================== 推送通知设置 ====================
    initNotificationSettings();

    // 关闭弹窗（点击遮罩）
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
        overlay.addEventListener('click', function(e) {
            // 登录遮罩禁止点背景关闭：必须先登录才能进入，关闭会导致无法操作
            if (this.id === 'authOverlay') return;
            if (e.target === this) {
                this.classList.remove('show');
            }
        });
    });

    // 加载初始数据
    loadBabies();

    // 调试：检查是否有输入框自动聚焦（可能导致"点击显示输入符号"问题）
    setTimeout(() => {
        const focused = document.activeElement;
        if (focused && focused.tagName === 'INPUT') {
            console.warn('[mainInit] 发现自动聚焦的输入框:', focused.id || focused.placeholder);
            // 移除自动聚焦，避免用户误以为页面无法操作
            focused.blur();
        }
    }, 100);
}

// 根据 readyState 决定立即执行或等待 DOMContentLoaded（防止脚本加载时 DOM 已就绪导致事件监听不到）
function safeInit() {
    console.log('[safeInit] 开始初始化...');
    try {
        mainInit();
        console.log('[safeInit] 初始化完成');
    } catch (err) {
        console.error('[mainInit] 初始化失败:', err);
        // 显示错误提示到页面
        const errDiv = document.createElement('div');
        errDiv.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#e74c3c;color:#fff;padding:12px;z-index:9999;font-size:14px;text-align:center;';
        errDiv.textContent = '页面初始化失败: ' + err.message + ' (请按F12查看控制台详情)';
        document.body.appendChild(errDiv);
    }
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', safeInit);
} else {
    safeInit();
}

// ==================== BMI 追踪 ====================
function initBmiPage() {
    document.getElementById('addBmiBtn')?.addEventListener('click', showBmiForm);
}

function loadBmiData() {
    if (!App.currentBaby) return;
    fetch(`${App.apiBase}/api/babies/${App.currentBaby}/growth`)
        .then(r => r.json())
        .then(res => {
            if (!res.success || !res.data.length) {
                document.getElementById('bmiCurrentValue').textContent = '--';
                document.getElementById('bmiPercentile').textContent = '--';
                document.getElementById('bmiStatusBadge').textContent = '--';
                document.getElementById('bmiStatusBadge').className = 'bmi-status-badge';
                document.getElementById('bmiHistoryList').innerHTML = '<p class="empty-tip">暂无BMI记录</p>';
                return;
            }

            // 筛选有BMI或身高体重的记录
            const bmiRecords = res.data.filter(r => r.bmi || (r.height && r.weight));
            if (!bmiRecords.length) {
                document.getElementById('bmiHistoryList').innerHTML = '<p class="empty-tip">请先在成长记录中添加身高体重数据</p>';
                return;
            }

            // 计算每条记录的BMI
            const records = bmiRecords.map(r => {
                let bmi = r.bmi;
                if (!bmi && r.height && r.weight) {
                    const h = r.height / 100;
                    bmi = (r.weight / (h * h)).toFixed(1);
                }
                return { ...r, bmi: parseFloat(bmi) };
            });

            // 更新摘要
            const latest = records[0];
            document.getElementById('bmiCurrentValue').textContent = latest.bmi.toFixed(1);

            // 计算百分位
            const baby = App.babies?.find(b => b.id === App.currentBaby);
            let percentile = null;
            if (baby) {
                const birthDate = new Date(baby.birthday);
                const recordDate = new Date(latest.record_date);
                const ageInDays = Math.floor((recordDate - birthDate) / (1000 * 60 * 60 * 24));
                percentile = getBmiPercentile(baby.gender, ageInDays, latest.bmi);
            }

            if (percentile !== null) {
                document.getElementById('bmiPercentile').textContent = `P${percentile}`;
                const badge = document.getElementById('bmiStatusBadge');
                if (percentile < 15) {
                    badge.textContent = '偏瘦';
                    badge.className = 'bmi-status-badge underweight';
                } else if (percentile < 85) {
                    badge.textContent = '正常';
                    badge.className = 'bmi-status-badge normal';
                } else if (percentile < 97) {
                    badge.textContent = '超重';
                    badge.className = 'bmi-status-badge overweight';
                } else {
                    badge.textContent = '肥胖';
                    badge.className = 'bmi-status-badge obese';
                }
            } else {
                document.getElementById('bmiPercentile').textContent = '--';
                document.getElementById('bmiStatusBadge').textContent = '--';
            }

            // 绘制图表
            drawBmiChart(records);

            // 更新历史列表
            const historyHtml = records.slice(0, 10).map(r => `
                <div class="bmi-history-item">
                    <span class="bmi-history-date">${r.record_date}</span>
                    <span class="bmi-history-value">BMI ${r.bmi}</span>
                    <span class="bmi-history-percent">${r.height ? r.height + 'cm / ' + r.weight + 'kg' : ''}</span>
                </div>
            `).join('');
            document.getElementById('bmiHistoryList').innerHTML = historyHtml;
        })
        .catch(err => console.error('加载BMI数据失败:', err));
}

function getBmiPercentile(gender, ageInDays, bmi) {
    // 简化的BMI百分位估算
    // 实际应该查询WHO数据表
    if (ageInDays < 0 || ageInDays > 1825) return null;

    // 使用WHO近似值（2-5岁）
    const sex = gender === 'girl' ? 'girl' : 'boy';
    const whoData = sex === 'boy' ?
        [[365, 15.5, 16.5, 17.5, 18.5, 19.5], [730, 15.0, 16.0, 17.0, 18.0, 19.0], [1095, 14.5, 15.5, 16.5, 17.5, 18.5], [1460, 14.0, 15.0, 16.0, 17.0, 18.0], [1825, 13.8, 14.8, 15.8, 16.8, 17.8]] :
        [[365, 15.3, 16.3, 17.3, 18.3, 19.3], [730, 14.8, 15.8, 16.8, 17.8, 18.8], [1095, 14.3, 15.3, 16.3, 17.3, 18.3], [1460, 13.8, 14.8, 15.8, 16.8, 17.8], [1825, 13.5, 14.5, 15.5, 16.5, 17.5]];

    // 找到最接近的年龄
    let closest = whoData[0];
    for (const row of whoData) {
        if (Math.abs(ageInDays - row[0]) < Math.abs(ageInDays - closest[0])) {
            closest = row;
        }
    }

    const [, p3, p15, p50, p85, p97] = closest;
    if (bmi < p3) return 3;
    if (bmi < p15) return Math.round(3 + (bmi - p3) / (p15 - p3) * 12);
    if (bmi < p50) return Math.round(15 + (bmi - p15) / (p50 - p15) * 35);
    if (bmi < p85) return Math.round(50 + (bmi - p50) / (p85 - p50) * 35);
    if (bmi < p97) return Math.round(85 + (bmi - p85) / (p97 - p85) * 12);
    return 97;
}

function drawBmiChart(records) {
    const canvas = document.getElementById('bmiChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;

    ctx.clearRect(0, 0, width, height);

    if (records.length < 1) return;

    // 准备数据
    const sorted = [...records].sort((a, b) => new Date(a.record_date) - new Date(b.record_date));
    const minBmi = Math.min(...sorted.map(r => r.bmi)) - 1;
    const maxBmi = Math.max(...sorted.map(r => r.bmi)) + 1;
    const bmiRange = maxBmi - minBmi || 1;

    const padding = { top: 20, right: 20, bottom: 30, left: 40 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    // 绘制网格
    ctx.strokeStyle = '#e0e0e0';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
        const y = padding.top + (chartH / 4) * i;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(width - padding.right, y);
        ctx.stroke();

        // Y轴标签
        const val = maxBmi - (bmiRange / 4) * i;
        ctx.fillStyle = '#999';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(val.toFixed(1), padding.left - 5, y + 3);
    }

    // 绘制曲线
    ctx.strokeStyle = '#42A5F5';
    ctx.lineWidth = 2;
    ctx.beginPath();

    sorted.forEach((r, i) => {
        const x = padding.left + (i / Math.max(sorted.length - 1, 1)) * chartW;
        const y = padding.top + ((maxBmi - r.bmi) / bmiRange) * chartH;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // 绘制数据点
    sorted.forEach((r, i) => {
        const x = padding.left + (i / Math.max(sorted.length - 1, 1)) * chartW;
        const y = padding.top + ((maxBmi - r.bmi) / bmiRange) * chartH;
        ctx.fillStyle = '#42A5F5';
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.arc(x, y, 2, 0, Math.PI * 2);
        ctx.fill();
    });

    // X轴标签
    ctx.fillStyle = '#999';
    ctx.font = '9px sans-serif';
    ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(sorted.length / 5));
    sorted.forEach((r, i) => {
        if (i % step === 0 || i === sorted.length - 1) {
            const x = padding.left + (i / Math.max(sorted.length - 1, 1)) * chartW;
            ctx.fillText(r.record_date.slice(5), x, height - 5);
        }
    });
}

function showBmiForm() {
    // 跳转到成长记录页面或显示快速录入弹窗
    if (typeof switchPage === 'function') {
        switchPage('growth');
    }
}

// ==================== 数据导出/导入 ====================
function initDataManagement() {
    // 看诊摘要页（clinic）里的那套按钮
    document.getElementById('exportDataBtn')?.addEventListener('click', exportData);
    document.getElementById('importDataBtn')?.addEventListener('click', () => {
        document.getElementById('importFileInput')?.click();
    });
    document.getElementById('importFileInput')?.addEventListener('change', handleImportFile);
    document.getElementById('exportAllBtn')?.addEventListener('click', exportAllData);

    // 备份管理
    document.getElementById('createBackupBtn')?.addEventListener('click', createBackup);
    document.getElementById('importBackupInput')?.addEventListener('change', handleBackupUpload);

    // ---- 数据存储管理：存储总览 / 健康 / 自动备份 / 清理 ----
    document.getElementById('refreshStorageBtn')?.addEventListener('click', loadStorageOverview);
    document.getElementById('checkDbHealthBtn')?.addEventListener('click', checkDbHealth);
    document.getElementById('optimizeDbBtn')?.addEventListener('click', optimizeDb);
    document.getElementById('cleanOrphanBtn')?.addEventListener('click', cleanOrphanPhotos);
    document.getElementById('cleanLogsBtn')?.addEventListener('click', cleanupLogs);
    document.getElementById('triggerAutoBackupBtn')?.addEventListener('click', triggerAutoBackup);
    document.getElementById('saveAutoBackupBtn')?.addEventListener('click', saveAutoBackupConfig);

    // 设置页里的同款按钮
    document.getElementById('exportDataBtn2')?.addEventListener('click', exportData);
    document.getElementById('importDataBtn2')?.addEventListener('click', () => {
        document.getElementById('importFileInput2')?.click();
    });
    document.getElementById('importFileInput2')?.addEventListener('change', handleImportFile);

    // 加载备份列表 + 存储总览 + 自动备份配置
    loadBackupList();
    if (typeof loadStorageOverview === 'function') loadStorageOverview(false);
    if (typeof loadAutoBackupConfig === 'function') loadAutoBackupConfig();
}

function exportData() {
    if (!App.currentBaby) {
        showDataTip('请先选择宝宝', 'error');
        return;
    }

    fetch(`${App.apiBase}/api/babies/${App.currentBaby}/export`)
        .then(r => r.json())
        .then(res => {
            if (res.success) {
                // 创建下载
                const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `babycare_export_${res.data.baby.name}_${getToday()}.json`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                showDataTip('数据导出成功！', 'success');
            } else {
                showDataTip('导出失败: ' + (res.message || '未知错误'), 'error');
            }
        })
        .catch(err => {
            console.error('导出失败:', err);
            showDataTip('导出失败，请重试', 'error');
        });
}

function handleImportFile(e) {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = function(event) {
        try {
            const data = JSON.parse(event.target.result);
            if (!data.version || !data.baby) {
                showDataTip('无效的数据文件格式', 'error');
                return;
            }
            importDataToServer(data);
        } catch (err) {
            showDataTip('文件解析失败: ' + err.message, 'error');
        }
    };
    reader.readAsText(file);
    // 清空input以便重复选择同一文件
    e.target.value = '';
}

function importDataToServer(data) {
    if (!App.currentBaby) {
        showDataTip('请先选择宝宝', 'error');
        return;
    }

    fetch(`${App.apiBase}/api/babies/${App.currentBaby}/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            const total = Object.values(res.imported).reduce((a, b) => a + b, 0);
            showDataTip(`导入成功！共导入 ${total} 条记录`, 'success');
            // 刷新当前页面数据
            if (typeof loadDashboard === 'function') loadDashboard();
        } else {
            showDataTip('导入失败: ' + (res.message || '未知错误'), 'error');
        }
    })
    .catch(err => {
        console.error('导入失败:', err);
        showDataTip('导入失败，请重试', 'error');
    });
}

function showDataTip(msg, type) {
    const tip = document.getElementById('dataTip');
    if (!tip) return;
    tip.textContent = msg;
    tip.className = `data-tip ${type}`;
    tip.style.display = 'block';
    setTimeout(() => { tip.style.display = 'none'; }, 4000);
}

// ==================== 备份管理 ====================
function createBackup() {
    const btn = document.getElementById('createBackupBtn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '备份中...';
    }

    fetch(`${App.apiBase}/api/backup/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ baby_id: App.currentBaby || null, note: '手动备份' })
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            showDataTip(`备份创建成功！大小: ${res.size_human}`, 'success');
            loadBackupList();
        } else {
            showDataTip('备份失败: ' + (res.message || '未知错误'), 'error');
        }
    })
    .catch(err => {
        console.error('创建备份失败:', err);
        showDataTip('备份失败，请重试', 'error');
    })
    .finally(() => {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '创建备份';
        }
    });
}

function loadBackupList() {
    const listEl = document.getElementById('backupList');
    if (!listEl) return;

    fetch(`${App.apiBase}/api/backup/list`)
    .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    })
    .then(res => {
        if (!res.success || !res.data || res.data.length === 0) {
            listEl.innerHTML = '<p class="backup-empty">暂无备份</p>';
            return;
        }
        listEl.innerHTML = res.data.map(b => {
            const isAuto = b.filename && b.filename.startsWith('auto_backup_');
            return `<div class="backup-item${isAuto ? ' backup-item-auto' : ''}">
                <div class="backup-item-info">
                    <div class="backup-item-name">${escapeHtml(b.filename)}${isAuto ? ' <span class="backup-auto-badge">自动</span>' : ''}</div>
                    <div class="backup-item-meta">${escapeHtml(b.created_at)} · ${escapeHtml(b.size_human)}</div>
                </div>
                <div class="backup-item-actions">
                    <button class="btn btn-outline btn-sm" data-action="download" data-file="${escapeHtml(b.filename)}">下载</button>
                    <button class="btn btn-outline btn-sm" data-action="restore" data-file="${escapeHtml(b.filename)}">恢复</button>
                    <button class="btn btn-danger btn-sm" data-action="delete" data-file="${escapeHtml(b.filename)}">删</button>
                </div>
            </div>`;
        }).join('');
    })
    .catch(err => {
        console.error('加载备份列表失败:', err);
        listEl.innerHTML = '<p class="backup-empty">加载失败</p>';
    });
}

// 备份列表按钮事件委托（全局注册一次，data-action + data-file 取代内联 onclick，
// 同时修复此前"删除"按钮无任何绑定导致无法删除备份的 bug）
document.addEventListener('click', (e) => {
    const btn = e.target.closest('.backup-item-actions [data-action]');
    if (!btn) return;
    const file = btn.dataset.file;
    if (!file) return;
    if (btn.dataset.action === 'download') downloadBackup(file);
    else if (btn.dataset.action === 'restore') restoreBackup(file);
    else if (btn.dataset.action === 'delete') deleteBackup(file);
});

// ==================== 数据存储管理：总览 / 健康 / 自动备份 / 清理 ====================
function loadStorageOverview(showSuccess) {
    const fill = document.getElementById('diskUsageFill');
    const pctEl = document.getElementById('diskUsagePct');
    const dbEl = document.getElementById('storageDb');
    const photosEl = document.getElementById('storagePhotos');
    const backupsEl = document.getElementById('storageBackups');
    const logsEl = document.getElementById('storageLogs');
    const freeEl = document.getElementById('diskFreeText');
    const refreshDataMgmt = typeof loadBackupList === 'function' ? loadBackupList : null;

    fetch(`${App.apiBase}/api/storage/overview`, { headers: { 'Accept': 'application/json' } })
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then(data => {
        if (!data.success && !data.database) throw new Error(data.message || '加载失败');
        if (dbEl) dbEl.textContent = data.database?.size_human || '--';
        if (photosEl) photosEl.textContent = (data.photos?.count ?? '--') + ' 张 / ' + (data.photos?.total_size_human || '--');
        if (backupsEl) backupsEl.textContent = (data.backups?.count ?? '--') + ' 个 / ' + (data.backups?.size_human || '--');
        if (logsEl) logsEl.textContent = (data.logs?.count ?? '--') + ' 个 / ' + (data.logs?.size_human || '--');
        const disk = data.disk || {};
        if (disk.percent_used != null) {
            if (fill) fill.style.width = Math.min(100, disk.percent_used) + '%';
            if (pctEl) pctEl.textContent = disk.percent_used + '%';
            if (fill) fill.style.background = disk.percent_used > 90 ? 'var(--danger-color)' :
                disk.percent_used > 70 ? 'var(--warning-color)' : 'var(--primary-color)';
        }
        if (freeEl) freeEl.textContent = '可用空间：' + (disk.free_human || '--') + ' / 共 ' + (disk.total_human || '--');
        if (showSuccess) showDataTip('存储信息已刷新', 'success');
        if (refreshDataMgmt) refreshDataMgmt();
    })
    .catch(err => {
        console.error('加载存储总览失败:', err);
        showDataTip('加载存储总览失败', 'error');
    });
}

let dbHealthCache = null;
function checkDbHealth() {
    const statusEl = document.getElementById('dbHealthStatus');
    if (statusEl) statusEl.textContent = '正在检查完整性...';
    fetch(`${App.apiBase}/api/storage/db/health`)
    .then(r => r.json())
    .then(data => {
        dbHealthCache = data;
        const ok = data.integrity_ok;
        if (statusEl) {
            statusEl.className = 'storage-health-status ' + (ok ? 'status-ok' : 'status-error');
            statusEl.textContent = (ok ? '完整性正常' : '完整性异常') +
                (data.page_count != null ? ` · ${data.page_count} 页 · ${data.journal_mode}` : '');
        }
        showDataTip(ok ? '数据库完整性正常' : '数据库完整性异常: ' + (data.integrity || '未知'), ok ? 'success' : 'error');
    })
    .catch(err => { if (statusEl) statusEl.textContent = '检查失败: ' + err.message; showDataTip('检查失败', 'error'); });
}

function optimizeDb() {
    const btn = document.getElementById('optimizeDbBtn');
    if (btn) { btn.disabled = true; btn.textContent = '优化中...'; }
    showDataTip('正在优化数据库，稍候...', 'success');
    fetch(`${App.apiBase}/api/storage/db/optimize`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            showDataTip(`优化完成！释放 ${res.saved_human || '--'}`, 'success');
            loadStorageOverview(false);
        } else {
            showDataTip('优化失败: ' + (res.message || '未知错误'), 'error');
        }
    })
    .catch(err => showDataTip('优化失败: ' + err.message, 'error'))
    .finally(() => { if (btn) { btn.disabled = false; btn.textContent = '优化数据库'; } });
}

function cleanOrphanPhotos() {
    showDataTip('正在扫描孤立照片...', 'success');
    fetch(`${App.apiBase}/api/storage/photos/orphans`)
    .then(r => r.json())
    .then(data => {
        const cnt = data.orphan_count || 0;
        if (cnt === 0) { showDataTip('没有孤立照片，文件夹干净', 'success'); return; }
        if (!confirm(`发现 ${cnt} 个孤立照片文件（共 ${data.orphan_size_human || '--'}），是否清理？`)) return;
        return fetch(`${App.apiBase}/api/storage/photos/cleanup`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) })
            .then(r => r.json())
            .then(res => {
                if (res.success) showDataTip(`已清理 ${res.removed_count} 个孤立文件，释放 ${res.freed_size_human || '--'}`, 'success');
                else showDataTip('清理失败: ' + (res.message || ''), 'error');
                loadStorageOverview(false);
            });
    })
    .catch(err => showDataTip('扫描失败: ' + err.message, 'error'));
}

function cleanupLogs() {
    if (!confirm('确认清空所有日志文件？此操作不可撤销。')) return;
    fetch(`${App.apiBase}/api/storage/logs/cleanup`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'truncate' }) })
    .then(r => r.json())
    .then(res => {
        if (res.success) { showDataTip(res.message || '日志已清空', 'success'); loadStorageOverview(false); }
        else showDataTip('清空失败: ' + (res.message || ''), 'error');
    })
    .catch(err => showDataTip('清空失败: ' + err.message, 'error'));
}

function triggerAutoBackup() {
    fetch(`${App.apiBase}/api/storage/auto-backup/trigger`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) })
    .then(r => r.json())
    .then(res => {
        if (res.success) { showDataTip('自动备份任务已启动，稍候会在列表中显示', 'success'); }
        else showDataTip('启动失败: ' + (res.message || '未知错误'), 'error');
    })
    .catch(err => showDataTip('启动失败: ' + err.message, 'error'));
}

function saveAutoBackupConfig() {
    const enabled = document.getElementById('autoBackupEnabled')?.checked || false;
    const interval = parseInt(document.getElementById('autoBackupInterval')?.value || '24', 10);
    const keep = parseInt(document.getElementById('autoBackupKeep')?.value || '10', 10);
    fetch(`${App.apiBase}/api/storage/auto-backup/config`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled, interval_hours: interval, keep_count: keep })
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) { showDataTip('自动备份配置已保存', 'success'); loadAutoBackupConfig(); }
        else showDataTip('保存失败: ' + (res.message || ''), 'error');
    })
    .catch(err => showDataTip('保存失败: ' + err.message, 'error'));
}

function loadAutoBackupConfig() {
    fetch(`${App.apiBase}/api/storage/auto-backup/config`)
    .then(r => r.json())
    .then(data => {
        if (!data || data.enabled == null) return;
        const cb = document.getElementById('autoBackupEnabled');
        const interval = document.getElementById('autoBackupInterval');
        const keep = document.getElementById('autoBackupKeep');
        const statusEl = document.getElementById('autoBackupStatus');
        const autoTag = document.getElementById('backupAutoTag');
        if (cb) cb.checked = !!data.enabled;
        if (interval) {
            const opt = Array.from(interval.options).find(o => parseInt(o.value, 10) === data.interval_hours);
            if (opt) interval.value = String(data.interval_hours);
        }
        if (keep) {
            const opt = Array.from(keep.options).find(o => parseInt(o.value, 10) === data.keep_count);
            if (opt) keep.value = String(data.keep_count);
        }
        if (statusEl) {
            if (data.enabled) {
                statusEl.textContent = `已启用 · 间隔 ${data.interval_hours}h · 保留 ${data.keep_count} 个` +
                    (data.last_run ? ' · 上次 ' + data.last_run : '') + (data.last_status === 'error' ? ' · 最近失败' : '') +
                    (data.next_run ? ' · 下次 ' + data.next_run : '');
                statusEl.className = 'auto-backup-status status-on';
            } else {
                statusEl.textContent = '未启用';
                statusEl.className = 'auto-backup-status';
            }
        }
        if (autoTag) autoTag.style.display = data.last_run ? 'inline' : 'none';
    })
    .catch(err => console.error('加载自动备份配置失败:', err));
}

function downloadBackup(filename) {
    window.open(`${App.apiBase}/api/backup/download/${encodeURIComponent(filename)}`, '_blank');
}

function restoreBackup(filename) {
    if (!confirm(`确定要从 "${filename}" 恢复数据吗？\n\n恢复会替换同名宝宝的现有记录（以备份为准），请谨慎操作。`)) {
        return;
    }

    showDataTip('正在恢复，请稍候...', 'success');

    fetch(`${App.apiBase}/api/backup/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename })
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            showDataTip(`恢复成功！${res.message}`, 'success');
            loadBackupList();
            if (typeof loadDashboard === 'function') loadDashboard();
        } else {
            showDataTip('恢复失败: ' + (res.message || '未知错误'), 'error');
        }
    })
    .catch(err => {
        console.error('恢复失败:', err);
        showDataTip('恢复失败，请重试', 'error');
    });
}

function deleteBackup(filename) {
    if (!confirm(`确定要删除备份 "${filename}" 吗？`)) {
        return;
    }

    fetch(`${App.apiBase}/api/backup/delete/${encodeURIComponent(filename)}`, {
        method: 'DELETE'
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            showDataTip('备份已删除', 'success');
            loadBackupList();
        } else {
            showDataTip('删除失败: ' + (res.message || '未知错误'), 'error');
        }
    })
    .catch(err => {
        console.error('删除备份失败:', err);
        showDataTip('删除失败，请重试', 'error');
    });
}

function handleBackupUpload(e) {
    const file = e.target.files[0];
    if (!file) return;

    if (!file.name.endsWith('.json')) {
        showDataTip('请选择 JSON 格式的备份文件', 'error');
        return;
    }

    if (!confirm(`确定要从 "${file.name}" 恢复数据吗？`)) {
        e.target.value = '';
        return;
    }

    showDataTip('正在上传恢复，请稍候...', 'success');

    const formData = new FormData();
    formData.append('file', file);

    fetch(`${App.apiBase}/api/backup/upload-restore`, {
        method: 'POST',
        body: formData
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            showDataTip(`上传恢复成功！${res.message}`, 'success');
            loadBackupList();
            if (typeof loadDashboard === 'function') loadDashboard();
        } else {
            showDataTip('恢复失败: ' + (res.message || '未知错误'), 'error');
        }
    })
    .catch(err => {
        console.error('上传恢复失败:', err);
        showDataTip('上传恢复失败，请重试', 'error');
    })
    .finally(() => {
        e.target.value = '';
    });
}

// ==================== 深色模式 ====================
function initDarkMode() {
    // 读取保存的主题设置
    const savedTheme = localStorage.getItem('babycare_theme') || 'light';
    if (savedTheme === 'dark') {
        document.documentElement.setAttribute('data-theme', 'dark');
        const toggle = document.getElementById('darkModeToggle');
        if (toggle) toggle.checked = true;
    }

    // 绑定切换事件
    document.getElementById('darkModeToggle')?.addEventListener('change', function() {
        if (this.checked) {
            document.documentElement.setAttribute('data-theme', 'dark');
            localStorage.setItem('babycare_theme', 'dark');
        } else {
            document.documentElement.removeAttribute('data-theme');
            localStorage.setItem('babycare_theme', 'light');
        }
    });
}

// ==================== 睡眠预测 ====================
function loadSleepPrediction() {
    if (!App.currentBaby) return;
    const content = document.getElementById('sleepPredictionContent');
    if (!content) return;

    content.innerHTML = '<p class="prediction-loading">分析中...</p>';

    fetch(`${App.apiBase}/api/babies/${App.currentBaby}/sleep-prediction`)
        .then(r => r.json())
        .then(res => {
            if (res.success && res.data) {
                const d = res.data;
                content.innerHTML = `
                    <div class="prediction-main">
                        <span class="prediction-time">${d.next_sleep_time}</span>
                        <span class="prediction-confidence">可信度 ${d.confidence}%</span>
                    </div>
                    <div class="prediction-detail">
                        <span>平均间隔: ${d.avg_interval_hours}h</span>
                        <span>平均时长: ${d.avg_duration_minutes}min</span>
                    </div>
                    <div class="prediction-note">${d.pattern_note}</div>
                `;
            } else {
                content.innerHTML = `<p class="prediction-note">${escapeHtml(res.message || '暂无足够数据进行预测')}</p>`;
            }
        })
        .catch(err => {
            console.error('加载睡眠预测失败:', err);
            content.innerHTML = '<p class="prediction-note">加载失败，请重试</p>';
        });
}

// ==================== 安抚音效播放器 ====================
// 使用单例 AudioContext + 主增益节点链，统一管理所有音效的播放/停止/暂停
let audioCtx = null;
let masterGainNode = null;       // 主增益节点（所有音效汇总到这里）
let compressorNode = null;       // 压缩器（防止削波爆音）
let currentSoundNode = null;     // 当前播放的音效节点（含自定义 stop 方法）
let currentGainNode = null;      // 当前音效的独立增益（用于淡入淡出）
let currentSoundType = null;     // 当前音效类型
let currentCard = null;          // 当前播放的卡片 DOM
let soundsTimerSeconds = 0;      // 定时器剩余秒数
let soundsTimerInterval = null;  // 定时器 interval
let isPaused = false;            // 是否暂停
let stopSoundTimeout = null;     // 淡出停止的待执行 timeout

/**
 * 初始化音频上下文（单例，整个应用生命周期只创建一次）
 * 创建主链路：音源 -> 独立增益 -> 主增益 -> 压缩器 -> 扬声器
 */
function ensureAudioContext() {
    if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        // 创建主增益节点（全局音量）
        masterGainNode = audioCtx.createGain();
        masterGainNode.gain.value = 1.0;
        // 创建软限幅压缩器（兜底防削波爆音）
        compressorNode = audioCtx.createDynamicsCompressor();
        compressorNode.threshold.value = -10;
        compressorNode.knee.value = 24;
        compressorNode.ratio.value = 8;
        compressorNode.attack.value = 0.004;
        compressorNode.release.value = 0.25;
        // 主链路：主增益 -> 压缩器 -> 扬声器
        masterGainNode.connect(compressorNode);
        compressorNode.connect(audioCtx.destination);
    }
    return audioCtx;
}

// 音效偏好（音量/定时时长）本地记忆：哄睡多半是半夜摸黑操作，
// 每次进页面都回到默认的 50% / 30 分钟很折腾
const SOUND_PREF_KEY = 'babycare_sound_prefs';

function loadSoundPrefs() {
    try {
        return JSON.parse(localStorage.getItem(SOUND_PREF_KEY) || '{}') || {};
    } catch (e) {
        return {};
    }
}

function saveSoundPrefs(patch) {
    try {
        localStorage.setItem(SOUND_PREF_KEY, JSON.stringify(Object.assign(loadSoundPrefs(), patch)));
    } catch (e) { /* 隐私模式下 localStorage 可能不可用，忽略 */ }
}

function initSoundsPage() {
    // 绑定音效卡片点击
    document.querySelectorAll('.sound-card').forEach(card => {
        card.addEventListener('click', function() {
            const soundType = this.dataset.sound;
            handleSoundPlay(soundType, this);
        });
    });

    // 恢复上次的音量与定时设置
    const prefs = loadSoundPrefs();
    const volEl = document.getElementById('playerVolume');
    if (volEl && prefs.volume != null) {
        volEl.value = prefs.volume;
        if (masterGainNode && audioCtx) masterGainNode.gain.value = prefs.volume / 100;
    }
    const timerSelect = document.getElementById('soundsTimer');
    if (timerSelect && prefs.timer != null) {
        timerSelect.value = String(prefs.timer);
    }

    // 绑定控制按钮
    document.getElementById('playerStop')?.addEventListener('click', stopSound);
    document.getElementById('playerPause')?.addEventListener('click', togglePauseSound);
    document.getElementById('playerVolume')?.addEventListener('input', function() {
        if (masterGainNode && audioCtx) {
            // 用 setTargetAtTime 平滑过渡，避免拖动滑杆时的音量跳变/爆音
            const now = audioCtx.currentTime;
            masterGainNode.gain.cancelScheduledValues(now);
            masterGainNode.gain.setTargetAtTime(this.value / 100, now, 0.05);
        }
        saveSoundPrefs({ volume: this.value });
    });

    // 定时器选择
    document.getElementById('soundsTimer')?.addEventListener('change', function() {
        soundsTimerSeconds = parseInt(this.value) * 60;
        updatePlayerTimer();
        saveSoundPrefs({ timer: this.value });
    });
}

function handleSoundPlay(soundType, card) {
    // 如果点击的是当前播放的音效，则停止
    if (currentSoundType === soundType && currentSoundNode) {
        stopSound();
        return;
    }

    // 停止当前播放
    stopSound();

    // 初始化音频上下文
    ensureAudioContext();

    // 恢复暂停状态
    if (audioCtx.state === 'suspended') {
        audioCtx.resume();
    }
    isPaused = false;

    // 生成音效节点（createSoundNode 内部已调用 source.start()）
    currentSoundNode = createSoundNode(audioCtx, soundType);
    currentSoundType = soundType;
    currentCard = card;

    // 创建当前音效的独立增益节点（用于淡入淡出）
    currentGainNode = audioCtx.createGain();
    const volume = document.getElementById('playerVolume') ?
        document.getElementById('playerVolume').value / 100 : 0.5;

    // 连接链路：音效节点 -> 独立增益 -> 主增益
    currentSoundNode.connect(currentGainNode);
    currentGainNode.connect(masterGainNode);

    // 淡入启动（约 0.8s），避免播放瞬间的"嘭"声吵到宝宝
    const now = audioCtx.currentTime;
    currentGainNode.gain.cancelScheduledValues(now);
    currentGainNode.gain.setValueAtTime(0.0001, now);
    currentGainNode.gain.exponentialRampToValueAtTime(Math.max(volume, 0.0001), now + 0.8);

    // 更新UI
    const iconEl = document.getElementById('playerIcon');
    const nameEl = document.getElementById('playerName');
    const playerEl = document.getElementById('soundsPlayer');
    if (iconEl) iconEl.textContent = card.querySelector('.sound-icon').textContent;
    if (nameEl) nameEl.textContent = card.querySelector('.sound-name').textContent;
    if (playerEl) playerEl.style.display = 'flex';
    card.classList.add('playing');
    card.querySelector('.sound-status').textContent = '播放中';
    const pauseBtn = document.getElementById('playerPause');
    if (pauseBtn) pauseBtn.textContent = '[暂停]';

    // 设置定时器
    const timerValue = parseInt(document.getElementById('soundsTimer')?.value || 0);
    if (timerValue > 0) {
        soundsTimerSeconds = timerValue * 60;
        startSoundsTimer();
    }
}

function createSoundNode(ctx, type) {
    switch (type) {
        case 'white-noise': return createWhiteNoiseNode(ctx);
        case 'pink-noise': return createPinkNoiseNode(ctx);
        case 'brown-noise': return createBrownNoiseNode(ctx);
        case 'heartbeat': return createHeartbeatNode(ctx);
        case 'ocean': return createOceanNode(ctx);
        case 'rain': return createRainNode(ctx);
        case 'shushing': return createShushingNode(ctx);
        case 'fan': return createFanNode(ctx);
        case 'lullaby': return createLullabyNode(ctx);
        default: return createWhiteNoiseNode(ctx);
    }
}

/* ---------- 噪音缓冲与无缝循环 ---------- */

// 白噪音源数据
function fillWhite(a, n) {
    for (let i = 0; i < n; i++) {
        a[i] = Math.random() * 2 - 1;
    }
}

// 粉红噪音源数据（Paul Kellet 滤波法，能量随频率减半而减半）
function fillPink(a, n) {
    let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
    for (let i = 0; i < n; i++) {
        const white = Math.random() * 2 - 1;
        b0 = 0.99886 * b0 + white * 0.0555179;
        b1 = 0.99332 * b1 + white * 0.0750759;
        b2 = 0.96900 * b2 + white * 0.1538520;
        b3 = 0.86650 * b3 + white * 0.3104856;
        b4 = 0.55000 * b4 + white * 0.5329522;
        b5 = -0.7616 * b5 - white * 0.0168980;
        a[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362) * 0.11;
        b6 = white * 0.115926;
    }
}

// 棕噪音源数据（积分白噪音，需限幅避免超出 [-1,1] 削波）
function fillBrown(a, n) {
    let last = 0;
    for (let i = 0; i < n; i++) {
        const white = Math.random() * 2 - 1;
        last = (last + 0.02 * white) / 1.02;
        a[i] = Math.max(-1, Math.min(1, last * 3.5));
    }
}

// 生成无缝循环噪音缓冲：把缓冲末尾与开头做交叉淡化，
// 消除循环点处的波形跳变（否则每隔几秒能听到"咔哒"声）
function makeLoopNoiseBuffer(ctx, seconds, genFn, fadeSeconds) {
const rate = ctx.sampleRate;
const L = Math.max(1, Math.floor(seconds * rate));
const F = Math.max(1, Math.floor((fadeSeconds || 0.05) * rate));
const src = new Float32Array(L + F);
genFn(src, L + F);
const buffer = ctx.createBuffer(1, L, rate);
const out = buffer.getChannelData(0);
for (let i = F; i < L; i++) {
out[i] = src[i];
}
for (let i = 0; i < F; i++) {
const t = i / F; // 0 → 1
out[i] = src[L + i] * (1 - t) + src[i] * t;
}
return buffer;
}

function loopNoiseSource(ctx, seconds, genFn, fadeSeconds) {
const source = ctx.createBufferSource();
source.buffer = makeLoopNoiseBuffer(ctx, seconds, genFn, fadeSeconds);
source.loop = true;
return source;
}

/* ---------- 三种基础噪音 ---------- */

// 白噪音：低通软化高频嘶嘶声 + 高通去除次声隆隆，输出校准到温和水平。
// 原始满幅白噪音是刺耳的电流声，必须滤波后才是安抚用的"沙沙"声
function createWhiteNoiseNode(ctx) {
const source = loopNoiseSource(ctx, 4, fillWhite);

const hp = ctx.createBiquadFilter();
hp.type = 'highpass';
hp.frequency.value = 100;

const lp = ctx.createBiquadFilter();
lp.type = 'lowpass';
lp.frequency.value = 1500;
lp.Q.value = 0.7;

const gainNode = ctx.createGain();
gainNode.gain.value = 0.5;

source.connect(hp);
hp.connect(lp);
lp.connect(gainNode);
source.start();

gainNode.stop = function() {
try { source.stop(); } catch (e) {}
gainNode.disconnect();
};
return gainNode;
}

// 粉红噪音：频谱本身柔和，只做轻度限带与音量校准
function createPinkNoiseNode(ctx) {
const source = loopNoiseSource(ctx, 4, fillPink);

const hp = ctx.createBiquadFilter();
hp.type = 'highpass';
hp.frequency.value = 60;

const lp = ctx.createBiquadFilter();
lp.type = 'lowpass';
lp.frequency.value = 4000;
lp.Q.value = 0.5;

const gainNode = ctx.createGain();
gainNode.gain.value = 0.7;

source.connect(hp);
hp.connect(lp);
lp.connect(gainNode);
source.start();

gainNode.stop = function() {
try { source.stop(); } catch (e) {}
gainNode.disconnect();
};
return gainNode;
}

// 棕噪音：深沉轰鸣，低通后输出
function createBrownNoiseNode(ctx) {
const source = loopNoiseSource(ctx, 4, fillBrown);

const lp = ctx.createBiquadFilter();
lp.type = 'lowpass';
lp.frequency.value = 800;
lp.Q.value = 0.7;

const gainNode = ctx.createGain();
gainNode.gain.value = 0.85;

source.connect(lp);
lp.connect(gainNode);
source.start();

gainNode.stop = function() {
try { source.stop(); } catch (e) {}
gainNode.disconnect();
};
return gainNode;
}

function createHeartbeatNode(ctx) {
    // 真实心跳 = 低频正弦脉冲 + lub-dub 双击包络（68 BPM）。
    // 不再用 LFO 直接调制增益（会使增益变负产生相位反转嗡嗡声），
    // 改为按时间轴调度包络，声音是"咚-咚"而不是电流震动
    const entry = ctx.createGain();
    entry.gain.value = 0.9;

    const osc = ctx.createOscillator();
    osc.type = 'sine';
    osc.frequency.value = 55;

    const env = ctx.createGain();
    env.gain.value = 0;
    osc.connect(env);
    env.connect(entry);
    osc.start();

    const beat = 60 / 68; // 68 BPM
    let nextTime = ctx.currentTime + 0.05;

    function scheduleBeat(t, peak) {
        // "lub"（强）
        env.gain.setValueAtTime(0.0001, t);
        env.gain.exponentialRampToValueAtTime(peak, t + 0.02);
        env.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
        // "dub"（弱，间隔 0.22s）
        env.gain.setValueAtTime(0.0001, t + 0.22);
        env.gain.exponentialRampToValueAtTime(peak * 0.7, t + 0.24);
        env.gain.exponentialRampToValueAtTime(0.0001, t + 0.38);
    }

    // 预调度首次心跳，之后用调度器每次提前 1.2s 补排，保证无限循环不卡顿
    scheduleBeat(nextTime, 0.9);
    nextTime += beat;
    const timer = setInterval(() => {
        while (nextTime < ctx.currentTime + 1.2) {
            scheduleBeat(nextTime, 0.9);
            nextTime += beat;
        }
    }, 400);

    entry.stop = function() {
        clearInterval(timer);
        try { osc.stop(); } catch (e) {}
        entry.disconnect();
    };
    return entry;
}

function createOceanNode(ctx) {
    // 海浪：棕噪音打底 + 低通滤波 + 慢 LFO 起伏，
    // 听感是"哗——哗——"的浪涌而不是电流嘶嘶声
    const source = loopNoiseSource(ctx, 4, fillBrown);

    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = 600;
    filter.Q.value = 0.7;

    const gainNode = ctx.createGain();
    gainNode.gain.value = 0.7;

    const lfo = ctx.createOscillator();
    lfo.type = 'sine';
    lfo.frequency.value = 0.12; // 约 8 秒一个浪

    const lfoGain = ctx.createGain();
    lfoGain.gain.value = 0.22;

    lfo.connect(lfoGain);
    lfoGain.connect(gainNode.gain);

    source.connect(filter);
    filter.connect(gainNode);

    lfo.start();
    source.start();

    gainNode.stop = function() {
        try { lfo.stop(); } catch (e) {}
        try { source.stop(); } catch (e) {}
        gainNode.disconnect();
    };

    return gainNode;
}

function createRainNode(ctx) {
    // 雨声：粉红噪音 + 低通限带（雨声主体在中低频，
    // 之前的带通 3kHz 等于只留最刺耳的高频嘶嘶）
    const source = loopNoiseSource(ctx, 4, fillPink);

    const hp = ctx.createBiquadFilter();
    hp.type = 'highpass';
    hp.frequency.value = 300;

    const lp = ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.value = 2600;
    lp.Q.value = 0.5;

    const gainNode = ctx.createGain();
    gainNode.gain.value = 0.7;

    source.connect(hp);
    hp.connect(lp);
    lp.connect(gainNode);
    source.start();

    gainNode.stop = function() {
        try { source.stop(); } catch (e) {}
        gainNode.disconnect();
    };

    return gainNode;
}

function createShushingNode(ctx) {
    // 嘘声：白噪音 + 带通滤波（中频"shhh"），
    // 之前用高通 2kHz 等于只保留最刺耳的高频嘶嘶，与安抚目标背道而驰；
    // LFO 缓慢移动滤波器中心频率，模拟哄睡时呼吸般的嘘声起伏
    const source = loopNoiseSource(ctx, 4, fillWhite);

    const filter = ctx.createBiquadFilter();
    filter.type = 'bandpass';
    filter.frequency.value = 550;
    filter.Q.value = 0.9;

    const lfo = ctx.createOscillator();
    lfo.type = 'sine';
    lfo.frequency.value = 0.22;

    const lfoGain = ctx.createGain();
    lfoGain.gain.value = 200; // 中心频率在 350-750Hz 间缓慢呼吸

    lfo.connect(lfoGain);
    lfoGain.connect(filter.frequency);

    const gainNode = ctx.createGain();
    gainNode.gain.value = 0.75;

    source.connect(filter);
    filter.connect(gainNode);

    lfo.start();
    source.start();

    gainNode.stop = function() {
        try { lfo.stop(); } catch (e) {}
        try { source.stop(); } catch (e) {}
        gainNode.disconnect();
    };

    return gainNode;
}

function createFanNode(ctx) {
    // 风扇声：棕噪音低通（风声主体）+ 轻微 118Hz 电机嗡鸣，
    // 稳定不刺耳，适合掩盖环境噪音
    const source = loopNoiseSource(ctx, 4, fillBrown);

    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = 320;
    filter.Q.value = 0.7;

    const hum = ctx.createOscillator();
    hum.type = 'sine';
    hum.frequency.value = 118;

    const humGain = ctx.createGain();
    humGain.gain.value = 0.04;

    const gainNode = ctx.createGain();
    gainNode.gain.value = 0.9;

    source.connect(filter);
    filter.connect(gainNode);
    hum.connect(humGain);
    humGain.connect(gainNode);

    hum.start();
    source.start();

    gainNode.stop = function() {
        try { hum.stop(); } catch (e) {}
        try { source.stop(); } catch (e) {}
        gainNode.disconnect();
    };

    return gainNode;
}

function createLullabyNode(ctx) {
    // 摇篮曲：勃拉姆斯摇篮曲主旋律片段，柔和正弦波 + 软包络循环播放。
    // 旧版只播一遍就静止，且音符用 setValueAtTime 硬切音量会产生咔哒声
    const entry = ctx.createGain();
    entry.gain.value = 0.9;

    const lp = ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.value = 2400;
    lp.connect(entry);

    const osc = ctx.createOscillator();
    osc.type = 'sine';

    const env = ctx.createGain();
    env.gain.value = 0;
    osc.connect(env);
    env.connect(lp);
    osc.start();

    // [频率, 拍数]：小星星（Twinkle Twinkle Little Star）
    const notes = [
        [523.25, 1], [523.25, 1], [783.99, 1], [783.99, 1], // C C G G
        [880.00, 1], [880.00, 1], [783.99, 2],              // A A G-
        [698.46, 1], [698.46, 1], [659.25, 1], [659.25, 1], // F F E E
        [587.33, 1], [587.33, 1], [523.25, 2]               // D D C-
    ];
    const unit = 0.55; // 每拍 0.55 秒，轻柔慢速
    let t = ctx.currentTime + 0.1;
    let idx = 0;

    function scheduleNote(time, freq, dur) {
        osc.frequency.setValueAtTime(freq, time);
        // 软起音 + 软收音，两端归零避免爆音
        env.gain.setValueAtTime(0.0001, time);
        env.gain.exponentialRampToValueAtTime(0.22, time + 0.06);
        env.gain.setValueAtTime(0.22, time + dur - 0.12);
        env.gain.exponentialRampToValueAtTime(0.0001, time + dur);
    }

    // 预排一小段，之后调度器每次提前 2s 补排，无限循环
    let schedEnd = t;
    for (let k = 0; k < 4; k++) {
        const n = notes[idx % notes.length];
        const dur = n[1] * unit;
        scheduleNote(schedEnd, n[0], dur);
        schedEnd += dur;
        idx++;
    }
    const timer = setInterval(() => {
        while (schedEnd < ctx.currentTime + 2) {
            const n = notes[idx % notes.length];
            const dur = n[1] * unit;
            scheduleNote(schedEnd, n[0], dur);
            schedEnd += dur;
            idx++;
        }
    }, 500);

    entry.stop = function() {
        clearInterval(timer);
        try { osc.stop(); } catch (e) {}
        entry.disconnect();
    };
    return entry;
}

function stopSound() {
    // 清除之前待执行的停止操作，避免重复停止
    if (stopSoundTimeout) {
        clearTimeout(stopSoundTimeout);
        stopSoundTimeout = null;
    }

    const node = currentSoundNode;
    const gain = currentGainNode;
    const card = currentCard;
    currentSoundNode = null;
    currentGainNode = null;
    currentSoundType = null;
    currentCard = null;
    isPaused = false;

    // 淡出后再停止（约 0.4s），避免戛然而止的"嘭"声惊醒宝宝
    if (gain && audioCtx && audioCtx.state === 'running') {
        try {
            const now = audioCtx.currentTime;
            gain.gain.cancelScheduledValues(now);
            gain.gain.setValueAtTime(Math.max(gain.gain.value, 0.0001), now);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);
        } catch (e) {
            // 增益节点可能已断开，忽略错误
        }
    }
    stopSoundTimeout = setTimeout(() => {
        stopSoundTimeout = null;
        // 停止音效节点的音源（内部 source/osc）
        if (node) {
            try { if (node.stop) node.stop(); } catch (e) {}
            try { if (node.disconnect) node.disconnect(); } catch (e) {}
        }
        // 断开增益节点
        if (gain) {
            try { gain.disconnect(); } catch (e) {}
        }
    }, 400);

    // 清除定时器
    if (soundsTimerInterval) {
        clearInterval(soundsTimerInterval);
        soundsTimerInterval = null;
    }

    // 重置UI
    const player = document.getElementById('soundsPlayer');
    if (player) player.style.display = 'none';
    document.querySelectorAll('.sound-card.playing').forEach(c => {
        c.classList.remove('playing');
        const status = c.querySelector('.sound-status');
        if (status) status.textContent = '点击播放';
    });
    const pauseBtn = document.getElementById('playerPause');
    if (pauseBtn) pauseBtn.textContent = '[暂停]';
}

function togglePauseSound() {
    if (!audioCtx || !currentSoundType) return;

    if (isPaused) {
        // 恢复播放
        audioCtx.resume();
        isPaused = false;
        const pauseBtn = document.getElementById('playerPause');
        if (pauseBtn) pauseBtn.textContent = '[暂停]';
        if (currentCard) {
            const status = currentCard.querySelector('.sound-status');
            if (status) status.textContent = '播放中';
        }
    } else {
        // 暂停播放
        audioCtx.suspend();
        isPaused = true;
        const pauseBtn = document.getElementById('playerPause');
        if (pauseBtn) pauseBtn.textContent = '[继续]';
        if (currentCard) {
            const status = currentCard.querySelector('.sound-status');
            if (status) status.textContent = '已暂停';
        }
    }
}

function startSoundsTimer() {
    if (soundsTimerInterval) {
        clearInterval(soundsTimerInterval);
    }
    soundsTimerInterval = setInterval(() => {
        soundsTimerSeconds--;
        updatePlayerTimer();
        if (soundsTimerSeconds <= 0) {
            stopSound();
        }
    }, 1000);
}

function updatePlayerTimer() {
    const timerEl = document.getElementById('playerTimer');
    if (!timerEl) return;
    if (soundsTimerSeconds <= 0) {
        timerEl.textContent = '剩余: ∞';
    } else {
        const min = Math.floor(soundsTimerSeconds / 60);
        const sec = soundsTimerSeconds % 60;
        timerEl.textContent = `剩余: ${min.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
    }
}

/**
 * 清理所有音频资源（页面卸载时调用）
 */
function cleanupAudioResources() {
    // 清除待执行的停止操作
    if (stopSoundTimeout) {
        clearTimeout(stopSoundTimeout);
        stopSoundTimeout = null;
    }
    // 清除定时器
    if (soundsTimerInterval) {
        clearInterval(soundsTimerInterval);
        soundsTimerInterval = null;
    }
    // 停止当前音效
    if (currentSoundNode) {
        try { if (currentSoundNode.stop) currentSoundNode.stop(); } catch (e) {}
        try { if (currentSoundNode.disconnect) currentSoundNode.disconnect(); } catch (e) {}
    }
    if (currentGainNode) {
        try { currentGainNode.disconnect(); } catch (e) {}
    }
    // 关闭 AudioContext
    if (audioCtx) {
        try { audioCtx.close(); } catch (e) {}
        audioCtx = null;
        masterGainNode = null;
        compressorNode = null;
    }
    currentSoundNode = null;
    currentGainNode = null;
    currentSoundType = null;
    currentCard = null;
    isPaused = false;
}

// 页面卸载时清理音频资源
window.addEventListener('beforeunload', cleanupAudioResources);

// ==================== 辅食食谱 ====================
let currentRecipeId = null;
let currentRecipeData = null;
let completedSteps = [];

// ==================== 食谱收藏 ====================

function getFavoriteRecipes() {
    try {
        return JSON.parse(localStorage.getItem('favoriteRecipes') || '[]');
    } catch {
        return [];
    }
}

function isRecipeFavorite(id) {
    return getFavoriteRecipes().includes(id);
}

function toggleRecipeFavorite(id) {
    const favs = getFavoriteRecipes();
    const idx = favs.indexOf(id);
    if (idx >= 0) {
        favs.splice(idx, 1);
    } else {
        favs.push(id);
    }
    localStorage.setItem('favoriteRecipes', JSON.stringify(favs));
    return idx < 0;
}

function updateFavoriteBtn() {
    const btn = document.getElementById('favoriteRecipeBtn');
    if (!btn || !currentRecipeId) return;
    const fav = isRecipeFavorite(currentRecipeId);
    btn.textContent = fav ? '★' : '☆';
    btn.style.color = fav ? '#f59e0b' : '';
    btn.title = fav ? '取消收藏' : '收藏食谱';
}

// ==================== 烹饪模式 ====================

let cookingModeState = {
    active: false,
    currentStep: 0,
    totalSteps: 0,
    timer: null,
    seconds: 0
};

function enterCookingMode() {
    if (!currentRecipeData || !currentRecipeData.instructions_list) return;
    const steps = currentRecipeData.instructions_list;
    if (steps.length === 0) return;

    cookingModeState = {
        active: true,
        currentStep: 0,
        totalSteps: steps.length,
        timer: null,
        seconds: 0
    };

    const container = document.getElementById('recipeModalBody');
    if (!container) return;

    renderCookingModeView(container, steps);
}

function renderCookingModeView(container, steps) {
    const state = cookingModeState;
    const step = steps[state.currentStep];
    const progress = ((state.currentStep + 1) / state.totalSteps * 100).toFixed(0);

    container.innerHTML = `
        <div class="cooking-mode">
            <div class="cooking-mode-header">
                <button class="btn btn-sm btn-secondary" onclick="exitCookingMode()">← 退出烹饪模式</button>
                <span class="cooking-step-counter">步骤 ${state.currentStep + 1} / ${state.totalSteps}</span>
            </div>
            <div class="cooking-progress-bar">
                <div class="cooking-progress-fill" style="width: ${progress}%"></div>
            </div>
            <div class="cooking-step-card">
                <div class="cooking-step-number">${state.currentStep + 1}</div>
                <div class="cooking-step-text">${escapeHtml(step)}</div>
            </div>
            <div class="cooking-timer-bar">
                <span id="cookingTimerDisplay">00:00</span>
                <button class="btn btn-sm btn-secondary" onclick="toggleCookingTimer()" id="cookingTimerBtn">开始计时</button>
            </div>
            <div class="cooking-mode-nav">
                <button class="btn btn-primary" onclick="prevCookingStep()" ${state.currentStep === 0 ? 'disabled' : ''}>上一步</button>
                <button class="btn btn-primary" onclick="nextCookingStep()" ${state.currentStep >= state.totalSteps - 1 ? 'disabled' : ''}>下一步</button>
            </div>
            ${state.currentStep >= state.totalSteps - 1 ? '<div class="cooking-done-tip">恭喜！所有步骤已完成</div>' : ''}
        </div>
    `;
}

function exitCookingMode() {
    if (cookingModeState.timer) {
        clearInterval(cookingModeState.timer);
    }
    cookingModeState = { active: false, currentStep: 0, totalSteps: 0, timer: null, seconds: 0 };
    if (currentRecipeData) {
        renderRecipeModal(currentRecipeData);
    }
}

function nextCookingStep() {
    if (!cookingModeState.active) return;
    if (cookingModeState.currentStep < cookingModeState.totalSteps - 1) {
        cookingModeState.currentStep++;
        const steps = currentRecipeData.instructions_list;
        const container = document.getElementById('recipeModalBody');
        if (container) renderCookingModeView(container, steps);
    }
}

function prevCookingStep() {
    if (!cookingModeState.active) return;
    if (cookingModeState.currentStep > 0) {
        cookingModeState.currentStep--;
        const steps = currentRecipeData.instructions_list;
        const container = document.getElementById('recipeModalBody');
        if (container) renderCookingModeView(container, steps);
    }
}

function toggleCookingTimer() {
    const btn = document.getElementById('cookingTimerBtn');
    const display = document.getElementById('cookingTimerDisplay');
    if (!btn || !display) return;

    if (cookingModeState.timer) {
        clearInterval(cookingModeState.timer);
        cookingModeState.timer = null;
        btn.textContent = '开始计时';
    } else {
        cookingModeState.timer = setInterval(() => {
            cookingModeState.seconds++;
            const m = Math.floor(cookingModeState.seconds / 60).toString().padStart(2, '0');
            const s = (cookingModeState.seconds % 60).toString().padStart(2, '0');
            display.textContent = `${m}:${s}`;
        }, 1000);
        btn.textContent = '暂停计时';
    }
}

function initRecipesPage() {
    if (App.recipesPageInitialized) return;
    App.recipesPageInitialized = true;

    document.getElementById('recipeAgeFilter')?.addEventListener('change', loadRecipes);
    document.getElementById('recipeCategoryFilter')?.addEventListener('change', loadRecipes);
    document.getElementById('recipeSearchInput')?.addEventListener('input', debounce(loadRecipes, 300));
    document.getElementById('recipeFavFilter')?.addEventListener('change', loadRecipes);
    document.getElementById('closeRecipeModal')?.addEventListener('click', closeRecipeModal);
    document.getElementById('addRecipeBtn')?.addEventListener('click', openRecipeForm);
    document.getElementById('closeRecipeFormModal')?.addEventListener('click', closeRecipeFormModal);
    document.getElementById('cancelRecipeForm')?.addEventListener('click', closeRecipeFormModal);
    document.getElementById('recipeForm')?.addEventListener('submit', saveRecipeForm);
    document.getElementById('editRecipeBtn')?.addEventListener('click', editCurrentRecipe);
    document.getElementById('deleteRecipeBtn')?.addEventListener('click', deleteCurrentRecipe);
    document.getElementById('printRecipeBtn')?.addEventListener('click', printCurrentRecipe);
    document.getElementById('favoriteRecipeBtn')?.addEventListener('click', function() {
        if (!currentRecipeId) return;
        const added = toggleRecipeFavorite(currentRecipeId);
        updateFavoriteBtn();
        showToast(added ? '已添加到收藏' : '已取消收藏');
    });
    document.getElementById('cookingModeBtn')?.addEventListener('click', enterCookingMode);
    document.getElementById('recommendRecipeBtn')?.addEventListener('click', loadRecommendedRecipes);
}

// ==================== 智能推荐 ====================

function loadRecommendedRecipes() {
    if (!App.currentBaby) {
        showToast('请先选择宝宝');
        return;
    }
    const container = document.getElementById('recipeList');
    if (container) container.innerHTML = '<p class="empty-tip">正在为您智能推荐...</p>';

    api(`/api/recipes/recommended/${App.currentBaby}`).then(data => {
        if (data.success) {
            const recipes = data.data || [];
            if (recipes.length === 0) {
                if (container) container.innerHTML = '<div class="empty-state"><div class="empty-icon"></div><div class="empty-text">' + (data.message || '暂无推荐食谱') + '</div></div>';
                return;
            }
            renderRecipeList(recipes);
            showToast(data.message || `为您推荐${recipes.length}个食谱`);
        } else {
            if (container) container.innerHTML = '<div class="empty-state"><div class="empty-icon"></div><div class="empty-text">推荐失败，请重试</div></div>';
        }
    }).catch(err => {
        console.error('加载推荐食谱失败:', err);
        if (container) container.innerHTML = '<div class="empty-state"><div class="empty-icon"></div><div class="empty-text">加载失败，请重试</div></div>';
    });
}

function loadRecipes() {
    const ageFilter = document.getElementById('recipeAgeFilter')?.value || '';
    const categoryFilter = document.getElementById('recipeCategoryFilter')?.value || '';
    const search = document.getElementById('recipeSearchInput')?.value || '';
    const favOnly = document.getElementById('recipeFavFilter')?.checked || false;

    let url = '/api/recipes?';
    if (ageFilter) url += `age=${encodeURIComponent(ageFilter)}&`;
    if (categoryFilter) url += `category=${encodeURIComponent(categoryFilter)}&`;
    if (search) url += `search=${encodeURIComponent(search)}&`;

    api(url).then(data => {
        if (data.success) {
            let recipes = data.data || data.recipes || [];
            if (favOnly) {
                const favs = getFavoriteRecipes();
                recipes = recipes.filter(r => favs.includes(r.id));
            }
            renderRecipeList(recipes);
        }
    }).catch(err => console.error('加载食谱失败:', err));
}

function renderRecipeList(recipes) {
    const container = document.getElementById('recipeList');
    if (!container) return;

    if (recipes.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="empty-icon"></div><div class="empty-text">暂无匹配的食谱<br><small>点击右上角"新增食谱"添加</small></div></div>';
        return;
    }

    container.innerHTML = recipes.map(r => {
        const prep = parseFloat(r.prep_time) || 0;
        const cook = parseFloat(r.cook_time) || 0;
        const totalTime = prep + cook;
        const difficultyClass = r.difficulty === '中等' ? 'medium' : r.difficulty === '困难' ? 'hard' : 'easy';
        return `
        <div class="recipe-card" onclick="showRecipeDetail(${r.id})">
            <div class="recipe-icon ${r.icon ? 'recipe-icon-text' : 'recipe-icon-' + (r.category || 'default')}">${r.icon || getCategoryIcon(r.category)}</div>
            <div class="recipe-info">
                <div class="recipe-title">${escapeHtml(r.title)}</div>
                <div class="recipe-meta">
                    ${r.age_group ? `<span>${escapeHtml(r.age_group)}</span>` : ''}
                    ${totalTime > 0 ? `<span>${totalTime}分钟</span>` : ''}
                    ${r.servings ? `<span>${escapeHtml(r.servings)}人份</span>` : ''}
                </div>
                ${r.nutrition ? `<div class="recipe-nutrition">${escapeHtml(r.nutrition)}</div>` : ''}
            </div>
            ${r.difficulty ? `<div class="recipe-difficulty difficulty-${difficultyClass}">${escapeHtml(r.difficulty)}</div>` : ''}
        </div>
    `}).join('');
}

function getCategoryIcon(category) {
    const map = {'谷物': '谷', '蔬菜': '蔬', '水果': '果', '蛋白质': '蛋', '肉类': '肉', '混合': '混', '其他': '食'};
    return map[category] || '食';
}

function showRecipeDetail(id) {
    api(`/api/recipes/${id}`).then(data => {
        if (data.success) {
            currentRecipeId = id;
            currentRecipeData = data.data || data.recipe || {};
            completedSteps = [];
            renderRecipeModal(currentRecipeData);
            updateFavoriteBtn();
            showModal('recipeModal');
        }
    }).catch(err => console.error('加载食谱详情失败:', err));
}

function renderRecipeModal(recipe) {
    const container = document.getElementById('recipeModalBody');
    const titleEl = document.getElementById('recipeDetailTitle');
    if (!container) return;

    titleEl.textContent = recipe.title || '食谱详情';

    const difficultyClass = recipe.difficulty === '中等' ? 'medium' : recipe.difficulty === '困难' ? 'hard' : 'easy';
    const allergensHtml = recipe.allergens_list && recipe.allergens_list.length > 0
        ? `<div class="recipe-allergens">[!] 过敏原: ${recipe.allergens_list.map(a => escapeHtml(a)).join('、')}</div>`
        : '';

    const baseServings = parseFloat(recipe.servings) || 1;

    container.innerHTML = `
        <div class="recipe-detail-header">
            <div class="recipe-detail-icon ${recipe.icon ? 'recipe-icon-text' : 'recipe-icon-' + (recipe.category || 'default')}">${recipe.icon || getCategoryIcon(recipe.category)}</div>
            <div class="recipe-detail-title">${escapeHtml(recipe.title)}</div>
        </div>
        <div class="recipe-detail-meta">
            ${recipe.age_group ? `<span>${escapeHtml(recipe.age_group)}</span>` : ''}
            ${recipe.prep_time ? `<span>准备${escapeHtml(recipe.prep_time)}分钟</span>` : ''}
            ${recipe.cook_time ? `<span>烹饪${escapeHtml(recipe.cook_time)}分钟</span>` : ''}
            ${recipe.difficulty ? `<span class="recipe-difficulty difficulty-${difficultyClass}">${escapeHtml(recipe.difficulty)}</span>` : ''}
        </div>
        <div class="recipe-servings-bar">
            <span>份量：</span>
            <button class="btn btn-sm btn-secondary" onclick="adjustServings(-1)">-</button>
            <span id="servingsDisplay">${recipe.servings || 1}人份</span>
            <button class="btn btn-sm btn-secondary" onclick="adjustServings(1)">+</button>
            <input type="hidden" id="baseServings" value="${baseServings}">
        </div>
        <div class="recipe-detail-section">
            <h4>食材</h4>
            <div class="recipe-ingredients" id="recipeIngredientsList">
                ${(recipe.ingredients_list || []).map(i => `<span class="ingredient-tag">${escapeHtml(i)}</span>`).join('')}
            </div>
        </div>
        <div class="recipe-detail-section">
            <h4>步骤 <span class="tip">(点击勾选已完成)</span></h4>
            <ol class="recipe-steps" id="recipeStepsList">
                ${(recipe.instructions_list || []).map((s, i) => `<li class="recipe-step" data-step="${i}" onclick="toggleStep(${i})"><span class="step-checkbox"></span><span class="step-text">${escapeHtml(s)}</span></li>`).join('')}
            </ol>
        </div>
        ${recipe.nutrition ? `<div class="recipe-detail-section"><h4>营养价值</h4><p>${escapeHtml(recipe.nutrition)}</p></div>` : ''}
        ${recipe.tips ? `<div class="recipe-detail-section"><h4>小贴士</h4><p>${escapeHtml(recipe.tips)}</p></div>` : ''}
        ${allergensHtml}
    `;
}

function adjustServings(delta) {
    const baseInput = document.getElementById('baseServings');
    const display = document.getElementById('servingsDisplay');
    if (!baseInput || !display) return;
    const base = parseFloat(baseInput.value) || 1;
    let current = parseFloat(display.textContent) || base;
    current = Math.max(0.5, current + delta);
    display.textContent = (current % 1 === 0 ? current : current.toFixed(1)) + '人份';
}

function toggleStep(index) {
    const stepEl = document.querySelector(`.recipe-step[data-step="${index}"]`);
    if (!stepEl) return;
    if (completedSteps.includes(index)) {
        completedSteps = completedSteps.filter(i => i !== index);
        stepEl.classList.remove('completed');
    } else {
        completedSteps.push(index);
        stepEl.classList.add('completed');
    }
}

function closeRecipeModal() {
    hideModal('recipeModal');
    currentRecipeId = null;
    currentRecipeData = null;
    completedSteps = [];
}

// ==================== 食谱表单（新增/编辑） ====================

function openRecipeForm(recipe) {
    const modal = document.getElementById('recipeFormModal');
    const title = document.getElementById('recipeFormTitle');
    const form = document.getElementById('recipeForm');
    if (!modal) return;

    form.reset();

    if (recipe) {
        title.textContent = '编辑食谱';
        document.getElementById('recipeFormId').value = recipe.id || '';
        document.getElementById('recipeFormTitleInput').value = recipe.title || '';
        document.getElementById('recipeFormAge').value = recipe.age_group || '6月+';
        document.getElementById('recipeFormCategory').value = recipe.category || '其他';
        document.getElementById('recipeFormPrepTime').value = recipe.prep_time || '';
        document.getElementById('recipeFormCookTime').value = recipe.cook_time || '';
        document.getElementById('recipeFormServings').value = recipe.servings || '';
        document.getElementById('recipeFormDifficulty').value = recipe.difficulty || '简单';
        document.getElementById('recipeFormIngredients').value = recipe.ingredients || '';
        document.getElementById('recipeFormInstructions').value = recipe.instructions || '';
        document.getElementById('recipeFormAllergens').value = recipe.allergens || '';
        document.getElementById('recipeFormNutrition').value = recipe.nutrition || '';
        document.getElementById('recipeFormTips').value = recipe.tips || '';
        document.getElementById('recipeFormIcon').value = recipe.icon || '';
    } else {
        title.textContent = '新增食谱';
        document.getElementById('recipeFormId').value = '';
    }

    showModal('recipeFormModal');
}

function closeRecipeFormModal() {
    hideModal('recipeFormModal');
}

function saveRecipeForm(e) {
    e.preventDefault();
    const id = document.getElementById('recipeFormId').value;
    const data = {
        title: document.getElementById('recipeFormTitleInput').value.trim(),
        icon: document.getElementById('recipeFormIcon').value,
        age_group: document.getElementById('recipeFormAge').value,
        category: document.getElementById('recipeFormCategory').value,
        prep_time: document.getElementById('recipeFormPrepTime').value,
        cook_time: document.getElementById('recipeFormCookTime').value,
        servings: document.getElementById('recipeFormServings').value,
        difficulty: document.getElementById('recipeFormDifficulty').value,
        ingredients: document.getElementById('recipeFormIngredients').value,
        instructions: document.getElementById('recipeFormInstructions').value,
        allergens: document.getElementById('recipeFormAllergens').value || '无',
        nutrition: document.getElementById('recipeFormNutrition').value,
        tips: document.getElementById('recipeFormTips').value,
    };

    if (!data.title) {
        showToast('请填写食谱名称');
        return;
    }

    const url = id ? `/api/recipes/${id}` : '/api/recipes';
    const method = id ? 'PUT' : 'POST';

    api(url, { method, body: JSON.stringify(data) }).then(res => {
        if (res.success) {
            showToast(id ? '食谱已更新' : '食谱已添加');
            closeRecipeFormModal();
            loadRecipes();
        } else {
            showToast(res.message || '保存失败');
        }
    }).catch(err => {
        console.error('保存食谱失败:', err);
        showToast('保存失败，请重试');
    });
}

function editCurrentRecipe() {
    if (currentRecipeData) {
        closeRecipeModal();
        openRecipeForm(currentRecipeData);
    }
}

function deleteCurrentRecipe() {
    if (!currentRecipeId) return;
    if (!confirm('确定要删除这个食谱吗？删除后无法恢复。')) return;

    api(`/api/recipes/${currentRecipeId}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast('食谱已删除');
            closeRecipeModal();
            loadRecipes();
        } else {
            showToast(res.message || '删除失败');
        }
    }).catch(err => {
        console.error('删除食谱失败:', err);
        showToast('删除失败，请重试');
    });
}

function printCurrentRecipe() {
    if (!currentRecipeData) return;
    const r = currentRecipeData;
    const printWindow = window.open('', '_blank');
    if (!printWindow) return;

    const stepsHtml = (r.instructions_list || []).map((s, i) => `<li>${escapeHtml(s)}</li>`).join('');
    const ingredientsHtml = (r.ingredients_list || []).map(i => `<li>${escapeHtml(i)}</li>`).join('');

    printWindow.document.write(`
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>${escapeHtml(r.title)} - 育儿宝</title>
    <style>
        body { font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333; }
        h1 { text-align: center; font-size: 24px; margin-bottom: 8px; }
        .meta { text-align: center; color: #666; font-size: 14px; margin-bottom: 20px; }
        .meta span { margin: 0 8px; }
        h3 { font-size: 16px; border-bottom: 1px solid #eee; padding-bottom: 6px; margin-top: 20px; }
        ol, ul { padding-left: 20px; }
        li { margin-bottom: 6px; line-height: 1.6; }
        .tips { background: #FFF8E1; padding: 12px; border-radius: 8px; margin-top: 16px; }
        .allergens { background: #FFEBEE; padding: 12px; border-radius: 8px; margin-top: 12px; color: #C62828; }
        .footer { text-align: center; color: #999; font-size: 12px; margin-top: 30px; }
    </style>
</head>
<body>
    <h1>${escapeHtml(r.title)}</h1>
    <div class="meta">
        ${r.age_group ? `<span>${escapeHtml(r.age_group)}</span>` : ''}
        ${r.prep_time ? `<span>准备${escapeHtml(r.prep_time)}分钟</span>` : ''}
        ${r.cook_time ? `<span>烹饪${escapeHtml(r.cook_time)}分钟</span>` : ''}
        ${r.servings ? `<span>${escapeHtml(r.servings)}人份</span>` : ''}
    </div>
    <h3>食材</h3>
    <ul>${ingredientsHtml}</ul>
    <h3>步骤</h3>
    <ol>${stepsHtml}</ol>
    ${r.nutrition ? `<h3>营养价值</h3><p>${escapeHtml(r.nutrition)}</p>` : ''}
    ${r.tips ? `<div class="tips">小贴士: ${escapeHtml(r.tips)}</div>` : ''}
    ${r.allergens_list && r.allergens_list.length > 0 ? `<div class="allergens">过敏原: ${r.allergens_list.map(a => escapeHtml(a)).join('、')}</div>` : ''}
    <div class="footer">育儿宝 - 辅食食谱</div>
</body>
</html>
    `);
    printWindow.document.close();
    printWindow.print();
}

function debounce(fn, delay) {
    let timer;
    return function(...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

// ==================== 活动游戏推荐 ====================
function initActivitiesPage() {
    if (App.activitiesPageInitialized) return;
    App.activitiesPageInitialized = true;

    // 绑定分类筛选按钮
    document.querySelectorAll('.activity-cat-ext-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.activity-cat-ext-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadActivities(btn.dataset.cat);
        });
    });

    // 绑定统计按钮
    const statsBtn = document.getElementById('activityStatsBtn');
    if (statsBtn && !statsBtn._bound) {
        statsBtn._bound = true;
        statsBtn.addEventListener('click', showActivityStatsModal);
    }

    // 绑定收藏按钮
    const favoritesBtn = document.getElementById('activityFavoritesBtn');
    if (favoritesBtn && !favoritesBtn._bound) {
        favoritesBtn._bound = true;
        favoritesBtn.addEventListener('click', showActivityFavoritesModal);
    }

    // 绑定家长指导按钮
    const guideBtn = document.getElementById('activityGuideBtn');
    if (guideBtn && !guideBtn._bound) {
        guideBtn._bound = true;
        guideBtn.addEventListener('click', showParentGuideModal);
    }
}

function loadActivities(category = 'all') {
    let url = '/api/activities?';
    if (category && category !== 'all') url += `category=${category}&`;

    // 获取宝宝年龄来筛选
    const baby = App.babies?.find(b => b.id === App.currentBaby);
    if (baby && baby.birthday) {
        const ageMonths = calcAgeMonths(baby.birthday);
        url += `age_months=${ageMonths}`;
        updateAgeBadge(ageMonths);
    }

    api(url).then(data => {
        if (data.success) {
            renderActivityList(data.data);
        }
    }).catch(err => console.error('加载活动失败:', err));

    // 加载智能推荐
    loadActivityRecommend();
}

function loadActivityRecommend() {
    if (!App.currentBaby) return;
    const baby = App.babies?.find(b => b.id === App.currentBaby);
    const ageMonths = baby && baby.birthday ? calcAgeMonths(baby.birthday) : 0;

    api(`/api/activities/recommend?baby_id=${App.currentBaby}&age_months=${ageMonths}&limit=5`).then(data => {
        if (data.success && data.data.length > 0) {
            renderActivityRecommend(data.data);
        } else {
            const container = document.getElementById('activityRecommendList');
            if (container) container.innerHTML = '<p class="empty-tip">暂无推荐</p>';
        }
    }).catch(err => {
        console.error('加载推荐失败:', err);
        const container = document.getElementById('activityRecommendList');
        if (container) container.innerHTML = '<p class="empty-tip">加载推荐失败</p>';
    });
}

function renderActivityRecommend(activities) {
    const container = document.getElementById('activityRecommendList');
    if (!container) return;

    container.innerHTML = activities.map(a => `
        <div class="activity-recommend-card" onclick="showActivityDetail(${a.id})">
            <div class="activity-recommend-icon">${a.icon || '🎮'}</div>
            <div class="activity-recommend-title">${escapeHtml(a.title)}</div>
            <div class="activity-recommend-meta">${a.duration || ''} · ${a.difficulty || ''}</div>
        </div>
    `).join('');
}

function showActivityStatsModal() {
    if (!App.currentBaby) {
        showToast('请先选择宝宝');
        return;
    }

    api(`/api/activities/stats?baby_id=${App.currentBaby}`).then(res => {
        if (res.success) {
            const stats = res.data;
            const categoryStatsHtml = stats.category_stats.map(c =>
                `<div class="stat-category-item"><span>${escapeHtml(c.category)}</span><strong>${c.count}次</strong></div>`
            ).join('');

            const dailyStatsHtml = stats.daily_stats.map(d => {
                const date = new Date(d.date);
                const dayNames = ['日', '一', '二', '三', '四', '五', '六'];
                return `<div class="daily-stat-item"><span>周${dayNames[date.getDay()]}</span><strong>${d.count}</strong></div>`;
            }).join('');

            const modal = document.createElement('div');
            modal.className = 'modal-overlay';
            modal.id = 'activityStatsModal';
            modal.innerHTML = `
                <div class="modal modal-large">
                    <div class="modal-header">
                        <h2>活动统计</h2>
                        <button class="modal-close" onclick="closeActivityStatsModal()">X</button>
                    </div>
                    <div class="modal-body">
                        <div class="activity-stats-grid">
                            <div class="activity-stat-card">
                                <div class="activity-stat-value">${stats.total_count}</div>
                                <div class="activity-stat-label">总完成次数</div>
                            </div>
                            <div class="activity-stat-card">
                                <div class="activity-stat-value">${stats.today_count}</div>
                                <div class="activity-stat-label">今日完成</div>
                            </div>
                            <div class="activity-stat-card">
                                <div class="activity-stat-value">${stats.week_count}</div>
                                <div class="activity-stat-label">本周完成</div>
                            </div>
                        </div>
                        <div class="activity-detail-section">
                            <h4>分类统计</h4>
                            <div class="category-stats-list">${categoryStatsHtml || '<p class="empty-tip">暂无分类数据</p>'}</div>
                        </div>
                        <div class="activity-detail-section">
                            <h4>最近7天</h4>
                            <div class="daily-stats-list">${dailyStatsHtml}</div>
                        </div>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
        }
    }).catch(err => {
        console.error('加载活动统计失败:', err);
        showToast('加载统计失败');
    });
}

function closeActivityStatsModal() {
    const modal = document.getElementById('activityStatsModal');
    if (modal) modal.remove();
}

function showActivityFavoritesModal() {
    if (!App.currentBaby) {
        showToast('请先选择宝宝');
        return;
    }

    api(`/api/activities/favorites?baby_id=${App.currentBaby}`).then(res => {
        if (res.success) {
            const modal = document.createElement('div');
            modal.className = 'modal-overlay';
            modal.id = 'activityFavoritesModal';
            modal.innerHTML = `
                <div class="modal modal-large">
                    <div class="modal-header">
                        <h2>我的收藏</h2>
                        <button class="modal-close" onclick="closeActivityFavoritesModal()">X</button>
                    </div>
                    <div class="modal-body">
                        ${res.data.length === 0 ? '<p class="empty-tip">暂无收藏的活动</p>' : `
                            <div class="activity-list">
                                ${res.data.map(a => `
                                    <div class="activity-card" onclick="closeActivityFavoritesModal();showActivityDetail(${a.id})">
                                        <div class="activity-icon">${a.icon || ''}</div>
                                        <div class="activity-info">
                                            <div class="activity-title">${escapeHtml(a.title)}</div>
                                            ${a.summary ? `<div class="activity-summary">${escapeHtml(a.summary)}</div>` : ''}
                                            <div class="activity-meta">
                                                ${a.duration ? `<span>${escapeHtml(a.duration)}</span>` : ''}
                                                ${a.difficulty ? `<span>${escapeHtml(a.difficulty)}</span>` : ''}
                                            </div>
                                        </div>
                                    </div>
                                `).join('')}
                            </div>
                        `}
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
        }
    }).catch(err => {
        console.error('加载收藏失败:', err);
        showToast('加载收藏失败');
    });
}

function closeActivityFavoritesModal() {
    const modal = document.getElementById('activityFavoritesModal');
    if (modal) modal.remove();
}

function showParentGuideModal() {
    const baby = App.babies?.find(b => b.id === App.currentBaby);
    if (!baby || !baby.birthday) {
        showToast('请先选择宝宝');
        return;
    }
    const ageMonths = calcAgeMonths(baby.birthday);

    api(`/api/activities/parent-guide?age_months=${ageMonths}`).then(res => {
        if (res.success) {
            const guide = res.data;

            const tipsHtml = guide.tips.map(t => `<li>${escapeHtml(t)}</li>`).join('');
            const warningsHtml = guide.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('');
            const scheduleHtml = Object.entries(guide.daily_schedule).map(([time, activity]) => `
                <div class="schedule-item">
                    <span class="schedule-time">${escapeHtml(time === 'morning' ? '上午' : time === 'afternoon' ? '下午' : '晚上')}</span>
                    <span class="schedule-activity">${escapeHtml(activity)}</span>
                </div>
            `).join('');

            const modal = document.createElement('div');
            modal.className = 'modal-overlay';
            modal.id = 'parentGuideModal';
            modal.innerHTML = `
                <div class="modal modal-large">
                    <div class="modal-header">
                        <h2>家长指导 - ${escapeHtml(guide.stage)}</h2>
                        <button class="modal-close" onclick="closeParentGuideModal()">X</button>
                    </div>
                    <div class="modal-body">
                        <div class="guide-header">
                            <div class="guide-title">${escapeHtml(guide.title)}</div>
                            <div class="guide-description">${escapeHtml(guide.description)}</div>
                        </div>
                        <div class="activity-detail-section">
                            <h4>💡 活动建议</h4>
                            <ol class="guide-tips">${tipsHtml}</ol>
                        </div>
                        <div class="activity-detail-section">
                            <h4>⚠️ 注意事项</h4>
                            <ul class="guide-warnings">${warningsHtml}</ul>
                        </div>
                        <div class="activity-detail-section">
                            <h4>📅 每日活动安排</h4>
                            <div class="guide-schedule">${scheduleHtml}</div>
                        </div>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
        }
    }).catch(err => {
        console.error('加载家长指导失败:', err);
        showToast('加载指导失败');
    });
}

function closeParentGuideModal() {
    const modal = document.getElementById('parentGuideModal');
    if (modal) modal.remove();
}

function updateAgeBadge(ageMonths) {
    const badge = document.getElementById('activitiesAgeBadge');
    if (!badge) return;
    if (ageMonths < 0) {
        badge.textContent = '宝宝还未出生';
        badge.style.display = 'none';
    } else {
        badge.textContent = `适合 ${ageMonths} 个月宝宝`;
        badge.style.display = 'block';
    }
}

function calcAgeMonths(birthDate) {
    const birth = new Date(birthDate);
    const now = new Date();
    const diff = (now - birth) / (1000 * 60 * 60 * 24 * 30.44);
    return Math.floor(diff);
}

function renderActivityList(activities) {
    const container = document.getElementById('activityList');
    if (!container) return;

    if (activities.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="empty-icon"></div><div class="empty-text">暂无匹配的活动</div></div>';
        return;
    }

    container.innerHTML = activities.map(a => `
        <div class="activity-card" onclick="showActivityDetail(${a.id})">
            <div class="activity-icon">${a.icon || ''}</div>
            <div class="activity-info">
                <div class="activity-title">${escapeHtml(a.title)}</div>
                ${a.summary ? `<div class="activity-summary">${escapeHtml(a.summary)}</div>` : ''}
                <div class="activity-meta">
                    ${a.duration ? `<span>${escapeHtml(a.duration)}</span>` : ''}
                    ${a.difficulty ? `<span>${escapeHtml(a.difficulty)}</span>` : ''}
                </div>
            </div>
        </div>
    `).join('');
}

function showActivityDetail(id) {
    api(`/api/activities/${id}`).then(data => {
        if (data.success) {
            // 检查收藏状态
            checkActivityFavorite(id).then(isFavorite => {
                data.data._isFavorite = isFavorite;
                renderActivityModal(data.data);
                showModal('activityModal');
            }).catch(() => {
                renderActivityModal(data.data);
                showModal('activityModal');
            });
        }
    }).catch(err => console.error('加载活动详情失败:', err));
}

function renderActivityModal(activity) {
    const container = document.getElementById('activityModalBody');
    if (!container) return;

    const difficultyClass = activity.difficulty === '中等' ? 'medium' : activity.difficulty === '困难' ? 'hard' : 'easy';
    const isFavorite = activity._isFavorite || false;
    const categoryNames = {
        physical: '体能发展', motor: '精细动作', cognitive: '认知发展',
        social: '社交能力', language: '语言发展', art: '艺术启蒙',
        sensory: '感官探索'
    };
    const categoryName = categoryNames[activity.category] || activity.category;

    container.innerHTML = `
        <div class="activity-detail-header">
            <div class="activity-detail-icon">${activity.icon || ''}</div>
            <div class="activity-detail-title-row">
                <div class="activity-detail-title">${escapeHtml(activity.title)}</div>
                <button class="activity-favorite-btn ${isFavorite ? 'favorited' : ''}" id="activityFavoriteBtn" onclick="toggleActivityFavorite(${activity.id})">
                    ${isFavorite ? '★ 已收藏' : '☆ 收藏'}
                </button>
            </div>
        </div>
        <div class="activity-detail-meta">
            ${activity.duration ? `<span>⏱ ${escapeHtml(activity.duration)}</span>` : ''}
            ${activity.difficulty ? `<span class="activity-difficulty difficulty-${difficultyClass}">${escapeHtml(activity.difficulty)}</span>` : ''}
            <span>${activity.min_age_months}-${activity.max_age_months}个月</span>
            <span class="activity-category-tag">${escapeHtml(categoryName)}</span>
        </div>
        ${activity.summary ? `<div class="activity-detail-section"><h4>活动介绍</h4><p>${escapeHtml(activity.summary)}</p></div>` : ''}
        <div class="activity-detail-section">
            <h4>所需材料</h4>
            <div class="activity-materials">
                ${(activity.materials_list && activity.materials_list.length > 0)
                    ? activity.materials_list.map(m => `<span class="material-tag">${escapeHtml(m)}</span>`).join('')
                    : `<span class="material-tag">无需特殊材料</span>`
                }
            </div>
        </div>
        <div class="activity-detail-section">
            <h4>活动步骤</h4>
            <ol class="activity-steps">
                ${(activity.steps_list || []).map(s => `<li>${escapeHtml(s)}</li>`).join('')}
            </ol>
        </div>
        <div class="activity-detail-section">
            <h4>发展益处</h4>
            <div class="activity-benefits">
                ${(activity.benefits_list || []).map(b => `<span class="benefit-tag">${escapeHtml(b)}</span>`).join('')}
            </div>
        </div>
        ${activity.tags_list && activity.tags_list.length > 0 ? `
            <div class="activity-detail-section">
                <h4>相关标签</h4>
                <div class="activity-tags">
                    ${activity.tags_list.map(t => `<span class="activity-tag-item">${escapeHtml(t)}</span>`).join('')}
                </div>
            </div>
        ` : ''}
        ${activity.tips ? `<div class="activity-detail-section"><h4>贴心提示</h4><p>${escapeHtml(activity.tips)}</p></div>` : ''}
        <div class="activity-detail-actions">
            <button class="btn btn-primary" onclick="completeActivity(${activity.id})">
                ✓ 完成活动
            </button>
            <button class="btn btn-secondary" onclick="viewActivityLogs(${activity.id})">
                📊 查看记录
            </button>
        </div>
    `;
}

// ==================== 活动收藏功能 ====================

function toggleActivityFavorite(activityId) {
    if (!App.currentBaby) {
        showToast('请先选择宝宝');
        return;
    }

    const btn = document.getElementById('activityFavoriteBtn');
    const isFavorited = btn && btn.classList.contains('favorited');

    const url = '/api/activities/favorite';
    const method = isFavorited ? 'DELETE' : 'POST';
    const data = { baby_id: App.currentBaby, activity_id: activityId };

    api(url, method, isFavorited ? null : data).then(res => {
        if (res.success) {
            showToast(isFavorited ? '已取消收藏' : '收藏成功');
            if (btn) {
                btn.classList.toggle('favorited');
                btn.innerHTML = isFavorited ? '☆ 收藏' : '★ 已收藏';
            }
        } else {
            showToast(res.message || '操作失败');
        }
    }).catch(err => {
        console.error('收藏操作失败:', err);
        showToast('操作失败，请重试');
    });
}

function checkActivityFavorite(activityId) {
    if (!App.currentBaby) return Promise.resolve(false);
    return api(`/api/activities/favorite/check?baby_id=${App.currentBaby}&activity_id=${activityId}`)
        .then(res => res.success && res.data.is_favorite)
        .catch(() => false);
}

// ==================== 活动完成记录 ====================

function completeActivity(activityId) {
    if (!App.currentBaby) {
        showToast('请先选择宝宝');
        return;
    }

    // 显示完成记录弹窗
    showCompleteActivityModal(activityId);
}

function showCompleteActivityModal(activityId) {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.id = 'completeActivityModal';
    modal.innerHTML = `
        <div class="modal">
            <div class="modal-header">
                <h2>完成活动记录</h2>
                <button class="modal-close" onclick="closeCompleteActivityModal()">X</button>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label>活动时长（分钟）</label>
                    <input type="number" id="activityDuration" class="form-input" placeholder="输入活动时长" min="1" max="120" value="10">
                </div>
                <div class="form-group">
                    <label>宝宝心情</label>
                    <div class="mood-selector">
                        <button type="button" class="mood-btn selected" data-mood="happy">😊 开心</button>
                        <button type="button" class="mood-btn" data-mood="calm">😌 平静</button>
                        <button type="button" class="mood-btn" data-mood="excited">🤩 兴奋</button>
                        <button type="button" class="mood-btn" data-mood="tired">😴 疲倦</button>
                    </div>
                </div>
                <div class="form-group">
                    <label>备注（可选）</label>
                    <textarea id="activityNote" class="form-input" placeholder="记录活动中的有趣瞬间..." rows="3"></textarea>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="closeCompleteActivityModal()">取消</button>
                <button class="btn btn-primary" onclick="submitActivityLog(${activityId})">保存记录</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    // 绑定心情选择
    modal.querySelectorAll('.mood-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            modal.querySelectorAll('.mood-btn').forEach(b => b.classList.remove('selected'));
            btn.classList.add('selected');
        });
    });
}

function closeCompleteActivityModal() {
    const modal = document.getElementById('completeActivityModal');
    if (modal) modal.remove();
}

function submitActivityLog(activityId) {
    const duration = document.getElementById('activityDuration').value;
    const moodBtn = document.querySelector('#completeActivityModal .mood-btn.selected');
    const mood = moodBtn ? moodBtn.dataset.mood : 'happy';
    const note = document.getElementById('activityNote').value;

    const data = {
        baby_id: App.currentBaby,
        activity_id: activityId,
        duration_minutes: parseInt(duration) || 0,
        mood: mood,
        note: note
    };

    api('/api/activities/log', { method: 'POST', body: JSON.stringify(data) }).then(res => {
        if (res.success) {
            showToast('活动记录已保存！');
            closeCompleteActivityModal();
        } else {
            showToast(res.message || '保存失败');
        }
    }).catch(err => {
        console.error('保存活动记录失败:', err);
        showToast('保存失败，请重试');
    });
}

function viewActivityLogs(activityId) {
    if (!App.currentBaby) return;
    api(`/api/activities/logs?baby_id=${App.currentBaby}&activity_id=${activityId}`).then(res => {
        if (res.success) {
            showActivityLogsModal(activityId, res.data);
        }
    }).catch(err => console.error('加载活动记录失败:', err));
}

function showActivityLogsModal(activityId, logs) {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.id = 'activityLogsModal';
    modal.innerHTML = `
        <div class="modal modal-large">
            <div class="modal-header">
                <h2>活动完成记录</h2>
                <button class="modal-close" onclick="closeActivityLogsModal()">X</button>
            </div>
            <div class="modal-body">
                ${logs.length === 0 ? '<p class="empty-tip">暂无完成记录</p>' : `
                    <div class="activity-logs-list">
                        ${logs.map(log => {
                            const moodEmoji = { happy: '😊', calm: '😌', excited: '🤩', tired: '😴' };
                            const date = new Date(log.completed_at);
                            return `
                                <div class="activity-log-item">
                                    <div class="log-header">
                                        <span class="log-mood">${moodEmoji[log.mood] || '😊'}</span>
                                        <span class="log-date">${date.toLocaleDateString('zh-CN')} ${date.toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'})}</span>
                                        <span class="log-duration">${log.duration_minutes}分钟</span>
                                    </div>
                                    ${log.note ? `<div class="log-note">${escapeHtml(log.note)}</div>` : ''}
                                </div>
                            `;
                        }).join('')}
                    </div>
                `}
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

function closeActivityLogsModal() {
    const modal = document.getElementById('activityLogsModal');
    if (modal) modal.remove();
}

function closeActivityModal() {
    hideModal('activityModal');
}

// ==================== 出牙追踪 ====================

function initTeethPage() {
    document.getElementById('addTeethBtn')?.addEventListener('click', showAddTeethModal);
    renderTeethChart();
}

function renderTeethChart() {
    const eruptedCodes = (App.teethRecords || []).map(r => r.tooth_code);

    Object.keys(TEETH_DATA).forEach(quadrant => {
        const container = document.getElementById('teeth' + quadrant.charAt(0).toUpperCase() + quadrant.slice(1));
        if (!container) return;
        container.innerHTML = TEETH_DATA[quadrant].map(t => {
            const isErupted = eruptedCodes.includes(t.code);
            return `<div class="tooth-item tooth-type-${t.type} ${isErupted ? 'erupted' : ''}" 
                        onclick="toggleTooth('${t.code}', '${t.name}')" title="${t.name} (${t.code})">
                <span class="tooth-code">${t.code}</span>
            </div>`;
        }).join('');
    });
}

function toggleTooth(code, name) {
    const existing = (App.teethRecords || []).find(r => r.tooth_code === code);
    if (existing) {
        if (confirm(`确定要删除 ${name}(${code}) 的出牙记录吗？`)) {
            api(`/api/babies/${App.currentBaby}/teeth/${existing.id}`, { method: 'DELETE' }).then(() => {
                App.teethRecords = App.teethRecords.filter(r => r.id !== existing.id);
                renderTeethChart();
                renderTeethList();
            });
        }
    } else {
        const today = getToday();
        const date = prompt(`输入 ${name}(${code}) 的出牙日期`, today);
        if (date) {
            api(`/api/babies/${App.currentBaby}/teeth`, {
                method: 'POST',
                body: JSON.stringify({
                    tooth_code: code,
                    tooth_name: name,
                    erupt_date: date,
                    note: ''
                })
            }).then(res => {
                if (res.success) {
                    App.teethRecords = App.teethRecords || [];
                    App.teethRecords.push({ id: Date.now(), tooth_code: code, tooth_name: name, erupt_date: date });
                    renderTeethChart();
                    renderTeethList();
                }
            });
        }
    }
}

function showAddTeethModal() {
    document.querySelector('.teeth-chart-container')?.scrollIntoView({ behavior: 'smooth' });
}

function loadTeethRecords() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/teeth`).then(res => {
        if (res.success) {
            App.teethRecords = res.data;
            renderTeethChart();
            renderTeethList();
        }
    });
}

function renderTeethList() {
    const container = document.getElementById('teethList');
    if (!container) return;
    const records = App.teethRecords || [];

    if (records.length === 0) {
        container.innerHTML = '<p class="empty-tip">暂无出牙记录，点击上方牙位图记录出牙</p>';
        return;
    }

    const typeIcons = { incisor: '', canine: '', molar: '' };
    container.innerHTML = records.map(r => {
        const tType = TEETH_DATA.upperRight.find(t => t.code === r.tooth_code)?.type ||
                      TEETH_DATA.upperLeft.find(t => t.code === r.tooth_code)?.type ||
                      TEETH_DATA.lowerRight.find(t => t.code === r.tooth_code)?.type ||
                      TEETH_DATA.lowerLeft.find(t => t.code === r.tooth_code)?.type || 'incisor';
        return `
        <div class="teeth-item-card">
            <div class="teeth-item-icon">${typeIcons[tType] || ''}</div>
            <div class="teeth-item-info">
                <div class="teeth-item-title">${escapeHtml(r.tooth_name)} (${escapeHtml(r.tooth_code)})</div>
                <div class="teeth-item-date">出牙日期: ${escapeHtml(r.erupt_date)}</div>
            </div>
            <button class="teeth-delete" onclick="deleteTeethRecord(${r.id})">X</button>
        </div>
    `}).join('');
}

function deleteTeethRecord(id) {
    if (!confirm('确定要删除这条出牙记录吗？')) return;
    api(`/api/babies/${App.currentBaby}/teeth/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            App.teethRecords = App.teethRecords.filter(r => r.id !== id);
            renderTeethChart();
            renderTeethList();
        }
    });
}

// ==================== 健康记录 ====================
function initHealthPage() {
    document.getElementById('addHealthBtn')?.addEventListener('click', showAddHealthModal);
    document.querySelectorAll('.health-cat-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.health-cat-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadHealthRecords(btn.dataset.cat);
        });
    });
}

function loadHealthRecords(category = 'all') {
    if (!App.currentBaby) return;
    let url = `/api/babies/${App.currentBaby}/health-records`;
    if (category && category !== 'all') url += `?type=${category}`;

    api(url).then(res => {
        if (res.success) {
            App.healthRecords = res.data;
            renderHealthList(res.data);
        }
    });
}

function renderHealthList(records) {
    const container = document.getElementById('healthList');
    if (!container) return;

    if (records.length === 0) {
        container.innerHTML = '<p class="empty-tip">暂无健康记录</p>';
        return;
    }

    const typeNames = { fever: '发烧', doctor: '就医', medication: '用药' };
    container.innerHTML = records.map(r => {
        let detailHtml = '';
        if (r.record_type === 'fever' && r.temperature) {
            detailHtml = `<div class="health-item-temp">${escapeHtml(r.temperature)}°C</div>`;
        }
        if (r.symptom) detailHtml += `<div class="health-item-detail">症状: ${escapeHtml(r.symptom)}</div>`;
        if (r.diagnosis) detailHtml += `<div class="health-item-detail">诊断: ${escapeHtml(r.diagnosis)}</div>`;
        if (r.medication) detailHtml += `<div class="health-item-detail">用药: ${escapeHtml(r.medication)}</div>`;
        if (r.doctor) detailHtml += `<div class="health-item-detail">医生: ${escapeHtml(r.doctor)}</div>`;
        if (r.note) detailHtml += `<div class="health-item-detail">备注: ${escapeHtml(r.note)}</div>`;

        return `
            <div class="health-item-card">
                <div class="health-item-header">
                    <span class="health-item-type ${r.record_type}">${typeNames[r.record_type] || r.record_type}</span>
                    <span class="health-item-date">${escapeHtml(r.record_date)}</span>
                </div>
                ${detailHtml}
                <button class="health-delete" onclick="deleteHealthRecord(${r.id})">X</button>
            </div>
        `;
    }).join('');
}

function showAddHealthModal() {
    const today = getToday();
    const formHtml = `
        <div class="form-group">
            <label>记录类型</label>
            <select id="healthType">
                <option value="fever"> 发烧</option>
                <option value="doctor"> 就医</option>
                <option value="medication"> 用药</option>
            </select>
        </div>
        <div class="form-group">
            <label>日期</label>
            <input type="date" id="healthDate" value="${today}">
        </div>
        <div class="form-group" id="healthTempGroup">
            <label>体温 (°C)</label>
            <input type="number" id="healthTemp" step="0.1" placeholder="36.5">
        </div>
        <div class="form-group">
            <label>症状</label>
            <input type="text" id="healthSymptom" placeholder="如: 咳嗽、流涕">
        </div>
        <div class="form-group">
            <label>诊断</label>
            <input type="text" id="healthDiagnosis" placeholder="医生诊断结果">
        </div>
        <div class="form-group">
            <label>用药</label>
            <input type="text" id="healthMedication" placeholder="药物名称和剂量">
        </div>
        <div class="form-group">
            <label>医生</label>
            <input type="text" id="healthDoctor" placeholder="医生姓名/医院">
        </div>
        <div class="form-group">
            <label>备注</label>
            <textarea id="healthNote" rows="2" placeholder="其他备注"></textarea>
        </div>
    `;

    showModal('healthModal');
    const body = document.getElementById('healthModalBody');
    if (body) {
        body.innerHTML = formHtml;
        document.getElementById('healthType')?.addEventListener('change', (e) => {
            const tempGroup = document.getElementById('healthTempGroup');
            if (tempGroup) tempGroup.style.display = e.target.value === 'fever' ? 'block' : 'none';
        });
    }
}

function submitHealthRecord() {
    const data = {
        record_type: document.getElementById('healthType')?.value || 'fever',
        record_date: document.getElementById('healthDate')?.value,
        temperature: parseFloat(document.getElementById('healthTemp')?.value) || null,
        symptom: document.getElementById('healthSymptom')?.value || '',
        diagnosis: document.getElementById('healthDiagnosis')?.value || '',
        medication: document.getElementById('healthMedication')?.value || '',
        doctor: document.getElementById('healthDoctor')?.value || '',
        note: document.getElementById('healthNote')?.value || '',
    };

    api(`/api/babies/${App.currentBaby}/health-records`, { method: 'POST', body: JSON.stringify(data) }).then(res => {
        if (res.success) {
            hideModal('healthModal');
            loadHealthRecords();
        }
    });
}

function deleteHealthRecord(id) {
    if (!confirm('确定要删除这条健康记录吗？')) return;
    api(`/api/babies/${App.currentBaby}/health-records/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            const activeCat = document.querySelector('.health-cat-btn.active')?.dataset.cat || 'all';
            loadHealthRecords(activeCat);
        }
    });
}

// ==================== 育儿健康档案模块 ====================
let _healthTabInitialized = false;
let _currentCheckupRecords = [];
let _currentIndicators = [];
let _currentReminders = [];
let _editingCheckupId = null;
let _editingReminderId = null;

function initHealthArchivePage() {
    if (_healthTabInitialized) return;
    _healthTabInitialized = true;

    // Tab 切换
    document.querySelectorAll('.health-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.health-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.health-tab-content').forEach(c => c.classList.remove('active'));
            tab.classList.add('active');
            const tabName = tab.dataset.tab;
            document.getElementById(`tab-${tabName}`)?.classList.add('active');
            loadHealthTabData(tabName);
        });
    });

    // 体检 Tab
    document.getElementById('addCheckupBtn')?.addEventListener('click', () => showCheckupForm());
    document.getElementById('checkupTypeFilter')?.addEventListener('change', loadCheckupRecords);

    // 生长曲线 Tab - 添加记录按钮
    document.getElementById('addGrowthBtnHealth')?.addEventListener('click', () => showRecordModal('growth'));

    // 生长曲线 Tab
    document.querySelectorAll('.growth-metric-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.growth-metric-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadGrowthChart(btn.dataset.metric);
        });
    });

    // 指标 Tab
    document.getElementById('indicatorNameFilter')?.addEventListener('change', loadIndicators);

    // 提醒 Tab
    document.getElementById('addReminderBtn')?.addEventListener('click', () => showReminderForm());
    document.getElementById('showCompletedReminders')?.addEventListener('change', loadReminders);

    // 疫苗 Tab
    initVaccineTab();

    // 里程碑 Tab
    initMilestoneTab();

    // 筛查 Tab
    initScreeningTab();

    // 过敏 Tab
    initAllergyTab();

    // 喂养摘要 Tab
    initFeedingTab();

    // 加载概览数据（默认 Tab）
    if (typeof loadOverview === 'function') loadOverview();
}

/** 切到健康档案的某个 Tab（外部跳转用）。视图先显示再加载数据，
 *  和健康档案内部的 Tab 点击走同一条路径，避免两套切法行为不一致。 */
function activateHealthTab(tab) {
    const btn = document.querySelector('.health-tab[data-tab="' + tab + '"]');
    if (!btn) return false;
    btn.click();
    return true;
}

function loadHealthTabData(tab) {
    if (!App.currentBaby) return;
    switch (tab) {
        case 'overview': loadOverview(); break;
        case 'checkup': loadCheckupRecords(); break;
        case 'temperature': loadTemperaturePage(); break;
        case 'vaccine':
            // 列表沿用健康档案自己的（配套增删改流程），
            // 统计条来自原「疫苗追踪」独立页，渲染到 #vaccinesStats
            loadVaccineRecords();
            loadVaccineStats();
            break;
        case 'allergy':
            loadAllergies();       // 过敏史
            loadAllergyTests();    // 辅食过敏测试（原独立页）
            break;
        case 'reminders':
            loadReminders();       // 健康提醒
            loadMedReminders();    // 用药提醒（原独立页，渲染 #activeRemindersList / #allRemindersList / #medReminderStats）
            break;
        case 'milestone': loadMilestones(); break;
        case 'screening': loadScreenings(); break;
        case 'growth': loadGrowthChart('weight'); break;
        case 'indicators': loadIndicators(); break;
        case 'feeding': loadFeedingSummary(); break;
    }
}

// --- 概览 ---
function loadOverview() {
    if (!App.currentBaby) return;
    // 加载综合概览数据
    api(`/api/health/summary/${App.currentBaby}`).then(res => {
        if (res.success) {
            const s = res.summary;
            document.getElementById('ovCheckupCount').textContent = s.checkup_count || 0;
            document.getElementById('ovLatestHeight').textContent = s.latestHeight || s.latest_height || '-';
            document.getElementById('ovLatestWeight').textContent = s.latestWeight || s.latest_weight || '-';
            document.getElementById('ovPendingReminders').textContent = s.pending_reminders || 0;

            // 更新扩展概览卡片
            const ovVaccine = document.getElementById('ovVaccine');
            if (ovVaccine) ovVaccine.textContent = `${s.vaccine_completed || 0}/${s.vaccine_total || 0}`;
            const ovMilestone = document.getElementById('ovMilestone');
            if (ovMilestone) ovMilestone.textContent = `${s.milestone_achieved || 0}/${s.milestone_total || 0}`;
            const ovScreening = document.getElementById('ovScreening');
            if (ovScreening) ovScreening.textContent = s.screening_count || 0;
            const ovAllergy = document.getElementById('ovAllergy');
            if (ovAllergy) ovAllergy.textContent = s.allergy_count || 0;
            const ovAsq = document.getElementById('ovAsq');
            if (ovAsq) ovAsq.textContent = s.asq_count || 0;
            const ovFeeding = document.getElementById('ovFeeding');
            if (ovFeeding) ovFeeding.textContent = s.feeding_months || 0;

            // 综合最近记录
            const container = document.getElementById('ovRecentList');
            if (container) {
                const recentItems = [];
                if (s.latest_record_date) recentItems.push({ type: '体检', title: `最新身高 ${s.latest_height || '-'}cm / 体重 ${s.latest_weight || '-'}kg`, date: s.latest_record_date });
                if (s.latest_vaccine_date) recentItems.push({ type: '疫苗', title: s.latest_vaccine_name || '疫苗接种记录', date: s.latest_vaccine_date });
                if (s.latest_asq_date) recentItems.push({ type: 'ASQ筛查', title: `结果: ${s.latest_asq_result || '-'}`, date: s.latest_asq_date });
                if (s.screening_abnormal > 0) recentItems.push({ type: '筛查异常', title: `${s.screening_abnormal}项异常需关注`, date: '' });

                if (recentItems.length === 0) {
                    container.innerHTML = '<p class="empty-tip">暂无记录</p>';
                } else {
                    const typeLabels = { 体检: '', 疫苗: '', ASQ筛查: '',筛查异常: '[!]' };
                    container.innerHTML = recentItems.map(r => `
                        <div class="health-recent-item">
                            <span class="health-recent-type">${typeLabels[r.type] || ''} ${escapeHtml(r.type)}</span>
                            <span class="health-recent-title">${escapeHtml(r.title)}</span>
                            <span class="health-recent-date">${escapeHtml(r.date)}</span>
                        </div>
                    `).join('');
                }
            }
        }
    });
}

// --- 体检记录 ---
function loadCheckupRecords() {
    if (!App.currentBaby) return;
    const typeFilter = document.getElementById('checkupTypeFilter')?.value || '';
    let url = `/api/health/records/${App.currentBaby}`;
    if (typeFilter) url += `?type=${typeFilter}`;

    api(url).then(res => {
        if (res.success) {
            _currentCheckupRecords = res.records;
            renderCheckupList(res.records);
        }
    });
}

function renderCheckupList(records) {
    const container = document.getElementById('checkupList');
    if (!container) return;
    if (records.length === 0) {
        container.innerHTML = '<p class="empty-tip">暂无体检记录</p>';
        return;
    }

    const typeLabels = { routine: '常规体检', vaccine: '疫苗接种', dental: '口腔检查', eye: '视力检查', blood: '血常规', other: '其他' };
    const typeIcons = { routine: '', vaccine: '', dental: '', eye: '', blood: '', other: '' };

    container.innerHTML = records.map(r => {
        const indicators = r.indicators || [];
        const abnormalCount = indicators.filter(i => i.result_status === 'high' || i.result_status === 'low' || i.result_status === 'abnormal').length;
        return `
            <div class="checkup-card" onclick="showCheckupDetail(${r.id})">
                <div class="checkup-card-header">
                    <span class="checkup-card-icon">${typeIcons[r.record_type] || ''}</span>
                    <div class="checkup-card-info">
                        <div class="checkup-card-title">${escapeHtml(r.title)}</div>
                        <div class="checkup-card-meta">
                            <span>${typeLabels[r.record_type] || r.record_type || '体检'}</span>
                            <span>${escapeHtml(r.record_date)}</span>
                            ${r.hospital ? `<span>${escapeHtml(r.hospital)}</span>` : ''}
                            ${r.age_months != null ? `<span>${r.age_months}月龄</span>` : ''}
                        </div>
                    </div>
                    <div class="checkup-card-actions" onclick="event.stopPropagation()">
                        ${abnormalCount > 0 ? `<span class="checkup-abnormal-badge">${abnormalCount}项异常</span>` : ''}
                        <button class="btn btn-sm btn-secondary" onclick="editCheckupRecord(${r.id})">编辑</button>
                        <button class="btn btn-sm btn-danger" onclick="deleteCheckupRecord(${r.id})">删除</button>
                    </div>
                </div>
                ${r.height || r.weight || r.head_circumference ? `
                    <div class="checkup-card-measurements">
                        ${r.height ? `<span>身高 ${r.height}cm</span>` : ''}
                        ${r.weight ? `<span>体重 ${r.weight}kg</span>` : ''}
                        ${r.head_circumference ? `<span>头围 ${r.head_circumference}cm</span>` : ''}
                        ${r.bmi ? `<span>BMI ${r.bmi}</span>` : ''}
                    </div>
                ` : ''}
                ${r.diagnosis ? `<div class="checkup-card-diagnosis">${r.diagnosis}</div>` : ''}
            </div>
        `;
    }).join('');
}

function showCheckupForm(record = null) {
    _editingCheckupId = record ? record.id : null;
    const title = document.getElementById('checkupFormTitle');
    if (title) title.textContent = record ? '编辑体检记录' : '添加体检记录';

    const today = getToday();
    const typeOptions = [
        ['routine', '常规体检'], ['vaccine', '疫苗接种'], ['dental', '口腔检查'],
        ['eye', '视力检查'], ['blood', '血常规'], ['other', '其他']
    ];

    const formHtml = `
        <div class="form-row">
            <div class="form-group">
                <label>记录日期 *</label>
                <input type="date" id="formCheckupDate" value="${record?.record_date || today}">
            </div>
            <div class="form-group">
                <label>类型</label>
                <select id="formCheckupType">
                    ${typeOptions.map(v => `<option value="${v[0]}" ${record?.record_type === v[0] ? 'selected' : ''}>${v[1]}</option>`).join('')}
                </select>
            </div>
        </div>
        <div class="form-group">
            <label>标题 *</label>
            <input type="text" id="formCheckupTitle" value="${escapeHtml(record?.title || '')}" placeholder="如: 3月龄体检">
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>医院/机构</label>
                <input type="text" id="formCheckupHospital" value="${escapeHtml(record?.hospital || '')}" placeholder="医院或体检中心">
            </div>
            <div class="form-group">
                <label>医生</label>
                <input type="text" id="formCheckupDoctor" value="${escapeHtml(record?.doctor || '')}" placeholder="医生姓名">
            </div>
        </div>
        <h4 class="form-section-title">体格测量</h4>
        <div class="form-row">
            <div class="form-group">
                <label>身高 (cm)</label>
                <input type="number" id="formCheckupHeight" step="0.1" value="${record?.height || ''}" placeholder="62.5">
            </div>
            <div class="form-group">
                <label>体重 (kg)</label>
                <input type="number" id="formCheckupWeight" step="0.01" value="${record?.weight || ''}" placeholder="6.25">
            </div>
            <div class="form-group">
                <label>头围 (cm)</label>
                <input type="number" id="formCheckupHead" step="0.1" value="${record?.head_circumference || ''}" placeholder="40.2">
            </div>
        </div>
        <h4 class="form-section-title">检查结果</h4>
        <div class="form-row">
            <div class="form-group">
                <label>心脏</label>
                <input type="text" id="formCheckupHeart" value="${escapeHtml(record?.heart_result || '')}" placeholder="如: 未闻及杂音">
            </div>
            <div class="form-group">
                <label>肺部</label>
                <input type="text" id="formCheckupLung" value="${escapeHtml(record?.lung_result || '')}" placeholder="如: 呼吸音清">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>腹部</label>
                <input type="text" id="formCheckupAbdomen" value="${escapeHtml(record?.abdomen_result || '')}" placeholder="如: 软, 无压痛">
            </div>
            <div class="form-group">
                <label>皮肤</label>
                <input type="text" id="formCheckupSkin" value="${escapeHtml(record?.skin_result || '')}" placeholder="如: 无黄染">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>骨骼</label>
                <input type="text" id="formCheckupBone" value="${escapeHtml(record?.bone_result || '')}" placeholder="如: 无畸形">
            </div>
            <div class="form-group">
                <label>听力</label>
                <input type="text" id="formCheckupHearing" value="${escapeHtml(record?.hearing_result || '')}" placeholder="如: 通过">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>视力</label>
                <input type="text" id="formCheckupVision" value="${escapeHtml(record?.vision_result || '')}" placeholder="如: 追视好">
            </div>
            <div class="form-group">
                <label>血常规</label>
                <input type="text" id="formCheckupBlood" value="${escapeHtml(record?.blood_result || '')}" placeholder="如: 正常">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>尿常规</label>
                <input type="text" id="formCheckupUrine" value="${escapeHtml(record?.urine_result || '')}" placeholder="如: 正常">
            </div>
            <div class="form-group">
                <label>其他检查</label>
                <input type="text" id="formCheckupOther" value="${escapeHtml(record?.other_exam || '')}" placeholder="其他检查结果">
            </div>
        </div>
        <div class="form-group">
            <label>诊断结论</label>
            <textarea id="formCheckupDiagnosis" rows="2" placeholder="医生诊断结论">${escapeHtml(record?.diagnosis || '')}</textarea>
        </div>
        <div class="form-group">
            <label>医嘱建议</label>
            <textarea id="formCheckupAdvice" rows="2" placeholder="医生建议">${escapeHtml(record?.advice || '')}</textarea>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>下次随访日期</label>
                <input type="date" id="formCheckupNextVisit" value="${record?.next_visit_date || ''}">
            </div>
        </div>
        <div class="form-group">
            <label>备注</label>
            <textarea id="formCheckupNote" rows="2" placeholder="其他备注">${escapeHtml(record?.note || '')}</textarea>
        </div>
    `;

    const body = document.getElementById('checkupFormBody');
    if (body) body.innerHTML = formHtml;
    // checkupFormModal 被疫苗/里程碑/筛查/过敏/喂养五处复用，它们用 .onclick 劫持提交按钮，
    // 且仅在“提交成功”时才还原。用户中途关闭弹窗会让 onclick 残留成上一个功能的提交函数，
    // 导致下次打开体检表单点保存时把数据提交到错误接口。故每次打开都强制复位标题与提交按钮。
    const _cfTitle = document.querySelector('#checkupFormModal .modal-header h2');
    if (_cfTitle) _cfTitle.textContent = record ? '编辑体检记录' : '添加体检记录';
    const _cfBtn = document.querySelector('#checkupFormModal .modal-footer .btn-primary');
    if (_cfBtn) _cfBtn.onclick = submitCheckupRecord;
    showModal('checkupFormModal');
}

function submitCheckupRecord() {
    const data = {
        record_date: document.getElementById('formCheckupDate')?.value,
        record_type: document.getElementById('formCheckupType')?.value || 'routine',
        title: document.getElementById('formCheckupTitle')?.value?.trim(),
        hospital: document.getElementById('formCheckupHospital')?.value?.trim() || '',
        doctor: document.getElementById('formCheckupDoctor')?.value?.trim() || '',
        height: parseFloat(document.getElementById('formCheckupHeight')?.value) || null,
        weight: parseFloat(document.getElementById('formCheckupWeight')?.value) || null,
        head_circumference: parseFloat(document.getElementById('formCheckupHead')?.value) || null,
        heart_result: document.getElementById('formCheckupHeart')?.value?.trim() || '',
        lung_result: document.getElementById('formCheckupLung')?.value?.trim() || '',
        abdomen_result: document.getElementById('formCheckupAbdomen')?.value?.trim() || '',
        skin_result: document.getElementById('formCheckupSkin')?.value?.trim() || '',
        bone_result: document.getElementById('formCheckupBone')?.value?.trim() || '',
        hearing_result: document.getElementById('formCheckupHearing')?.value?.trim() || '',
        vision_result: document.getElementById('formCheckupVision')?.value?.trim() || '',
        blood_result: document.getElementById('formCheckupBlood')?.value?.trim() || '',
        urine_result: document.getElementById('formCheckupUrine')?.value?.trim() || '',
        other_exam: document.getElementById('formCheckupOther')?.value?.trim() || '',
        diagnosis: document.getElementById('formCheckupDiagnosis')?.value?.trim() || '',
        advice: document.getElementById('formCheckupAdvice')?.value?.trim() || '',
        next_visit_date: document.getElementById('formCheckupNextVisit')?.value || null,
        note: document.getElementById('formCheckupNote')?.value?.trim() || '',
    };

    if (!data.record_date || !data.title) {
        alert('请填写日期和标题');
        return;
    }

    const url = _editingCheckupId
        ? `/api/health/records/detail/${_editingCheckupId}`
        : `/api/health/records/${App.currentBaby}`;
    const method = _editingCheckupId ? 'PUT' : 'POST';

    api(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            hideModal('checkupFormModal');
            _editingCheckupId = null;
            loadCheckupRecords();
            loadOverview();
        } else {
            alert(res.message || '保存失败');
        }
    });
}

function editCheckupRecord(id) {
    const record = _currentCheckupRecords.find(r => r.id === id);
    if (record) {
        // 获取完整详情（含 indicators）
        api(`/api/health/records/detail/${id}`).then(res => {
            if (res.success) showCheckupForm(res.record);
        });
    }
}

function deleteCheckupRecord(id) {
    if (!confirm('确定要删除这条体检记录吗？关联的指标数据也会被删除。')) return;
    api(`/api/health/records/detail/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast.success('已删除');
            loadCheckupRecords();
            loadOverview();
        } else {
            showToast.error(res.message || '删除失败');
        }
    }).catch(() => {
        showToast.error('网络错误');
    });
}

function showCheckupDetail(id) {
    api(`/api/health/records/detail/${id}`).then(res => {
        if (!res.success) return;
        const r = res.record;
        const typeLabels = { routine: '常规体检', vaccine: '疫苗接种', dental: '口腔检查', eye: '视力检查', blood: '血常规', other: '其他' };

        let indicatorsHtml = '';
        if (r.indicators && r.indicators.length > 0) {
            indicatorsHtml = `
                <h4>检验指标</h4>
                <table class="health-indicator-table">
                    <thead><tr><th>项目</th><th>结果</th><th>单位</th><th>参考范围</th><th>状态</th></tr></thead>
                    <tbody>
                        ${r.indicators.map(i => {
                            const statusClass = i.result_status === 'high' ? 'indicator-high' :
                                i.result_status === 'low' ? 'indicator-low' :
                                i.result_status === 'abnormal' ? 'indicator-abnormal' : 'indicator-normal';
                            const statusText = i.result_status === 'high' ? '↑ 偏高' :
                                i.result_status === 'low' ? '↓ 偏低' :
                                i.result_status === 'abnormal' ? '异常' : '正常';
                            return `<tr>
                                <td>${escapeHtml(i.indicator_name)}</td>
                                <td>${escapeHtml(i.value)}</td>
                                <td>${escapeHtml(i.unit || '-')}</td>
                                <td>${escapeHtml(i.reference_text || (i.reference_low && i.reference_high ? `${i.reference_low}-${i.reference_high}` : '-'))}</td>
                                <td class="${statusClass}">${statusText}</td>
                            </tr>`;
                        }).join('')}
                    </tbody>
                </table>
            `;
        }

        const detailHtml = `
            <div class="checkup-detail">
                <div class="checkup-detail-header">
                    <span class="checkup-detail-type">${typeLabels[r.record_type] || r.record_type || '体检'}</span>
                    <span class="checkup-detail-date">${r.record_date}</span>
                    ${r.age_months != null ? `<span>${r.age_months}月龄</span>` : ''}
                </div>
                <h3 class="checkup-detail-title">${escapeHtml(r.title)}</h3>
                ${r.hospital ? `<p><strong>医院:</strong> ${escapeHtml(r.hospital)}</p>` : ''}
                ${r.doctor ? `<p><strong>医生:</strong> ${escapeHtml(r.doctor)}</p>` : ''}

                ${r.height || r.weight || r.head_circumference ? `
                    <h4>体格测量</h4>
                    <div class="checkup-measurements">
                        ${r.height ? `<div class="measurement-item"><span class="measurement-value">${r.height}</span><span class="measurement-unit">cm 身高</span></div>` : ''}
                        ${r.weight ? `<div class="measurement-item"><span class="measurement-value">${r.weight}</span><span class="measurement-unit">kg 体重</span></div>` : ''}
                        ${r.head_circumference ? `<div class="measurement-item"><span class="measurement-value">${r.head_circumference}</span><span class="measurement-unit">cm 头围</span></div>` : ''}
                        ${r.bmi ? `<div class="measurement-item"><span class="measurement-value">${r.bmi}</span><span class="measurement-unit">BMI</span></div>` : ''}
                    </div>
                ` : ''}

                <h4>检查结果</h4>
                <div class="checkup-exam-grid">
                    ${r.heart_result ? `<div><span class="exam-label">心脏:</span> ${escapeHtml(r.heart_result)}</div>` : ''}
                    ${r.lung_result ? `<div><span class="exam-label">肺部:</span> ${escapeHtml(r.lung_result)}</div>` : ''}
                    ${r.abdomen_result ? `<div><span class="exam-label">腹部:</span> ${escapeHtml(r.abdomen_result)}</div>` : ''}
                    ${r.skin_result ? `<div><span class="exam-label">皮肤:</span> ${escapeHtml(r.skin_result)}</div>` : ''}
                    ${r.bone_result ? `<div><span class="exam-label">骨骼:</span> ${escapeHtml(r.bone_result)}</div>` : ''}
                    ${r.hearing_result ? `<div><span class="exam-label">听力:</span> ${escapeHtml(r.hearing_result)}</div>` : ''}
                    ${r.vision_result ? `<div><span class="exam-label">视力:</span> ${escapeHtml(r.vision_result)}</div>` : ''}
                    ${r.blood_result ? `<div><span class="exam-label">血常规:</span> ${escapeHtml(r.blood_result)}</div>` : ''}
                    ${r.urine_result ? `<div><span class="exam-label">尿常规:</span> ${escapeHtml(r.urine_result)}</div>` : ''}
                    ${r.other_exam ? `<div><span class="exam-label">其他:</span> ${escapeHtml(r.other_exam)}</div>` : ''}
                </div>

                ${indicatorsHtml}

                ${r.diagnosis ? `<h4>诊断结论</h4><p>${escapeHtml(r.diagnosis)}</p>` : ''}
                ${r.advice ? `<h4>医嘱建议</h4><p>${escapeHtml(r.advice)}</p>` : ''}
                ${r.next_visit_date ? `<p><strong>下次随访:</strong> ${escapeHtml(r.next_visit_date)}</p>` : ''}
                ${r.note ? `<p><strong>备注:</strong> ${escapeHtml(r.note)}</p>` : ''}
            </div>
        `;

        const body = document.getElementById('checkupDetailBody');
        if (body) body.innerHTML = detailHtml;
        showModal('checkupDetailModal');
    });
}

// --- 生长曲线 ---
let _growthChartInstance = null;

function loadGrowthChart(metric = 'weight') {
    if (!App.currentBaby) return;
    api(`/api/health/trend/${App.currentBaby}?metric=${metric}`).then(res => {
        if (!res.success) return;
        renderGrowthChart(metric, res.data);
        renderGrowthTable(metric, res.data);
    });
}

function renderGrowthChart(metric, data) {
    const canvas = document.getElementById('growthChart');
    if (!canvas || typeof CanvasRenderingContext2D === 'undefined') return;
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;

    // 清空画布
    ctx.clearRect(0, 0, width, height);

    if (data.length === 0) {
        ctx.fillStyle = '#999';
        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('暂无数据，添加体检记录后可查看生长曲线', width / 2, height / 2);
        return;
    }

    const metricLabels = { weight: '体重 (kg)', height: '身高 (cm)', head_circumference: '头围 (cm)', bmi: 'BMI' };
    const padding = { top: 40, right: 30, bottom: 50, left: 60 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const values = data.map(d => d.value);
    const minVal = Math.min(...values) * 0.95;
    const maxVal = Math.max(...values) * 1.05;
    const valRange = maxVal - minVal || 1;

    // 标题
    ctx.fillStyle = '#333';
    ctx.font = 'bold 14px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(`${metricLabels[metric] || metric} 生长曲线`, width / 2, 20);

    // 绘制网格
    ctx.strokeStyle = '#eee';
    ctx.lineWidth = 1;
    const gridLines = 5;
    for (let i = 0; i <= gridLines; i++) {
        const y = padding.top + (chartH / gridLines) * i;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(width - padding.right, y);
        ctx.stroke();

        // Y轴标签
        const val = maxVal - (valRange / gridLines) * i;
        ctx.fillStyle = '#666';
        ctx.font = '11px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(val.toFixed(1), padding.left - 5, y + 4);
    }

    // 绘制折线
    if (data.length > 1) {
        ctx.strokeStyle = '#4A90D9';
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        data.forEach((d, i) => {
            const x = padding.left + (chartW / (data.length - 1)) * i;
            const y = padding.top + chartH - ((d.value - minVal) / valRange) * chartH;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
    }

    // 绘制数据点
    data.forEach((d, i) => {
        const x = padding.left + (chartW / Math.max(data.length - 1, 1)) * i;
        const y = padding.top + chartH - ((d.value - minVal) / valRange) * chartH;

        ctx.fillStyle = '#4A90D9';
        ctx.beginPath();
        ctx.arc(x, y, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.arc(x, y, 2.5, 0, Math.PI * 2);
        ctx.fill();

        // X轴标签（日期）
        ctx.fillStyle = '#666';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        const dateStr = d.date.substring(5); // MM-DD
        ctx.fillText(dateStr, x, height - padding.bottom + 15);

        // 数值标签
        ctx.fillStyle = '#333';
        ctx.font = '10px sans-serif';
        ctx.fillText(d.value.toString(), x, y - 10);
    });
}

function renderGrowthTable(metric, data) {
    const container = document.getElementById('growthTable');
    if (!container) return;
    if (data.length === 0) {
        container.innerHTML = '';
        return;
    }
    const metricLabels = { weight: '体重(kg)', height: '身高(cm)', head_circumference: '头围(cm)', bmi: 'BMI' };

    // 计算变化
    let rows = '';
    data.forEach((d, i) => {
        let changeStr = '-';
        if (i > 0) {
            const change = d.value - data[i - 1].value;
            const sign = change >= 0 ? '+' : '';
            changeStr = `${sign}${change.toFixed(2)}`;
        }
        rows += `<tr><td>${escapeHtml(d.date)}</td><td>${escapeHtml(String(d.value))}</td><td>${escapeHtml(changeStr)}</td></tr>`;
    });

    container.innerHTML = `
        <table class="health-trend-table">
            <thead><tr><th>日期</th><th>${metricLabels[metric] || metric}</th><th>变化</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

// --- 指标追踪 ---
function loadIndicators() {
    if (!App.currentBaby) return;
    const nameFilter = document.getElementById('indicatorNameFilter')?.value || '';
    let url = `/api/health/indicators/${App.currentBaby}`;
    if (nameFilter) url += `?name=${encodeURIComponent(nameFilter)}`;

    api(url).then(res => {
        if (res.success) {
            _currentIndicators = res.indicators;
            renderIndicatorList(res.indicators);
            updateIndicatorNameFilter(res.indicators);
        }
    });
}

function updateIndicatorNameFilter(indicators) {
    const filter = document.getElementById('indicatorNameFilter');
    if (!filter) return;
    const currentVal = filter.value;
    const names = [...new Set(indicators.map(i => i.indicator_name))].sort();
    filter.innerHTML = '<option value="">全部指标</option>' +
        names.map(n => `<option value="${n}" ${currentVal === n ? 'selected' : ''}>${n}</option>`).join('');
}

function renderIndicatorList(indicators) {
    const container = document.getElementById('indicatorList');
    if (!container) return;
    if (indicators.length === 0) {
        container.innerHTML = '<p class="empty-tip">暂无指标数据</p>';
        return;
    }

    // 按指标名称分组
    const groups = {};
    indicators.forEach(i => {
        if (!groups[i.indicator_name]) groups[i.indicator_name] = [];
        groups[i.indicator_name].push(i);
    });

    container.innerHTML = Object.entries(groups).map(([name, items]) => {
        const latest = items[0];
        const statusClass = latest.result_status === 'high' ? 'indicator-high' :
            latest.result_status === 'low' ? 'indicator-low' :
            latest.result_status === 'abnormal' ? 'indicator-abnormal' : 'indicator-normal';
        const statusText = latest.result_status === 'high' ? '↑ 偏高' :
            latest.result_status === 'low' ? '↓ 偏低' :
            latest.result_status === 'abnormal' ? '异常' : '正常';

        return `
            <div class="indicator-group">
                <div class="indicator-group-header">
                    <span class="indicator-name">${escapeHtml(name)}</span>
                    <span class="indicator-latest">${escapeHtml(String(latest.value))} ${escapeHtml(latest.unit || '')}</span>
                    <span class="indicator-status ${statusClass}">${statusText}</span>
                    <span class="indicator-date">${escapeHtml(latest.record_date)}</span>
                </div>
                <div class="indicator-history">
                    ${items.slice(0, 5).map(i => `
                        <span class="indicator-history-item">${escapeHtml(i.record_date)}: ${escapeHtml(String(i.value))} ${escapeHtml(i.unit || '')}</span>
                    `).join('')}
                </div>
            </div>
        `;
    }).join('');
}

// --- 健康提醒 ---
function loadReminders() {
    if (!App.currentBaby) return;
    const showCompleted = document.getElementById('showCompletedReminders')?.checked || false;
    let url = `/api/health/reminders/${App.currentBaby}`;
    if (showCompleted) url += '?completed=true';

    api(url).then(res => {
        if (res.success) {
            _currentReminders = res.reminders;
            renderReminderList(res.reminders);
        }
    });
}

function renderReminderList(reminders) {
    const container = document.getElementById('reminderList');
    if (!container) return;
    if (reminders.length === 0) {
        container.innerHTML = '<p class="empty-tip">暂无提醒</p>';
        return;
    }

    const typeLabels = { vaccine: '疫苗', checkup: '体检', custom: '自定义' };
    const today = getToday();

    container.innerHTML = reminders.map(r => {
        const isOverdue = r.due_date < today && !r.is_completed;
        const isDueSoon = r.due_date <= today && !r.is_completed;
        const statusClass = r.is_completed ? 'reminder-done' : isOverdue ? 'reminder-overdue' : isDueSoon ? 'reminder-due-soon' : 'reminder-normal';

        return `
            <div class="reminder-card ${statusClass}">
                <div class="reminder-check">
                    <input type="checkbox" ${r.is_completed ? 'checked' : ''} onchange="toggleReminderComplete(${r.id}, this.checked)">
                </div>
                <div class="reminder-info">
                    <div class="reminder-title">${escapeHtml(r.title)}</div>
                    ${r.description ? `<div class="reminder-desc">${escapeHtml(r.description)}</div>` : ''}
                    <div class="reminder-meta">
                        <span class="reminder-type">${typeLabels[r.reminder_type] || r.reminder_type}</span>
                        <span class="reminder-due ${isOverdue ? 'overdue' : ''}"> ${escapeHtml(r.due_date)}${isOverdue ? ' (已逾期)' : ''}</span>
                    </div>
                </div>
                <div class="reminder-actions">
                    <button class="btn btn-sm btn-secondary" onclick="editReminder(${r.id})">编辑</button>
                    <button class="btn btn-sm btn-danger" onclick="deleteReminder(${r.id})">删除</button>
                </div>
            </div>
        `;
    }).join('');
}

function showReminderForm(reminder = null) {
    _editingReminderId = reminder ? reminder.id : null;
    const title = document.getElementById('reminderFormTitle');
    if (title) title.textContent = reminder ? '编辑提醒' : '添加提醒';

    const today = getToday();
    const formHtml = `
        <div class="form-group">
            <label>提醒类型</label>
            <select id="formReminderType">
                <option value="vaccine" ${reminder?.reminder_type === 'vaccine' ? 'selected' : ''}> 疫苗</option>
                <option value="checkup" ${reminder?.reminder_type === 'checkup' ? 'selected' : ''}> 体检</option>
                <option value="custom" ${(!reminder || reminder.reminder_type === 'custom') ? 'selected' : ''}> 自定义</option>
            </select>
        </div>
        <div class="form-group">
            <label>标题 *</label>
            <input type="text" id="formReminderTitle" value="${reminder?.title || ''}" placeholder="如: 乙肝疫苗第二针">
        </div>
        <div class="form-group">
            <label>描述</label>
            <textarea id="formReminderDesc" rows="2" placeholder="详细说明">${reminder?.description || ''}</textarea>
        </div>
        <div class="form-group">
            <label>到期日期 *</label>
            <input type="date" id="formReminderDue" value="${reminder?.due_date || today}">
        </div>
        <div class="form-group">
            <label>备注</label>
            <input type="text" id="formReminderNote" value="${reminder?.note || ''}" placeholder="其他备注">
        </div>
    `;

    const body = document.getElementById('reminderFormBody');
    if (body) body.innerHTML = formHtml;
    showModal('reminderFormModal');
}

function submitReminder() {
    const data = {
        reminder_type: document.getElementById('formReminderType')?.value || 'custom',
        title: document.getElementById('formReminderTitle')?.value?.trim(),
        description: document.getElementById('formReminderDesc')?.value?.trim() || '',
        due_date: document.getElementById('formReminderDue')?.value,
        note: document.getElementById('formReminderNote')?.value?.trim() || '',
    };

    if (!data.title || !data.due_date) {
        alert('请填写标题和到期日期');
        return;
    }

    const url = _editingReminderId
        ? `/api/health/reminders/detail/${_editingReminderId}`
        : `/api/health/reminders/${App.currentBaby}`;
    const method = _editingReminderId ? 'PUT' : 'POST';

    api(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            hideModal('reminderFormModal');
            _editingReminderId = null;
            loadReminders();
            loadOverview();
        } else {
            alert(res.message || '保存失败');
        }
    });
}

function editReminder(id) {
    const reminder = _currentReminders.find(r => r.id === id);
    if (reminder) showReminderForm(reminder);
}

function deleteReminder(id) {
    if (!confirm('确定要删除这条提醒吗？')) return;
    api(`/api/health/reminders/detail/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            loadReminders();
            loadOverview();
        }
    });
}

function toggleReminderComplete(id, completed) {
    api(`/api/health/reminders/detail/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_completed: completed })
    }).then(res => {
        if (res.success) {
            loadReminders();
            loadOverview();
        }
    });
}

// ==================== 疫苗管理 ====================
let _vaccineRecords = [];
let _vaccineSchedule = { standard: [], recommended: [] };
let temporaryVaccineEditId = null;

function initVaccineTab() {
    document.getElementById('addVaccineBtn2')?.addEventListener('click', () => showVaccineForm());
    document.getElementById('loadScheduleBtn')?.addEventListener('click', loadVaccineSchedule);
    document.getElementById('vaccineStatusFilter')?.addEventListener('change', loadVaccineRecords);
}

function loadVaccineRecords() {
    if (!App.currentBaby) return;
    const statusFilter = document.getElementById('vaccineStatusFilter')?.value || '';
    // 使用 vaccines.py 蓝图的 API
    api(`/api/babies/${App.currentBaby}/vaccines`).then(res => {
        if (res.success) {
            let vaccines = res.data || [];
            if (statusFilter) {
                vaccines = vaccines.filter(v => v.status === statusFilter);
            }
            _vaccineRecords = vaccines;
            renderVaccineList(vaccines);
        }
    }).catch(() => {
        showToast.error('加载疫苗记录失败');
    });
}

function loadVaccineSchedule() {
    if (!App.currentBaby) return;
    // 获取 vaccine_details 表中的疫苗计划
    api(`/api/health/vaccine/schedule/${App.currentBaby}`).then(res => {
        if (res.success) {
            _vaccineSchedule = res;
            _vaccineSchedule.standard = res.schedule || [];
            document.getElementById('vaccineScheduleSection').style.display = 'block';
            renderVaccineSchedule();
        } else {
            showToast.error(res.message || '加载疫苗计划失败');
        }
    }).catch(() => {
        showToast.error('加载疫苗计划失败');
    });
}

function renderVaccineSchedule() {
    const standardList = document.getElementById('vaccineStandardList');
    const recommendedList = document.getElementById('vaccineRecommendedList');
    if (standardList) {
        standardList.innerHTML = _vaccineSchedule.standard.map(v => {
            const existing = _vaccineRecords.find(r => r.vaccine_name === v.vaccine_name && r.dose_number === v.dose);
            const statusClass = existing ? `vaccine-status-${existing.status}` : 'vaccine-status-pending';
            const statusText = existing ? (existing.status === 'completed' ? ' 已完成' : existing.status === 'delayed' ? '⏰ 延期' : ' 计划中') : ' 计划中';
            return `<div class="vaccine-schedule-item ${statusClass}">
                <div class="vaccine-schedule-info">
                    <span class="vaccine-name">${escapeHtml(v.vaccine_name)}</span>
                    <span class="vaccine-dose">第${v.dose}针</span>
                    <span class="vaccine-age">${escapeHtml(v.scheduled_age_display)}</span>
                </div>
                <div class="vaccine-schedule-date">
                    <span>${escapeHtml(v.scheduled_date)}</span>
                    <span class="vaccine-schedule-status">${statusText}</span>
                </div>
                ${!existing ? `<button class="btn btn-sm btn-primary js-quick-vaccine" data-vname="${escapeHtml(v.vaccine_name)}" data-dose="${v.dose}" data-vdate="${escapeHtml(v.scheduled_date)}" data-vtype="${escapeHtml(v.type)}">记录接种</button>` : ''}
            </div>`;
        }).join('');
    }
    if (recommendedList) {
        recommendedList.innerHTML = _vaccineSchedule.recommended.map(v => {
            const existing = _vaccineRecords.find(r => r.vaccine_name === v.vaccine_name && r.dose_number === v.dose);
            return `<div class="vaccine-schedule-item ${existing ? 'vaccine-status-completed' : 'vaccine-status-pending'}">
                <div class="vaccine-schedule-info">
                    <span class="vaccine-name">${escapeHtml(v.vaccine_name)}</span>
                    <span class="vaccine-dose">第${v.dose}针</span>
                    <span class="vaccine-age">${escapeHtml(v.scheduled_age_display)}</span>
                    ${v.note ? `<span class="vaccine-note">${escapeHtml(v.note)}</span>` : ''}
                </div>
                <div class="vaccine-schedule-date">
                    <span>${escapeHtml(v.scheduled_date || '按需')}</span>
                    ${existing ? '<span class="vaccine-schedule-status"> 已接种</span>' :
                        `<button class="btn btn-sm btn-secondary js-quick-vaccine" data-vname="${escapeHtml(v.vaccine_name)}" data-dose="${v.dose}" data-vdate="${escapeHtml(v.scheduled_date || '')}" data-vtype="${escapeHtml(v.type)}">添加</button>`}
                </div>
            </div>`;
        }).join('');
    }
}

function renderVaccineList(vaccines) {
    const container = document.getElementById('vaccineList');
    if (!container) return;
    if (vaccines.length === 0) { container.innerHTML = '<p class="empty-tip">暂无疫苗记录</p>'; return; }
    const statusMap = { pending: '待接种', completed: '已完成', skipped: '跳过', delayed: '延期' };
    container.innerHTML = vaccines.map(v => {
        const statusClass = `vaccine-tag-${v.status}`;
        return `<div class="vaccine-card">
            <div class="vaccine-card-header">
                <span class="vaccine-card-name">${escapeHtml(v.vaccine_name)}</span>
                <span class="vaccine-card-dose">第${v.dose_number}针</span>
                <span class="vaccine-tag ${statusClass}">${statusMap[v.status] || escapeHtml(v.status)}</span>
            </div>
            <div class="vaccine-card-body">
                ${v.actual_date ? `<span> 接种: ${escapeHtml(v.actual_date)}</span>` : `<span> 计划: ${escapeHtml(v.scheduled_date || '未设置')}</span>`}
                ${v.hospital ? `<span> ${escapeHtml(v.hospital)}</span>` : ''}
                ${v.injection_site ? `<span> ${escapeHtml(v.injection_site)}</span>` : ''}
                ${v.batch_number ? `<span> 批号: ${escapeHtml(v.batch_number)}</span>` : ''}
                ${v.has_reaction ? `<span class="vaccine-reaction">[!] 不良反应: ${escapeHtml(v.reaction_detail)}</span>` : ''}
            </div>
            <div class="vaccine-card-actions">
                <button class="btn btn-sm btn-secondary" onclick="editVaccineRecord(${v.id})">编辑</button>
                <button class="btn btn-sm btn-danger" onclick="deleteVaccineRecord(${v.id})">删除</button>
            </div>
        </div>`;
    }).join('');
}

function showVaccineForm(vaccine = null) {
    const today = getToday();
    temporaryVaccineEditId = vaccine ? vaccine.id : null;
    const formHtml = `
        <div class="form-row">
            <div class="form-group"><label>疫苗名称 *</label><input type="text" id="formVaccineName" value="${escapeHtml(vaccine?.vaccine_name || '')}" placeholder="如: 乙肝疫苗"></div>
            <div class="form-group"><label>针次</label><input type="number" id="formVaccineDose" min="1" value="${vaccine?.dose_number || 1}"></div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>类型</label>
                <select id="formVaccineType"><option value="free" ${vaccine?.vaccine_type === 'free' ? 'selected' : ''}>一类（免费）</option><option value="paid" ${vaccine?.vaccine_type === 'paid' ? 'selected' : ''}>二类（自费）</option></select>
            </div>
            <div class="form-group">
                <label>状态</label>
                <select id="formVaccineStatus"><option value="pending" ${(!vaccine || vaccine.status === 'pending') ? 'selected' : ''}>待接种</option><option value="completed" ${vaccine?.status === 'completed' ? 'selected' : ''}>已完成</option><option value="delayed" ${vaccine?.status === 'delayed' ? 'selected' : ''}>延期</option><option value="skipped" ${vaccine?.status === 'skipped' ? 'selected' : ''}>跳过</option></select>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>计划日期</label><input type="date" id="formVaccineScheduled" value="${vaccine?.scheduled_date || today}"></div>
            <div class="form-group"><label>实际接种日期</label><input type="date" id="formVaccineActual" value="${vaccine?.actual_date || ''}"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>接种单位</label><input type="text" id="formVaccineHospital" value="${vaccine?.hospital || ''}"></div>
            <div class="form-group"><label>接种部位</label><input type="text" id="formVaccineSite" value="${vaccine?.injection_site || ''}" placeholder="左上臂/右大腿"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>生产厂家</label><input type="text" id="formVaccineManufacturer" value="${vaccine?.manufacturer || ''}"></div>
            <div class="form-group"><label>批号</label><input type="text" id="formVaccineBatch" value="${vaccine?.batch_number || ''}"></div>
        </div>
        <div class="form-group"><label>不良反应</label>
            <div style="display:flex;gap:10px;align-items:center">
                <label style="font-weight:normal"><input type="checkbox" id="formVaccineReaction" ${vaccine?.has_reaction ? 'checked' : ''}> 有反应</label>
                <input type="text" id="formVaccineReactionDetail" value="${vaccine?.reaction_detail || ''}" placeholder="如: 低热、局部红肿" style="flex:1">
                <select id="formVaccineReactionSeverity"><option value="none" ${(!vaccine || vaccine.reaction_severity === 'none') ? 'selected' : ''}>无</option><option value="mild" ${vaccine?.reaction_severity === 'mild' ? 'selected' : ''}>轻度</option><option value="moderate" ${vaccine?.reaction_severity === 'moderate' ? 'selected' : ''}>中度</option><option value="severe" ${vaccine?.reaction_severity === 'severe' ? 'selected' : ''}>重度</option></select>
            </div>
        </div>
        <div class="form-group"><label>备注</label><textarea id="formVaccineNote" rows="2">${vaccine?.note || ''}</textarea></div>
    `;
    document.getElementById('checkupFormBody').innerHTML = formHtml;
    showModal('checkupFormModal');
    document.querySelector('#checkupFormModal .modal-header h2').textContent = vaccine ? '编辑疫苗记录' : '添加疫苗记录';
    document.querySelector('#checkupFormModal .modal-footer .btn-primary').onclick = submitVaccineViaCheckupModal;
}

function submitVaccineViaCheckupModal() {
    const data = {
        vaccine_name: document.getElementById('formVaccineName')?.value?.trim(),
        dose_number: parseInt(document.getElementById('formVaccineDose')?.value) || 1,
        vaccine_type: document.getElementById('formVaccineType')?.value || 'free',
        status: document.getElementById('formVaccineStatus')?.value || 'pending',
        scheduled_date: document.getElementById('formVaccineScheduled')?.value || null,
        actual_date: document.getElementById('formVaccineActual')?.value || null,
        hospital: document.getElementById('formVaccineHospital')?.value?.trim() || '',
        injection_site: document.getElementById('formVaccineSite')?.value?.trim() || '',
        manufacturer: document.getElementById('formVaccineManufacturer')?.value?.trim() || '',
        batch_number: document.getElementById('formVaccineBatch')?.value?.trim() || '',
        has_reaction: document.getElementById('formVaccineReaction')?.checked || false,
        reaction_detail: document.getElementById('formVaccineReactionDetail')?.value?.trim() || '',
        reaction_severity: document.getElementById('formVaccineReactionSeverity')?.value || 'none',
        note: document.getElementById('formVaccineNote')?.value?.trim() || '',
    };
    if (!data.vaccine_name) { alert('请填写疫苗名称'); return; }
    const url = temporaryVaccineEditId ? `/api/health/vaccines/detail/${temporaryVaccineEditId}` : `/api/health/vaccines/${App.currentBaby}`;
    api(url, { method: temporaryVaccineEditId ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }).then(res => {
        if (res.success) {
            hideModal('checkupFormModal');
            temporaryVaccineEditId = null;
            loadVaccineRecords();
            document.querySelector('#checkupFormModal .modal-header h2').textContent = '添加体检记录';
            document.querySelector('#checkupFormModal .modal-footer .btn-primary').onclick = submitCheckupRecord;
        }
    });
}

// 疫苗快捷接种：事件委托（onclick 字符串拼接在 HTML属性+JS字符串 双重上下文中可被单引号突破，统一改走 data 属性）
document.addEventListener('click', function(e) {
    const btn = e.target.closest('.js-quick-vaccine');
    if (!btn) return;
    quickAddVaccine(btn.dataset.vname, btn.dataset.dose, btn.dataset.vdate, btn.dataset.vtype);
});

function quickAddVaccine(name, dose, scheduledDate, vaccineType) {
    const today = getToday();
    const data = { vaccine_name: name, dose_number: dose, vaccine_type: vaccineType, status: 'completed', scheduled_date: scheduledDate || today, actual_date: today };
    api(`/api/health/vaccines/${App.currentBaby}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }).then(res => { if (res.success) { loadVaccineRecords(); loadVaccineSchedule(); } });
}

function editVaccineRecord(id) {
    const vaccine = _vaccineRecords.find(v => v.id === id);
    if (vaccine) showVaccineForm(vaccine);
}

function deleteVaccineRecord(id) {
    if (!confirm('确定删除这条疫苗记录吗？')) return;
    api(`/api/health/vaccines/detail/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) { loadVaccineRecords(); loadOverview(); }
    });
}

function toggleVaccineSection(type) {
    const section = document.getElementById(type === 'standard' ? 'vaccineStandardList' : 'vaccineRecommendedList');
    if (section) {
        section.style.display = section.style.display === 'none' ? 'block' : 'none';
    }
}

// ==================== 发育里程碑 ====================
let _milestoneRecords = [];
let temporaryMilestoneEditId = null;

function initMilestoneTab() {
    document.getElementById('addMilestoneBtn2')?.addEventListener('click', () => showMilestoneForm());
    document.getElementById('initMilestoneBtn')?.addEventListener('click', initMilestones);
    document.getElementById('milestoneCategoryFilter')?.addEventListener('change', loadMilestones);
    document.getElementById('milestoneStatusFilter')?.addEventListener('change', loadMilestones);
}

function loadMilestones() {
    if (!App.currentBaby) return;
    const category = document.getElementById('milestoneCategoryFilter')?.value || '';
    const status = document.getElementById('milestoneStatusFilter')?.value || '';
    let url = `/api/health/milestones/${App.currentBaby}`;
    const params = [];
    if (category) params.push(`category=${category}`);
    if (status) params.push(`status=${status}`);
    if (params.length) url += '?' + params.join('&');
    api(url).then(res => {
        if (res.success) { _milestoneRecords = res.milestones; renderMilestoneProgress(res.milestones); renderMilestoneList(res.milestones); }
    });
}

function initMilestones() {
    if (!confirm('将自动生成 43 条标准发育里程碑（大运动、精细运动、语言、认知、社交、自理），是否继续？')) return;
    api(`/api/health/milestones/template/${App.currentBaby}`, { method: 'POST' }).then(res => {
        if (res.success) { alert(res.message); loadMilestones(); loadOverview(); } else { alert(res.message || '初始化失败'); }
    });
}

function renderMilestoneProgress(milestones) {
    const container = document.getElementById('milestoneProgress');
    if (!container) return;
    const total = milestones.length;
    const achieved = milestones.filter(m => m.status === 'achieved').length;
    const delayed = milestones.filter(m => m.status === 'delayed' || m.status === 'concerned').length;
    container.innerHTML = `
        <div class="milestone-progress-bar"><div class="milestone-progress-fill" style="width: ${total > 0 ? (achieved / total * 100) : 0}%"></div></div>
        <div class="milestone-progress-stats">
            <span> 已达成: ${achieved}</span><span> 待达成: ${total - achieved - delayed}</span>
            <span>[!] 延迟/关注: ${delayed}</span><span> 总计: ${total}</span>
        </div>`;
}

function renderMilestoneList(milestones) {
    const container = document.getElementById('milestoneList');
    if (!container) return;
    if (milestones.length === 0) { container.innerHTML = '<p class="empty-tip">暂无里程碑</p>'; return; }
    const catNames = { motor: ' 大运动', fine_motor: ' 精细运动', language: ' 语言', cognitive: ' 认知', social: ' 社交', self_care: ' 自理' };
    const statusMap = { pending: { text: '待达成', cls: 'milestone-pending' }, achieved: { text: ' 已达成', cls: 'milestone-achieved' }, delayed: { text: '[!] 延迟', cls: 'milestone-delayed' }, concerned: { text: ' 需关注', cls: 'milestone-concerned' } };
    let html = '';
    ['motor', 'fine_motor', 'language', 'cognitive', 'social', 'self_care'].forEach(cat => {
        const items = milestones.filter(m => m.category === cat);
        if (items.length === 0) return;
        html += `<div class="milestone-category"><h4>${catNames[cat]}</h4>`;
        items.forEach(m => {
            const s = statusMap[m.status] || statusMap.pending;
            html += `<div class="milestone-item ${s.cls}">
                <div class="milestone-item-info"><span class="milestone-name">${escapeHtml(m.milestone_name)}</span><span class="milestone-expected">${m.expected_age_months ? m.expected_age_months + '月龄' : ''}</span></div>
                <div class="milestone-item-status"><span class="${s.cls}">${s.text}</span>${m.achieved_date ? `<span>${m.achieved_date}</span>` : ''}</div>
                <div class="milestone-item-actions">
                    ${m.status !== 'achieved' ? `<button class="btn btn-sm btn-primary" onclick="achieveMilestone(${m.id})">达成</button>` : ''}
                    <button class="btn btn-sm btn-secondary" onclick="editMilestone(${m.id})">编辑</button>
                    <button class="btn btn-sm btn-danger" onclick="deleteMilestone(${m.id})">删除</button>
                </div>
            </div>`;
        });
        html += '</div>';
    });
    container.innerHTML = html;
}

function achieveMilestone(id) {
    const today = getToday();
    api(`/api/health/milestones/detail/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'achieved', achieved_date: today }) }).then(res => { if (res.success) { loadMilestones(); loadOverview(); } });
}

function showMilestoneForm(milestone = null) {
    temporaryMilestoneEditId = milestone ? milestone.id : null;
    const categories = [['motor', ' 大运动'], ['fine_motor', ' 精细运动'], ['language', ' 语言'], ['cognitive', ' 认知'], ['social', ' 社交'], ['self_care', ' 自理']];
    const formHtml = `
        <div class="form-group"><label>里程碑名称 *</label><input type="text" id="formMilestoneName" value="${escapeHtml(milestone?.milestone_name || '')}" placeholder="如: 独坐"></div>
        <div class="form-row">
            <div class="form-group"><label>分类 *</label><select id="formMilestoneCategory">${categories.map(c => `<option value="${c[0]}" ${milestone?.category === c[0] ? 'selected' : ''}>${c[1]}</option>`).join('')}</select></div>
            <div class="form-group"><label>预期月龄</label><input type="number" id="formMilestoneExpected" step="0.5" value="${milestone?.expected_age_months || ''}" placeholder="6"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>状态</label><select id="formMilestoneStatus"><option value="pending" ${(!milestone || milestone.status === 'pending') ? 'selected' : ''}>待达成</option><option value="achieved" ${milestone?.status === 'achieved' ? 'selected' : ''}>已达成</option><option value="delayed" ${milestone?.status === 'delayed' ? 'selected' : ''}>延迟</option><option value="concerned" ${milestone?.status === 'concerned' ? 'selected' : ''}>需关注</option></select></div>
            <div class="form-group"><label>达成日期</label><input type="date" id="formMilestoneDate" value="${milestone?.achieved_date || ''}"></div>
        </div>
        <div class="form-group"><label>备注</label><textarea id="formMilestoneNote" rows="2">${milestone?.note || ''}</textarea></div>
    `;
    document.getElementById('checkupFormBody').innerHTML = formHtml;
    showModal('checkupFormModal');
    document.querySelector('#checkupFormModal .modal-header h2').textContent = milestone ? '编辑里程碑' : '添加里程碑';
    document.querySelector('#checkupFormModal .modal-footer .btn-primary').onclick = submitMilestoneViaCheckupModal;
}

function submitMilestoneViaCheckupModal() {
    const data = {
        milestone_name: document.getElementById('formMilestoneName')?.value?.trim(),
        category: document.getElementById('formMilestoneCategory')?.value,
        expected_age_months: parseFloat(document.getElementById('formMilestoneExpected')?.value) || null,
        status: document.getElementById('formMilestoneStatus')?.value || 'pending',
        achieved_date: document.getElementById('formMilestoneDate')?.value || null,
        note: document.getElementById('formMilestoneNote')?.value?.trim() || '',
    };
    if (!data.milestone_name) { alert('请填写里程碑名称'); return; }
    const url = temporaryMilestoneEditId ? `/api/health/milestones/detail/${temporaryMilestoneEditId}` : `/api/health/milestones/${App.currentBaby}`;
    api(url, { method: temporaryMilestoneEditId ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }).then(res => {
        if (res.success) { hideModal('checkupFormModal'); temporaryMilestoneEditId = null; loadMilestones(); document.querySelector('#checkupFormModal .modal-header h2').textContent = '添加体检记录'; document.querySelector('#checkupFormModal .modal-footer .btn-primary').onclick = submitCheckupRecord; }
    });
}

function editMilestone(id) {
    const ms = _milestoneRecords.find(m => m.id === id);
    if (ms) showMilestoneForm(ms);
}

function deleteMilestone(id) {
    if (!confirm('确定删除这条里程碑记录吗？')) return;
    api(`/api/health/milestones/detail/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) { loadMilestones(); loadOverview(); }
    });
}

// ==================== 视力听力筛查 ====================
let _screeningRecords = [];
let temporaryScreeningEditId = null;

function initScreeningTab() {
    document.getElementById('addScreeningBtn')?.addEventListener('click', () => showScreeningForm());
    document.getElementById('screeningTypeFilter')?.addEventListener('change', loadScreenings);
    document.getElementById('addAsqFromHealthBtn')?.addEventListener('click', () => {
        // Navigate to ASQ page or show ASQ modal
        showAsqModalFromHealth();
    });
}

function loadScreenings() {
    if (!App.currentBaby) return;
    const stype = document.getElementById('screeningTypeFilter')?.value || '';
    let url = `/api/health/screenings/${App.currentBaby}`;
    if (stype) url += `?type=${encodeURIComponent(stype)}`;
    api(url).then(res => {
        if (res.success) { _screeningRecords = res.screenings; renderScreeningSummary(res.screenings); renderScreeningList(res.screenings); }
    });
    // 同时加载ASQ数据
    loadHealthAsq();
}

function loadHealthAsq() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/asq`).then(res => {
        if (res.success) { renderHealthAsqList(res.screenings); }
    });
}

function renderHealthAsqList(asqList) {
    const container = document.getElementById('healthAsqList');
    if (!container) return;
    if (!asqList || asqList.length === 0) { container.innerHTML = '<p class="empty-tip">暂无ASQ筛查记录</p>'; return; }
    const resultMap = { normal: '正常', monitoring: '需关注', referral: '需转诊' };
    const resultClass = { normal: 'asq-result-normal', monitoring: 'asq-result-monitoring', referral: 'asq-result-referral' };
    container.innerHTML = asqList.slice(0, 5).map(a => {
        const rClass = resultClass[a.result] || '';
        const rText = resultMap[a.result] || a.result;
        return `<div class="asq-mini-card ${rClass}">
            <div class="asq-mini-header"><span class="asq-mini-age">${a.age_months}月龄</span><span class="asq-mini-date">${a.screening_date}</span><span class="asq-mini-result ${rClass}">${rText}</span></div>
            <div class="asq-mini-scores">
                <span>沟通:${a.communication_score}</span><span>大运动:${a.gross_motor_score}</span>
                <span>精细:${a.fine_motor_score}</span><span>解决:${a.problem_solving_score}</span><span>社交:${a.personal_social_score}</span>
            </div>
        </div>`;
    }).join('');
}

function showAsqModalFromHealth() {
    // Reuse the existing ASQ modal from ASQ page
    const html = document.getElementById('asqModalBody')?.innerHTML;
    if (html) {
        showModal('asqModal');
    } else {
        // First time, create the modal content
        createAsqModalInHealth();
    }
}

function createAsqModalInHealth() {
    const modalHtml = `
        <div class="modal" id="asqModal" style="display:none">
            <div class="modal-content">
                <div class="modal-header"><h2>ASQ发育筛查</h2><button class="modal-close" onclick="hideModal('asqModal')">&times;</button></div>
                <div class="modal-body" id="asqModalBody">
                    <form id="asqForm">
                        <div class="form-row">
                            <div class="form-group"><label>月龄 *</label><select id="asqAgeMonths"></select></div>
                            <div class="form-group"><label>筛查日期</label><input type="date" id="asqDate"></div>
                        </div>
                        <div class="asq-form-scores">
                            <div class="form-group"><label>沟通 (0-60)</label><input type="number" id="asqComm" min="0" max="60" value="50"></div>
                            <div class="form-group"><label>大运动 (0-60)</label><input type="number" id="asqGross" min="0" max="60" value="50"></div>
                            <div class="form-group"><label>精细运动 (0-60)</label><input type="number" id="asqFine" min="0" max="60" value="50"></div>
                            <div class="form-group"><label>解决问题 (0-60)</label><input type="number" id="asqProblem" min="0" max="60" value="50"></div>
                            <div class="form-group"><label>个人-社交 (0-60)</label><input type="number" id="asqPersonal" min="0" max="60" value="50"></div>
                        </div>
                        <div class="form-group"><label>备注</label><input type="text" id="asqNote" placeholder="可选"></div>
                    </form>
                </div>
                <div class="modal-footer"><button class="btn btn-secondary" onclick="hideModal('asqModal')">取消</button><button class="btn btn-primary" id="submitAsqFromHealthBtn">保存</button></div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    // Populate age options
    const ageSelect = document.getElementById('asqAgeMonths');
    if (ageSelect) {
        const ages = [2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 27, 30, 33, 36];
        ageSelect.innerHTML = ages.map(a => `<option value="${a}">${a}个月</option>`).join('');
    }
    // Set today's date
    const dateInput = document.getElementById('asqDate');
    if (dateInput) dateInput.value = getToday();
    // Bind submit
    document.getElementById('submitAsqFromHealthBtn')?.addEventListener('click', submitAsqFromHealth);
    showModal('asqModal');
}

function submitAsqFromHealth() {
    const data = {
        age_months: parseInt(document.getElementById('asqAgeMonths')?.value),
        screening_date: document.getElementById('asqDate')?.value || getToday(),
        communication_score: parseInt(document.getElementById('asqComm')?.value) || 0,
        gross_motor_score: parseInt(document.getElementById('asqGross')?.value) || 0,
        fine_motor_score: parseInt(document.getElementById('asqFine')?.value) || 0,
        problem_solving_score: parseInt(document.getElementById('asqProblem')?.value) || 0,
        personal_social_score: parseInt(document.getElementById('asqPersonal')?.value) || 0,
        note: document.getElementById('asqNote')?.value || '',
    };
    api(`/api/babies/${App.currentBaby}/asq`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            hideModal('asqModal');
            loadHealthAsq();
            loadOverview();
        } else {
            alert('保存失败: ' + (res.message || '未知错误'));
        }
    });
}

function renderScreeningSummary(screenings) {
    const container = document.getElementById('screeningSummary');
    if (!container) return;
    const vision = screenings.filter(s => s.screening_type.includes('vision')).length;
    const hearing = screenings.filter(s => s.screening_type.includes('hearing')).length;
    const abnormal = screenings.filter(s => s.result_status === 'abnormal').length;
    container.innerHTML = `<div class="screening-summary-grid">
        <div class="screening-summary-card"><span class="screening-summary-num">${vision}</span><span class="screening-summary-label">视力检查</span></div>
        <div class="screening-summary-card"><span class="screening-summary-num">${hearing}</span><span class="screening-summary-label">听力检查</span></div>
        <div class="screening-summary-card ${abnormal > 0 ? 'screening-warning' : ''}"><span class="screening-summary-num">${abnormal}</span><span class="screening-summary-label">异常结果</span></div>
    </div>`;
}

function renderScreeningList(screenings) {
    const container = document.getElementById('screeningList');
    if (!container) return;
    if (screenings.length === 0) { container.innerHTML = '<p class="empty-tip">暂无筛查记录</p>'; return; }
    const typeMap = { 'vision_筛查': ' 视力筛查', 'hearing_筛查': ' 听力筛查', 'vision_诊断': ' 视力诊断', 'hearing_诊断': ' 听力诊断' };
    const statusMap = { normal: { text: '正常', cls: 'screening-normal' }, abnormal: { text: '异常', cls: 'screening-abnormal' }, borderline: { text: '边缘', cls: 'screening-borderline' }, pending: { text: '待判', cls: 'screening-pending' } };
    container.innerHTML = screenings.map(s => {
        const status = statusMap[s.result_status] || statusMap.pending;
        return `<div class="screening-card">
            <div class="screening-card-header"><span class="screening-type">${typeMap[s.screening_type] || s.screening_type}</span><span class="screening-date">${s.screening_date}</span><span class="screening-tag ${status.cls}">${status.text}</span></div>
            ${s.result_summary ? `<div class="screening-summary-text">${s.result_summary}</div>` : ''}
            ${s.hospital ? `<span> ${s.hospital}</span>` : ''}
            ${s.referral_needed ? `<span class="screening-referral"> 需转诊${s.referral_done ? '(已完成)' : ''}</span>` : ''}
            <div class="screening-card-actions"><button class="btn btn-sm btn-secondary" onclick="editScreening(${s.id})">编辑</button><button class="btn btn-sm btn-danger" onclick="deleteScreening(${s.id})">删除</button></div>
        </div>`;
    }).join('');
}

function showScreeningForm(screening = null) {
    const today = getToday();
    temporaryScreeningEditId = screening ? screening.id : null;
    const formHtml = `
        <div class="form-row">
            <div class="form-group"><label>筛查类型 *</label><select id="formScreeningType"><option value="vision_筛查" ${screening?.screening_type === 'vision_筛查' ? 'selected' : ''}> 视力筛查</option><option value="hearing_筛查" ${screening?.screening_type === 'hearing_筛查' ? 'selected' : ''}> 听力筛查</option><option value="vision_诊断" ${screening?.screening_type === 'vision_诊断' ? 'selected' : ''}> 视力诊断</option><option value="hearing_诊断" ${screening?.screening_type === 'hearing_诊断' ? 'selected' : ''}> 听力诊断</option></select></div>
            <div class="form-group"><label>筛查日期 *</label><input type="date" id="formScreeningDate" value="${screening?.screening_date || today}"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>筛查方法</label><input type="text" id="formScreeningMethod" value="${screening?.screening_method || ''}" placeholder="如: OAE, ABR"></div>
            <div class="form-group"><label>结果判定</label><select id="formScreeningStatus"><option value="normal" ${(!screening || screening.result_status === 'normal') ? 'selected' : ''}>正常</option><option value="borderline" ${screening?.result_status === 'borderline' ? 'selected' : ''}>边缘</option><option value="abnormal" ${screening?.result_status === 'abnormal' ? 'selected' : ''}>异常</option><option value="pending" ${screening?.result_status === 'pending' ? 'selected' : ''}>待判</option></select></div>
        </div>
        <div class="form-group"><label>结果概述</label><input type="text" id="formScreeningSummary" value="${screening?.result_summary || ''}"></div>
        <div class="form-row">
            <div class="form-group"><label>左眼/左耳</label><input type="text" id="formScreeningLeft" value="${screening?.detail_left || ''}"></div>
            <div class="form-group"><label>右眼/右耳</label><input type="text" id="formScreeningRight" value="${screening?.detail_right || ''}"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>检查机构</label><input type="text" id="formScreeningHospital" value="${screening?.hospital || ''}"></div>
            <div class="form-group"><label>检查医生</label><input type="text" id="formScreeningDoctor" value="${screening?.doctor || ''}"></div>
        </div>
        <div class="form-group">
            <label>转诊</label>
            <div style="display:flex;gap:10px"><label style="font-weight:normal"><input type="checkbox" id="formScreeningReferral" ${screening?.referral_needed ? 'checked' : ''}> 需转诊</label><label style="font-weight:normal"><input type="checkbox" id="formScreeningReferralDone" ${screening?.referral_done ? 'checked' : ''}> 已转诊</label></div>
        </div>
        <div class="form-group"><label>下次筛查日期</label><input type="date" id="formScreeningNext" value="${screening?.next_screening_date || ''}"></div>
        <div class="form-group"><label>备注</label><textarea id="formScreeningNote" rows="2">${screening?.note || ''}</textarea></div>
    `;
    document.getElementById('checkupFormBody').innerHTML = formHtml;
    showModal('checkupFormModal');
    document.querySelector('#checkupFormModal .modal-header h2').textContent = screening ? '编辑筛查记录' : '添加筛查记录';
    document.querySelector('#checkupFormModal .modal-footer .btn-primary').onclick = submitScreeningViaCheckupModal;
}

function submitScreeningViaCheckupModal() {
    const data = {
        screening_type: document.getElementById('formScreeningType')?.value,
        screening_date: document.getElementById('formScreeningDate')?.value,
        screening_method: document.getElementById('formScreeningMethod')?.value?.trim() || '',
        result_summary: document.getElementById('formScreeningSummary')?.value?.trim() || '',
        result_status: document.getElementById('formScreeningStatus')?.value || 'normal',
        detail_left: document.getElementById('formScreeningLeft')?.value?.trim() || '',
        detail_right: document.getElementById('formScreeningRight')?.value?.trim() || '',
        hospital: document.getElementById('formScreeningHospital')?.value?.trim() || '',
        doctor: document.getElementById('formScreeningDoctor')?.value?.trim() || '',
        referral_needed: document.getElementById('formScreeningReferral')?.checked || false,
        referral_done: document.getElementById('formScreeningReferralDone')?.checked || false,
        next_screening_date: document.getElementById('formScreeningNext')?.value || null,
        note: document.getElementById('formScreeningNote')?.value?.trim() || '',
    };
    if (!data.screening_type || !data.screening_date) { alert('请填写筛查类型和日期'); return; }
    const url = temporaryScreeningEditId ? `/api/health/screenings/detail/${temporaryScreeningEditId}` : `/api/health/screenings/${App.currentBaby}`;
    api(url, { method: temporaryScreeningEditId ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }).then(res => {
        if (res.success) { hideModal('checkupFormModal'); temporaryScreeningEditId = null; loadScreenings(); document.querySelector('#checkupFormModal .modal-header h2').textContent = '添加体检记录'; document.querySelector('#checkupFormModal .modal-footer .btn-primary').onclick = submitCheckupRecord; }
    });
}

function editScreening(id) {
    const s = _screeningRecords.find(x => x.id === id);
    if (s) showScreeningForm(s);
}

function deleteScreening(id) {
    if (!confirm('确定删除这条筛查记录吗？')) return;
    api(`/api/health/screenings/detail/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) { loadScreenings(); loadOverview(); }
    });
}

// ==================== 过敏史管理 ====================
let _allergyRecords = [];
let temporaryAllergyEditId = null;

function initAllergyTab() {
    document.getElementById('addAllergyBtn')?.addEventListener('click', () => showAllergyForm());
    document.getElementById('allergyTypeFilter')?.addEventListener('change', loadAllergies);
    document.getElementById('showResolvedAllergies')?.addEventListener('change', loadAllergies);
}

function loadAllergies() {
    if (!App.currentBaby) return;
    const atype = document.getElementById('allergyTypeFilter')?.value || '';
    const showResolved = document.getElementById('showResolvedAllergies')?.checked || false;
    let url = `/api/health/allergies/${App.currentBaby}`;
    const params = [];
    if (atype) params.push(`type=${atype}`);
    if (!showResolved) params.push('status=active');
    if (params.length) url += '?' + params.join('&');
    api(url).then(res => {
        if (res.success) { _allergyRecords = res.allergies; renderAllergyAlert(res.allergies); renderAllergyHistory(res.allergies); }
    });
}

function renderAllergyAlert(allergies) {
    const box = document.getElementById('allergyAlertBox');
    if (!box) return;
    const severe = allergies.filter(a => a.severity_level === 'severe' || a.severity_level === 'anaphylaxis');
    if (severe.length > 0) {
        box.style.display = 'block';
        box.innerHTML = `<strong>[!] 严重过敏警示：</strong>${severe.map(a => escapeHtml(a.allergen_name)).join('、')} — 需随身携带应急药物`;
    } else {
        box.style.display = 'none';
    }
}

function renderAllergyHistory(allergies) {
    // 健康档案过敏史（allergy_history 表）列表渲染，带编辑 / 删除按钮。
    // 注意：此前 loadAllergies 误调 renderAllergyList（按 allergy-tests 字段渲染），
    // 而健康档案容器是 #allergyList，导致列表从未显示、编辑/删除按钮悬空。
    const box = document.getElementById('allergyList');
    if (!box) return;
    if (!allergies || allergies.length === 0) {
        box.innerHTML = '<p class="empty-tip">暂无过敏记录</p>';
        return;
    }
    const typeNames = { food: '食物', drug: '药物', environmental: '环境', other: '其他' };
    const severityNames = { mild: '轻度', moderate: '中度', severe: '重度', anaphylaxis: '过敏性休克' };
    const statusNames = { active: '活跃', monitoring: '监测中', resolved: '已缓解' };
    const html = allergies.map(a => `
        <div class="allergy-item-card">
            <div class="allergy-item-header">
                <span class="allergy-item-food">${escapeHtml(a.allergen_name || '')}</span>
                <span class="allergy-item-type">${typeNames[a.allergen_type] || '其他'}</span>
                <span class="allergy-item-severity ${escapeHtml(a.severity_level || 'mild')}">${severityNames[a.severity_level] || escapeHtml(a.severity_level || '')}</span>
            </div>
            <div class="allergy-item-meta">状态：${statusNames[a.status] || escapeHtml(a.status || 'active')}</div>
            ${a.reaction_detail ? `<div class="allergy-item-symptoms">反应：${escapeHtml(a.reaction_detail)}</div>` : ''}
            ${a.first_occurrence_date ? `<div class="allergy-item-symptoms">首次：${escapeHtml(a.first_occurrence_date)}</div>` : ''}
            <div class="allergy-item-actions">
                <button class="btn btn-outline btn-sm" onclick="editAllergy(${a.id})">编辑</button>
                <button class="btn btn-outline btn-sm" onclick="deleteAllergy(${a.id})">删除</button>
            </div>
        </div>
    `).join('');
    box.innerHTML = html;
}

function showAllergyForm(allergy = null) {
    temporaryAllergyEditId = allergy ? allergy.id : null;
    const formHtml = `
        <div class="form-row">
            <div class="form-group"><label>过敏原类型 *</label><select id="formAllergyType"><option value="food" ${allergy?.allergen_type === 'food' ? 'selected' : ''}> 食物</option><option value="drug" ${allergy?.allergen_type === 'drug' ? 'selected' : ''}> 药物</option><option value="environmental" ${allergy?.allergen_type === 'environmental' ? 'selected' : ''}> 环境</option><option value="other" ${allergy?.allergen_type === 'other' ? 'selected' : ''}> 其他</option></select></div>
            <div class="form-group"><label>过敏原名称 *</label><input type="text" id="formAllergyName" value="${escapeHtml(allergy?.allergen_name || '')}" placeholder="如: 牛奶蛋白、鸡蛋"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>严重程度</label><select id="formAllergySeverity"><option value="mild" ${(!allergy || allergy.severity_level === 'mild') ? 'selected' : ''}>轻度</option><option value="moderate" ${allergy?.severity_level === 'moderate' ? 'selected' : ''}>中度</option><option value="severe" ${allergy?.severity_level === 'severe' ? 'selected' : ''}>重度</option><option value="anaphylaxis" ${allergy?.severity_level === 'anaphylaxis' ? 'selected' : ''}>过敏性休克</option></select></div>
            <div class="form-group"><label>状态</label><select id="formAllergyStatus"><option value="active" ${(!allergy || allergy.status === 'active') ? 'selected' : ''}>活跃</option><option value="monitoring" ${allergy?.status === 'monitoring' ? 'selected' : ''}>监测中</option><option value="resolved" ${allergy?.status === 'resolved' ? 'selected' : ''}>已缓解</option></select></div>
        </div>
        <div class="form-group"><label>过敏反应详情</label><textarea id="formAllergyReaction" rows="2" placeholder="描述过敏症状">${escapeHtml(allergy?.reaction_detail || '')}</textarea></div>
        <div class="form-row">
            <div class="form-group"><label>首次发生日期</label><input type="date" id="formAllergyFirst" value="${allergy?.first_occurrence_date || ''}"></div>
            <div class="form-group"><label>最近发生日期</label><input type="date" id="formAllergyLast" value="${allergy?.last_occurrence_date || ''}"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>诊断方法</label><input type="text" id="formAllergyDiagMethod" value="${allergy?.diagnosis_method || ''}" placeholder="如: 血清IgE、皮肤点刺"></div>
            <div class="form-group"><label>诊断医生</label><input type="text" id="formAllergyDiagBy" value="${allergy?.diagnosed_by || ''}"></div>
        </div>
        <div class="form-group"><label>处理措施</label><textarea id="formAllergyMgmt" rows="2" placeholder="治疗和预防措施">${allergy?.management || ''}</textarea></div>
        <div class="form-group"><label>备注</label><textarea id="formAllergyNote" rows="1">${escapeHtml(allergy?.note || '')}</textarea></div>
    `;
    document.getElementById('checkupFormBody').innerHTML = formHtml;
    showModal('checkupFormModal');
    document.querySelector('#checkupFormModal .modal-header h2').textContent = allergy ? '编辑过敏史' : '添加过敏记录';
    document.querySelector('#checkupFormModal .modal-footer .btn-primary').onclick = submitAllergyViaCheckupModal;
}

function submitAllergyViaCheckupModal() {
    const data = {
        allergen_type: document.getElementById('formAllergyType')?.value,
        allergen_name: document.getElementById('formAllergyName')?.value?.trim(),
        severity_level: document.getElementById('formAllergySeverity')?.value || 'mild',
        status: document.getElementById('formAllergyStatus')?.value || 'active',
        reaction_detail: document.getElementById('formAllergyReaction')?.value?.trim() || '',
        first_occurrence_date: document.getElementById('formAllergyFirst')?.value || null,
        last_occurrence_date: document.getElementById('formAllergyLast')?.value || null,
        diagnosis_method: document.getElementById('formAllergyDiagMethod')?.value?.trim() || '',
        diagnosed_by: document.getElementById('formAllergyDiagBy')?.value?.trim() || '',
        management: document.getElementById('formAllergyMgmt')?.value?.trim() || '',
        note: document.getElementById('formAllergyNote')?.value?.trim() || '',
    };
    if (!data.allergen_name) { alert('请填写过敏原名称'); return; }
    const url = temporaryAllergyEditId ? `/api/health/allergies/detail/${temporaryAllergyEditId}` : `/api/health/allergies/${App.currentBaby}`;
    api(url, { method: temporaryAllergyEditId ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }).then(res => {
        if (res.success) { hideModal('checkupFormModal'); temporaryAllergyEditId = null; loadAllergies(); loadOverview(); document.querySelector('#checkupFormModal .modal-header h2').textContent = '添加体检记录'; document.querySelector('#checkupFormModal .modal-footer .btn-primary').onclick = submitCheckupRecord; }
    });
}

function editAllergy(id) {
    const a = _allergyRecords.find(x => x.id === id);
    if (a) showAllergyForm(a);
}

function deleteAllergy(id) {
    if (!confirm('确定删除这条过敏记录吗？')) return;
    api(`/api/health/allergies/detail/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) { loadAllergies(); loadOverview(); }
    });
}

// ==================== 喂养摘要管理 ====================
let _feedingSummary = [];
let temporaryFeedingEditId = null;

function initFeedingTab() {
    document.getElementById('addFeedingSummaryBtn')?.addEventListener('click', () => showFeedingForm());
    document.getElementById('autoGenerateFeedingBtn')?.addEventListener('click', autoGenerateFeeding);
    document.getElementById('feedingMonthFilter')?.addEventListener('change', loadFeedingSummary);
}

function loadFeedingSummary() {
    if (!App.currentBaby) return;
    api(`/api/health/feeding_summary/${App.currentBaby}`).then(res => {
        if (res.success) {
            _feedingSummary = res.feeding_summary;
            renderFeedingSummary(res.feeding_summary);
        }
    });
}

function renderFeedingSummary(summaries) {
    const container = document.getElementById('feedingSummaryList');
    if (!container) return;
    if (summaries.length === 0) { container.innerHTML = '<p class="empty-tip">暂无喂养摘要，点击"自动生成"从喂养记录汇总</p>'; return; }
    const typeMap = { breastmilk: '母乳', formula: '配方奶', mixed: '混合喂养', solid: '辅食为主' };
    container.innerHTML = summaries.map(s => `
        <div class="feeding-card">
            <div class="feeding-card-header">
                <span class="feeding-month"> ${s.record_month}</span>
                <span class="feeding-type-tag">${typeMap[s.feeding_type] || s.feeding_type || '未记录'}</span>
            </div>
            <div class="feeding-card-body">
                ${s.avg_daily_ml ? `<span> 日均奶量: ${s.avg_daily_ml}ml</span>` : ''}
                ${s.feeding_frequency ? `<span> 日均次数: ${s.feeding_frequency}次</span>` : ''}
                ${s.solid_food_count ? `<span> 辅食种类: ${s.solid_food_count}种</span>` : ''}
                ${s.weight_gain_month ? `<span> 月增重: ${s.weight_gain_month}g</span>` : ''}
                ${s.height_gain_month ? `<span> 月增高: ${s.height_gain_month}cm</span>` : ''}
            </div>
            ${s.concerns ? `<div class="feeding-concerns">[!] ${escapeHtml(s.concerns)}</div>` : ''}
            <div class="feeding-card-actions">
                <button class="btn btn-sm btn-secondary" onclick="editFeedingSummary(${s.id})">编辑</button>
                <button class="btn btn-sm btn-danger" onclick="deleteFeedingSummary(${s.id})">删除</button>
            </div>
        </div>
    `).join('');
}

function autoGenerateFeeding() {
    if (!confirm('将根据最近6个月的喂养记录自动生成月度摘要，是否继续？')) return;
    api(`/api/health/feeding_summary/generate/${App.currentBaby}`, { method: 'POST' }).then(res => {
        if (res.success) { alert(res.message); loadFeedingSummary(); loadOverview(); }
    });
}

function showFeedingForm(summary = null) {
    temporaryFeedingEditId = summary ? summary.id : null;
    const today = new Date();
    const currentMonth = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`;
    const formHtml = `
        <div class="form-row">
            <div class="form-group"><label>月份 *</label><input type="month" id="formFeedingMonth" value="${summary?.record_month || currentMonth}"></div>
            <div class="form-group"><label>喂养方式</label><select id="formFeedingType"><option value="breastmilk" ${summary?.feeding_type === 'breastmilk' ? 'selected' : ''}>母乳</option><option value="formula" ${summary?.feeding_type === 'formula' ? 'selected' : ''}>配方奶</option><option value="mixed" ${summary?.feeding_type === 'mixed' ? 'selected' : ''}>混合喂养</option><option value="solid" ${summary?.feeding_type === 'solid' ? 'selected' : ''}>辅食为主</option></select></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>日均奶量(ml)</label><input type="number" id="formFeedingMl" step="0.1" value="${summary?.avg_daily_ml || ''}"></div>
            <div class="form-group"><label>日均喂养次数</label><input type="number" id="formFeedingFreq" value="${summary?.feeding_frequency || ''}"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>辅食种类数</label><input type="number" id="formFeedingSolid" value="${summary?.solid_food_count || ''}"></div>
            <div class="form-group"><label>月增重(g)</label><input type="number" id="formFeedingWeight" step="0.1" value="${summary?.weight_gain_month || ''}"></div>
        </div>
        <div class="form-row">
            <div class="form-group"><label>月增高(cm)</label><input type="number" id="formFeedingHeight" step="0.1" value="${summary?.height_gain_month || ''}"></div>
        </div>
        <div class="form-group"><label>喂养问题/注意事项</label><textarea id="formFeedingConcerns" rows="2">${summary?.concerns || ''}</textarea></div>
        <div class="form-group"><label>备注</label><textarea id="formFeedingNote" rows="1">${summary?.note || ''}</textarea></div>
    `;
    document.getElementById('checkupFormBody').innerHTML = formHtml;
    showModal('checkupFormModal');
    document.querySelector('#checkupFormModal .modal-header h2').textContent = summary ? '编辑喂养摘要' : '添加喂养摘要';
    document.querySelector('#checkupFormModal .modal-footer .btn-primary').onclick = submitFeedingViaCheckupModal;
}

function submitFeedingViaCheckupModal() {
    const data = {
        record_month: document.getElementById('formFeedingMonth')?.value,
        feeding_type: document.getElementById('formFeedingType')?.value,
        avg_daily_ml: parseFloat(document.getElementById('formFeedingMl')?.value) || null,
        feeding_frequency: parseInt(document.getElementById('formFeedingFreq')?.value) || null,
        solid_food_count: parseInt(document.getElementById('formFeedingSolid')?.value) || null,
        weight_gain_month: parseFloat(document.getElementById('formFeedingWeight')?.value) || null,
        height_gain_month: parseFloat(document.getElementById('formFeedingHeight')?.value) || null,
        concerns: document.getElementById('formFeedingConcerns')?.value?.trim() || '',
        note: document.getElementById('formFeedingNote')?.value?.trim() || '',
    };
    if (!data.record_month) { alert('请选择月份'); return; }
    const url = temporaryFeedingEditId ? `/api/health/feeding_summary/detail/${temporaryFeedingEditId}` : `/api/health/feeding_summary/${App.currentBaby}`;
    api(url, { method: temporaryFeedingEditId ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }).then(res => {
        if (res.success) { hideModal('checkupFormModal'); temporaryFeedingEditId = null; loadFeedingSummary(); document.querySelector('#checkupFormModal .modal-header h2').textContent = '添加体检记录'; document.querySelector('#checkupFormModal .modal-footer .btn-primary').onclick = submitCheckupRecord; }
    });
}

function editFeedingSummary(id) {
    const s = _feedingSummary.find(x => x.id === id);
    if (s) showFeedingForm(s);
}

function deleteFeedingSummary(id) {
    if (!confirm('确定删除这条喂养摘要吗？')) return;
    api(`/api/health/feeding_summary/detail/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) { loadFeedingSummary(); loadOverview(); }
    });
}

// ==================== 成长里程碑页面（现有独立页面） ====================
function initFirstsPage() {
    document.getElementById('addFirstsBtn')?.addEventListener('click', showAddFirstsModal);
    document.querySelectorAll('.firsts-template-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            showAddFirstsModal(btn.dataset.title, btn.dataset.cat);
        });
    });
}

function loadFirstsRecords() {
    if (!App.currentBaby) return;
    // 「第一次」与里程碑共用 milestones 表，只取 is_first = 1 的记录
    api(`/api/babies/${App.currentBaby}/milestones?first=1`).then(res => {
        if (res.success) {
            App.firstsRecords = res.data;
            renderFirstsList(res.data);
        }
    });
}

function renderFirstsList(records) {
    const container = document.getElementById('firstsList');
    if (!container) return;

    if (records.length === 0) {
        container.innerHTML = '<p class="empty-tip">暂无里程碑记录，点击上方模板快速添加</p>';
        return;
    }

    const catNames = { motor: '运动', language: '语言', social: '社交', cognitive: '认知', other: '其他' };
    container.innerHTML = records.map(r => `
        <div class="firsts-item-card">
            <div class="firsts-item-info">
                <div class="firsts-item-title">${escapeHtml(r.title)}</div>
                <div class="firsts-item-date">${escapeHtml(r.achieved_date)}${catNames[r.category] ? ' · ' + catNames[r.category] : ''}</div>
                ${r.description ? `<div class="firsts-item-note">${escapeHtml(r.description)}</div>` : ''}
            </div>
            <button class="firsts-delete" onclick="deleteFirstsRecord(${r.id})">X</button>
        </div>
    `).join('');
}

function showAddFirstsModal(title = '', category = '') {
    const today = getToday();
    const formHtml = `
        <div class="form-group">
            <label>成就名称</label>
            <input type="text" id="firstsTitle" value="${title}" placeholder="如: 第一次翻身">
        </div>
        <div class="form-group">
            <label>类别</label>
            <select id="firstsCategory">
                <option value="motor" ${category === 'motor' ? 'selected' : ''}>运动</option>
                <option value="language" ${category === 'language' ? 'selected' : ''}>语言</option>
                <option value="social" ${category === 'social' ? 'selected' : ''}>社交</option>
                <option value="cognitive" ${category === 'cognitive' ? 'selected' : ''}>认知</option>
                <option value="other" ${category === 'other' ? 'selected' : ''}>其他</option>
            </select>
        </div>
        <div class="form-group">
            <label>达成日期</label>
            <input type="date" id="firstsDate" value="${today}">
        </div>
        <div class="form-group">
            <label>备注</label>
            <textarea id="firstsNote" rows="2" placeholder="记录这个特别的时刻..."></textarea>
        </div>
    `;

    showModal('firstsModal');
    const body = document.getElementById('firstsModalBody');
    if (body) body.innerHTML = formHtml;
}

function submitFirstsRecord() {
    const data = {
        title: document.getElementById('firstsTitle')?.value,
        category: document.getElementById('firstsCategory')?.value || 'other',
        achieved_date: document.getElementById('firstsDate')?.value,
        description: document.getElementById('firstsNote')?.value || '',
        photo_url: '',
        is_first: 1,
    };

    if (!data.title) {
        alert('请输入成就名称');
        return;
    }

    api(`/api/babies/${App.currentBaby}/milestones`, { method: 'POST', body: JSON.stringify(data) }).then(res => {
        if (res.success) {
            hideModal('firstsModal');
            // 里程碑与「第一次」共用一张表，两边都要跟着刷新
            loadFirstsRecords();
            loadMilestonesPage();
        }
    });
}

function deleteFirstsRecord(id) {
    if (!confirm('确定要删除这条记录吗？')) return;
    api(`/api/babies/${App.currentBaby}/milestones/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            // 里程碑与「第一次」共用一张表，两边都要跟着刷新
            loadFirstsRecords();
            loadMilestonesPage();
        }
    });
}

// ==================== 数据报告 ====================
function initReportsPage() {
    if (App.reportsInitialized) return;   // 只绑一次，避免重复监听
    App.reportsInitialized = true;
    document.querySelectorAll('.report-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.report-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadReportData(btn.dataset.tab);
        });
    });
}

function loadReportData(tab) {
    if (!App.currentBaby) return;
    
    // 计算最近30天的数据
    const days = 30;
    const endDate = new Date();
    const startDate = new Date();
    startDate.setDate(startDate.getDate() - days);
    const startStr = formatDate(startDate);
    const endStr = formatDate(endDate);

    switch (tab) {
        case 'sleep':
            loadSleepReport(startStr, endStr);
            break;
        case 'feeding':
            loadFeedingReport(startStr, endStr);
            break;
        case 'diaper':
            loadDiaperReport(startStr, endStr);
            break;
        case 'growth':
            loadGrowthReport();
            break;
    }
}

function loadSleepReport(startStr, endStr) {
    api(`/api/babies/${App.currentBaby}/sleep?start=${startStr}&end=${endStr}`).then(res => {
        if (!res.success) return;
        const records = res.data;
        
        // 汇总卡片
        const totalSleep = records.reduce((sum, r) => sum + (r.duration_minutes || 0), 0);
        const avgSleep = records.length > 0 ? totalSleep / records.length : 0;
        const totalHours = (totalSleep / 60).toFixed(1);
        
        renderSummaryCards([
            { value: totalHours + 'h', label: '总睡眠时长' },
            { value: avgSleep.toFixed(0) + 'm', label: '平均每次睡眠' },
            { value: records.length + '次', label: '睡眠次数' },
        ]);

        // 绘制趋势图
        drawSleepTrendChart(records, startStr, endStr);
        
        // 统计信息
        renderStats([
{ label: '最长睡眠', value: Math.max(...records.map(r => r.duration_minutes || 0), 0) + ' 分钟' },
{ label: '最短睡眠', value: Math.min(...records.map(r => r.duration_minutes || 9999), 0) + ' 分钟' },
            { label: '夜醒次数', value: '暂无数据' },
        ]);
    });
}

function loadFeedingReport(startStr, endStr) {
    api(`/api/babies/${App.currentBaby}/feedings?start=${startStr}&end=${endStr}`).then(res => {
        if (!res.success) return;
        const records = res.data;
        
        const totalAmount = records.reduce((sum, r) => sum + (r.amount || 0), 0);
        const breastCount = records.filter(r => r.type === 'breast').length;
        const bottleCount = records.filter(r => r.type === 'bottle').length;
        const solidsCount = records.filter(r => r.type === 'solids').length;
        
        renderSummaryCards([
            { value: records.length + '次', label: '喂养次数' },
            { value: totalAmount + 'ml', label: '总喂养量' },
            { value: breastCount + '次', label: '母乳次数' },
        ]);

        drawFeedingTrendChart(records, startStr, endStr);
        
        renderStats([
            { label: '母乳次数', value: breastCount + ' 次' },
            { label: '配方奶次数', value: bottleCount + ' 次' },
            { label: '辅食次数', value: solidsCount + ' 次' },
            { label: '平均每次', value: records.length > 0 ? Math.round(totalAmount / records.length) + ' ml' : '0 ml' },
        ]);
    });
}

function loadDiaperReport(startStr, endStr) {
    api(`/api/babies/${App.currentBaby}/diapers?start=${startStr}&end=${endStr}`).then(res => {
        if (!res.success) return;
        const records = res.data;
        
        const wetCount = records.filter(r => r.type === 'wet' || r.type === 'both').length;
        const dirtyCount = records.filter(r => r.type === 'dirty' || r.type === 'both').length;
        
        renderSummaryCards([
            { value: records.length + '次', label: '更换次数' },
            { value: wetCount + '次', label: '小便' },
            { value: dirtyCount + '次', label: '大便' },
        ]);

        drawDiaperTrendChart(records, startStr, endStr);
        
        // 计算平均间隔
        if (records.length > 1) {
            const sorted = records.sort((a, b) => new Date(a.time) - new Date(b.time));
            let totalInterval = 0;
            for (let i = 1; i < sorted.length; i++) {
                totalInterval += (new Date(sorted[i].time) - new Date(sorted[i-1].time)) / (1000 * 60);
            }
            const avgInterval = totalInterval / (sorted.length - 1);
            renderStats([
                { label: '平均间隔', value: avgInterval.toFixed(0) + ' 分钟' },
                { label: '每天平均', value: (records.length / 30).toFixed(1) + ' 次' },
            ]);
        }
    });
}

function loadGrowthReport() {
    api(`/api/babies/${App.currentBaby}/growth`).then(res => {
        if (!res.success) return;
        const records = res.data;
        
        if (records.length === 0) {
            renderSummaryCards([
                { value: '0', label: '记录数' },
                { value: '-', label: '身高' },
                { value: '-', label: '体重' },
            ]);
            return;
        }

        const latest = records[0];
        const previous = records.length > 1 ? records[1] : null;
        const heightGrowth = previous ? (latest.height - previous.height).toFixed(1) : '-';
        const weightGrowth = previous ? (latest.weight - previous.weight).toFixed(1) : '-';
        
        renderSummaryCards([
            { value: latest.height + 'cm', label: '最新身高' },
            { value: latest.weight + 'kg', label: '最新体重' },
            { value: records.length + '次', label: '记录次数' },
        ]);

        drawGrowthTrendChart(records);
        
        renderStats([
            { label: '身高变化', value: (heightGrowth !== '-' ? '+'+heightGrowth+'cm' : '-') },
            { label: '体重变化', value: (weightGrowth !== '-' ? '+'+weightGrowth+'kg' : '-') },
            { label: 'BMI', value: latest.bmi || '-' },
        ]);
    });
}

function renderSummaryCards(cards) {
    const container = document.getElementById('reportSummaryCards');
    if (!container) return;
    container.innerHTML = cards.map(c => `
        <div class="report-summary-card">
            <div class="report-summary-value">${c.value}</div>
            <div class="report-summary-label">${c.label}</div>
        </div>
    `).join('');
}

function renderStats(stats) {
    const container = document.getElementById('reportStats');
    if (!container) return;
    container.innerHTML = stats.map(s => `
        <div class="report-stat-item">
            <span class="report-stat-label">${s.label}</span>
            <span class="report-stat-value">${s.value}</span>
        </div>
    `).join('');
}

// 绘制睡眠趋势图
function drawSleepTrendChart(records, startStr, endStr) {
    const canvas = document.getElementById('reportChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = 250 * dpr;
    ctx.scale(dpr, dpr);
    const width = rect.width;
    const height = 250;

    ctx.clearRect(0, 0, width, height);
    
    const titleEl = document.getElementById('reportChartTitle');
    if (titleEl) titleEl.textContent = '睡眠时长趋势';

    const padding = { top: 20, right: 20, bottom: 40, left: 50 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    // 按日期分组
    const dailyData = {};
    records.forEach(r => {
        const date = r.start_date || r.start_time?.split(' ')[0];
        if (!date) return;
        dailyData[date] = (dailyData[date] || 0) + (r.duration || 0);
    });

    const dates = Object.keys(dailyData).sort();
    if (dates.length === 0) {
        ctx.fillStyle = '#999';
        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('暂无数据', width/2, height/2);
        return;
    }

    const values = dates.map(d => dailyData[d]);
    const maxVal = Math.max(...values, 1);

    // 绘制坐标轴
    ctx.strokeStyle = '#E0E0E0';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padding.left, padding.top);
    ctx.lineTo(padding.left, height - padding.bottom);
    ctx.lineTo(width - padding.right, height - padding.bottom);
    ctx.stroke();

    // Y轴标签
    ctx.fillStyle = '#999';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
        const val = maxVal * (i / 4);
        const y = height - padding.bottom - chartH * (i / 4);
        ctx.fillText(val.toFixed(0) + 'm', padding.left - 5, y + 4);
    }

    // 绘制柱状图
    const barWidth = Math.min(20, chartW / dates.length - 2);
    dates.forEach((date, i) => {
        const x = padding.left + (chartW * i / dates.length) + (chartW / dates.length - barWidth) / 2;
        const barH = chartH * (dailyData[date] / maxVal);
        const y = height - padding.bottom - barH;
        
        // 渐变色
        const gradient = ctx.createLinearGradient(x, y, x, height - padding.bottom);
        gradient.addColorStop(0, '#42A5F5');
        gradient.addColorStop(1, '#90CAF9');
        ctx.fillStyle = gradient;
        ctx.fillRect(x, y, barWidth, barH);

        // X轴标签（只显示部分）
        if (dates.length <= 15 || i % Math.ceil(dates.length/10) === 0) {
            ctx.fillStyle = '#666';
            ctx.font = '10px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(date.slice(5), x + barWidth/2, height - padding.bottom + 15);
        }
    });
}

// 绘制喂养趋势图
function drawFeedingTrendChart(records, startStr, endStr) {
    const canvas = document.getElementById('reportChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = 250 * dpr;
    ctx.scale(dpr, dpr);
    const width = rect.width;
    const height = 250;

    ctx.clearRect(0, 0, width, height);
    
    const titleEl = document.getElementById('reportChartTitle');
    if (titleEl) titleEl.textContent = '喂养量趋势';

    const padding = { top: 20, right: 20, bottom: 40, left: 50 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    // 按日期分组
    const dailyData = {};
    records.forEach(r => {
        const date = r.start_date || r.time?.split(' ')[0];
        if (!date) return;
        dailyData[date] = (dailyData[date] || 0) + (r.amount || 0);
    });

    const dates = Object.keys(dailyData).sort();
    if (dates.length === 0) return;

    const values = dates.map(d => dailyData[d]);
    const maxVal = Math.max(...values, 1);

    // 绘制折线图
    ctx.strokeStyle = '#E0E0E0';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padding.left, padding.top);
    ctx.lineTo(padding.left, height - padding.bottom);
    ctx.lineTo(width - padding.right, height - padding.bottom);
    ctx.stroke();

    // Y轴标签
    ctx.fillStyle = '#999';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
        const val = maxVal * (i / 4);
        const y = height - padding.bottom - chartH * (i / 4);
        ctx.fillText(val.toFixed(0), padding.left - 5, y + 4);
    }

    // 绘制折线
    ctx.strokeStyle = '#66BB6A';
    ctx.lineWidth = 2;
    ctx.beginPath();
    dates.forEach((date, i) => {
        const x = padding.left + (chartW * i / Math.max(dates.length - 1, 1));
        const y = height - padding.bottom - chartH * (dailyData[date] / maxVal);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // 绘制数据点
    dates.forEach((date, i) => {
        const x = padding.left + (chartW * i / Math.max(dates.length - 1, 1));
        const y = height - padding.bottom - chartH * (dailyData[date] / maxVal);
        ctx.fillStyle = '#66BB6A';
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fill();
    });
}

// 绘制排泄趋势图
function drawDiaperTrendChart(records, startStr, endStr) {
    const canvas = document.getElementById('reportChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = 250 * dpr;
    ctx.scale(dpr, dpr);
    const width = rect.width;
    const height = 250;

    ctx.clearRect(0, 0, width, height);
    
    const titleEl = document.getElementById('reportChartTitle');
    if (titleEl) titleEl.textContent = '每日排泄次数';

    const padding = { top: 20, right: 20, bottom: 40, left: 50 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const dailyData = {};
    records.forEach(r => {
        const date = r.time?.split(' ')[0] || r.start_date;
        if (!date) return;
        dailyData[date] = (dailyData[date] || 0) + 1;
    });

    const dates = Object.keys(dailyData).sort();
    if (dates.length === 0) return;

    const values = dates.map(d => dailyData[d]);
    const maxVal = Math.max(...values, 1);

    // 绘制坐标轴
    ctx.strokeStyle = '#E0E0E0';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padding.left, padding.top);
    ctx.lineTo(padding.left, height - padding.bottom);
    ctx.lineTo(width - padding.right, height - padding.bottom);
    ctx.stroke();

    // Y轴标签
    ctx.fillStyle = '#999';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
        const val = Math.ceil(maxVal * (i / 4));
        const y = height - padding.bottom - chartH * (i / 4);
        ctx.fillText(val.toString(), padding.left - 5, y + 4);
    }

    // 绘制柱状图
    const barWidth = Math.min(15, chartW / dates.length - 2);
    dates.forEach((date, i) => {
        const x = padding.left + (chartW * i / dates.length) + (chartW / dates.length - barWidth) / 2;
        const barH = chartH * (dailyData[date] / maxVal);
        const y = height - padding.bottom - barH;
        
        ctx.fillStyle = '#FFA726';
        ctx.fillRect(x, y, barWidth, barH);
    });
}

// 绘制成长趋势图
function drawGrowthTrendChart(records) {
    const canvas = document.getElementById('reportChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = 250 * dpr;
    ctx.scale(dpr, dpr);
    const width = rect.width;
    const height = 250;

    ctx.clearRect(0, 0, width, height);
    
    const titleEl = document.getElementById('reportChartTitle');
    if (titleEl) titleEl.textContent = '身高体重趋势';

    const padding = { top: 20, right: 30, bottom: 40, left: 50 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    // 按日期排序（旧到新）
    const sorted = [...records].sort((a, b) => new Date(a.record_date) - new Date(b.record_date));
    
    const heights = sorted.map(r => r.height).filter(Boolean);
    const weights = sorted.map(r => r.weight).filter(Boolean);
    
    if (heights.length < 2 && weights.length < 2) {
        ctx.fillStyle = '#999';
        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('需要至少2条记录才能显示趋势', width/2, height/2);
        return;
    }

    const maxH = Math.max(...heights, 1);
    const minH = Math.min(...heights, 0);
    const maxW = Math.max(...weights, 1);
    const minW = Math.min(...weights, 0);

    // 绘制坐标轴
    ctx.strokeStyle = '#E0E0E0';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padding.left, padding.top);
    ctx.lineTo(padding.left, height - padding.bottom);
    ctx.lineTo(width - padding.right, height - padding.bottom);
    ctx.stroke();

    // 身高线（蓝色）
    if (heights.length >= 2) {
        ctx.strokeStyle = '#42A5F5';
        ctx.lineWidth = 2;
        ctx.beginPath();
        sorted.forEach((r, i) => {
            if (!r.height) return;
            const x = padding.left + (chartW * i / Math.max(sorted.length - 1, 1));
            const y = height - padding.bottom - chartH * ((r.height - minH) / (maxH - minH || 1));
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
    }

    // 体重线（绿色）
    if (weights.length >= 2) {
        ctx.strokeStyle = '#66BB6A';
        ctx.lineWidth = 2;
        ctx.beginPath();
        sorted.forEach((r, i) => {
            if (!r.weight) return;
            const x = padding.left + (chartW * i / Math.max(sorted.length - 1, 1));
            const y = height - padding.bottom - chartH * ((r.weight - minW) / (maxW - minW || 1));
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
    }

    // 图例
    ctx.font = '12px sans-serif';
    ctx.fillStyle = '#42A5F5';
    ctx.fillRect(width - 80, 10, 12, 12);
    ctx.fillStyle = '#333';
    ctx.textAlign = 'left';
    ctx.fillText('身高', width - 62, 20);
    ctx.fillStyle = '#66BB6A';
    ctx.fillRect(width - 80, 28, 12, 12);
    ctx.fillStyle = '#333';
    ctx.fillText('体重', width - 62, 38);
}

// ==================== 趴睡训练 ====================
let tummyTimerInterval = null;
let tummyTimerSeconds = 0;
let tummyTimerRunning = false;

function initTummytimePage() {
    document.getElementById('startTummyBtn')?.addEventListener('click', () => {
        // 滚动到计时器区域并开始
        document.querySelector('.tummytime-timer-card')?.scrollIntoView({ behavior: 'smooth' });
        setTimeout(() => startTummyTimer(), 500);
    });
    document.getElementById('addTummyManualBtn')?.addEventListener('click', showTummytimeModal);
    document.getElementById('tummyPlayBtn')?.addEventListener('click', startTummyTimer);
    document.getElementById('tummyPauseBtn')?.addEventListener('click', pauseTummyTimer);
    document.getElementById('tummyStopBtn')?.addEventListener('click', stopTummyTimer);
    document.getElementById('setTummyGoalBtn')?.addEventListener('click', showTummytimeGoalDialog);
    // 加载目标设置
    loadTummytimeGoal();
}

function showTummytimeModal() {
    // 重置表单
    document.getElementById('tummytimeDuration').value = '';
    document.getElementById('tummytimeNote').value = '';
    document.getElementById('tummytimeMilestone').value = '';
    // 设置默认开始时间为当前时间
    const now = new Date();
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    document.getElementById('tummytimeStart').value = local;
    showModal('tummytimeModal');
}

function submitTummytimeRecord() {
    if (!App.currentBaby) return;
    const startTime = document.getElementById('tummytimeStart').value;
    const duration = parseInt(document.getElementById('tummytimeDuration').value) || 0;
    const milestone = document.getElementById('tummytimeMilestone').value;
    const note = document.getElementById('tummytimeNote').value;

    if (!startTime) { showToast.error('请选择开始时间'); return; }
    if (duration <= 0) { showToast.error('请输入训练时长'); return; }

    const data = {
        start_time: startTime.replace('T', ' ') + ':00',
        duration: duration,
        milestone: milestone,
        note: note
    };

    api(`/api/babies/${App.currentBaby}/tummytime`, {
        method: 'POST',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            showToast.success('趴睡训练记录已保存');
            hideModal('tummytimeModal');
            loadTummytimeRecords();
        } else {
            showToast.error(res.message || '保存失败');
        }
    }).catch(err => {
        showToast.error('保存失败: ' + err.message);
    });
}

function startTummyTimer() {
    if (tummyTimerRunning) return;
    tummyTimerRunning = true;
    tummyTimerInterval = setInterval(() => {
        tummyTimerSeconds++;
        updateTummyTimerDisplay();
    }, 1000);
}

function pauseTummyTimer() {
    tummyTimerRunning = false;
    clearInterval(tummyTimerInterval);
}

function stopTummyTimer() {
    if (tummyTimerSeconds > 0) {
        // 保存记录
        if (App.currentBaby) {
            const now = new Date();
            const startTime = new Date(now.getTime() - tummyTimerSeconds * 1000);
            api(`/api/babies/${App.currentBaby}/tummytime`, {
                method: 'POST',
                body: JSON.stringify({
                    start_time: toLocalStr(startTime).slice(0, 19).replace('T', ' '),
                    duration: Math.floor(tummyTimerSeconds / 60),
                    note: ''
                })
            }).then(res => {
                if (res.success) {
                    showToast('趴睡训练记录已保存');
                    loadTummytimeRecords();
                }
            });
        }
    }
    tummyTimerRunning = false;
    clearInterval(tummyTimerInterval);
    tummyTimerSeconds = 0;
    updateTummyTimerDisplay();
}

function updateTummyTimerDisplay() {
    const display = document.getElementById('tummyTimer');
    if (display) {
        const min = Math.floor(tummyTimerSeconds / 60);
        const sec = tummyTimerSeconds % 60;
        display.textContent = `${min.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
    }
}

function loadTummytimeRecords() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/tummytime`).then(res => {
        if (res.success) {
            App.tummytimeRecords = res.data || [];
            renderTummytimeList(App.tummytimeRecords);
            updateTummytimeStats(App.tummytimeRecords);
            updateTummytimeGoalProgress(App.tummytimeRecords);
            drawTummytimeChart(App.tummytimeRecords);
            renderTummytimeMilestones(App.tummytimeRecords);
        }
    }).catch(err => console.error('加载趴睡训练记录失败:', err));
}

function renderTummytimeList(records) {
    const container = document.getElementById('tummytimeList');
    if (!container) return;
    if (records.length === 0) {
        container.innerHTML = '<p class="empty-tip">暂无趴睡训练记录</p>';
        return;
    }
    container.innerHTML = records.map(r => `
        <div class="tummytime-item-card">
            <div class="tummytime-item-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="20" height="20"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
            </div>
            <div class="tummytime-item-info">
                <div class="tummytime-item-date">${escapeHtml(r.start_time)}</div>
                <div class="tummytime-item-duration">时长: ${escapeHtml(String(r.duration_minutes || r.duration || 0))} 分钟</div>
                ${r.milestone ? `<div class="tummytime-item-milestone">🏆 ${escapeHtml(r.milestone)}</div>` : ''}
                ${r.note ? `<div class="tummytime-item-note">${escapeHtml(r.note)}</div>` : ''}
            </div>
            <button class="tummytime-delete" onclick="deleteTummytimeRecord(${r.id})" title="删除">✕</button>
        </div>
    `).join('');
}

function deleteTummytimeRecord(id) {
    if (!confirm('确定要删除这条趴睡训练记录吗？')) return;
    api(`/api/babies/${App.currentBaby}/tummytime/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) loadTummytimeRecords();
    });
}

// ==================== 趴睡训练增强功能 ====================

function loadTummytimeGoal() {
    // 从localStorage加载目标，默认10分钟
    const goalKey = `tummytime_goal_${App.currentBaby}`;
    const goal = parseInt(localStorage.getItem(goalKey)) || 10;
    document.getElementById('tummytimeGoalDisplay').textContent = goal + ' 分钟';
    return goal;
}

function showTummytimeGoalDialog() {
    const currentGoal = loadTummytimeGoal();
    const newGoal = prompt('设置每日训练目标（分钟）:', currentGoal);
    if (newGoal !== null && !isNaN(newGoal) && parseInt(newGoal) > 0) {
        const goalKey = `tummytime_goal_${App.currentBaby}`;
        localStorage.setItem(goalKey, parseInt(newGoal));
        loadTummytimeGoal();
        // 刷新统计和目标进度
        if (App.tummytimeRecords) {
            updateTummytimeGoalProgress(App.tummytimeRecords);
        }
    }
}

function updateTummytimeGoalProgress(records) {
    const goal = loadTummytimeGoal();
    const today = getToday();
    const todayTotal = records
        .filter(r => (r.start_time || '').startsWith(today))
        .reduce((sum, r) => sum + (r.duration_minutes || r.duration || 0), 0);
    const pct = Math.min(100, Math.round((todayTotal / goal) * 100));
    const fill = document.getElementById('tummytimeGoalFill');
    const text = document.getElementById('tummytimeGoalText');
    if (fill) fill.style.width = pct + '%';
    if (text) text.textContent = `已完成 ${todayTotal}/${goal} 分钟 (${pct}%)`;
}

function drawTummytimeChart(records) {
    const canvas = document.getElementById('tummytimeChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = 200 * dpr;
    ctx.scale(dpr, dpr);
    const w = rect.width;
    const h = 200;

    // 清空画布
    ctx.clearRect(0, 0, w, h);

    // 计算7天数据
    const days = [];
    const today = new Date();
    for (let i = 6; i >= 0; i--) {
        const d = new Date(today);
        d.setDate(d.getDate() - i);
        days.push(formatDate(d));
    }

    const dayData = days.map(day => {
        return records
            .filter(r => (r.start_time || '').startsWith(day))
            .reduce((sum, r) => sum + (r.duration_minutes || r.duration || 0), 0);
    });

    const maxVal = Math.max(...dayData, 10);
    const padding = { top: 20, right: 20, bottom: 30, left: 35 };
    const chartW = w - padding.left - padding.right;
    const chartH = h - padding.top - padding.bottom;
    const barW = chartW / 7 * 0.6;
    const gap = chartW / 7;

    // 绘制网格线
    ctx.strokeStyle = '#e5e7eb';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
        const y = padding.top + (chartH / 4) * i;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(w - padding.right, y);
        ctx.stroke();
        // Y轴标签
        ctx.fillStyle = '#6b7280';
        ctx.font = '11px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(Math.round(maxVal - (maxVal / 4) * i) + '分', padding.left - 5, y + 4);
    }

    // 绘制柱状图
    dayData.forEach((val, i) => {
        const barH = (val / maxVal) * chartH;
        const x = padding.left + gap * i + (gap - barW) / 2;
        const y = padding.top + chartH - barH;

        // 柱子
        const gradient = ctx.createLinearGradient(x, y, x, y + barH);
        gradient.addColorStop(0, '#6366f1');
        gradient.addColorStop(1, '#a5b4fc');
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.roundRect(x, y, barW, barH, [4, 4, 0, 0]);
        ctx.fill();

        // 数值标签
        if (val > 0) {
            ctx.fillStyle = '#374151';
            ctx.font = 'bold 11px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(val, x + barW / 2, y - 5);
        }

        // X轴标签（月-日）
        const dayLabel = days[i].slice(5);
        ctx.fillStyle = '#6b7280';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(dayLabel, x + barW / 2, h - 10);
    });
}

function updateTummytimeStats(records) {
    const today = getToday();
    const todayTotal = records
        .filter(r => (r.start_time || '').startsWith(today))
        .reduce((sum, r) => sum + (r.duration_minutes || r.duration || 0), 0);

    const weekAgo = new Date();
    weekAgo.setDate(weekAgo.getDate() - 7);
    const weekTotal = records
        .filter(r => new Date(r.start_time.replace(' ', 'T')) >= weekAgo)
        .reduce((sum, r) => sum + (r.duration_minutes || r.duration || 0), 0);

    const monthAgo = new Date();
    monthAgo.setMonth(monthAgo.getMonth() - 1);
    const monthTotal = records
        .filter(r => new Date(r.start_time.replace(' ', 'T')) >= monthAgo)
        .reduce((sum, r) => sum + (r.duration_minutes || r.duration || 0), 0);

    // 计算连续天数
    let streak = 0;
    const checkDate = new Date();
    while (true) {
        const dateStr = formatDate(checkDate);
        const hasRecord = records.some(r => (r.start_time || '').startsWith(dateStr));
        if (hasRecord) {
            streak++;
            checkDate.setDate(checkDate.getDate() - 1);
        } else {
            break;
        }
    }

    // 更新DOM
    const elToday = document.getElementById('statTodayMin');
    const elWeek = document.getElementById('statWeekMin');
    const elMonth = document.getElementById('statMonthMin');
    const elStreak = document.getElementById('statStreakDay');
    if (elToday) elToday.textContent = todayTotal;
    if (elWeek) elWeek.textContent = weekTotal;
    if (elMonth) elMonth.textContent = monthTotal;
    if (elStreak) elStreak.textContent = streak;
}

function renderTummytimeMilestones(records) {
    const container = document.getElementById('tummytimeMilestoneGrid');
    if (!container) return;

    const milestones = [
        { key: '首次抬头', icon: '🎉', label: '首次抬头' },
        { key: '保持30秒', icon: '⏱', label: '保持30秒' },
        { key: '保持1分钟', icon: '💪', label: '保持1分钟' },
        { key: '保持3分钟', icon: '🔥', label: '保持3分钟' },
        { key: '保持5分钟', icon: '⭐', label: '保持5分钟' },
        { key: '伸手够物', icon: '🤲', label: '伸手够物' },
        { key: '翻身尝试', icon: '🔄', label: '翻身尝试' },
        { key: '独自支撑', icon: '🏆', label: '独自支撑' },
    ];

    const achieved = new Set(records.filter(r => r.milestone).map(r => r.milestone));
    container.innerHTML = milestones.map(m => `
        <div class="tummytime-milestone-item ${achieved.has(m.key) ? 'achieved' : ''}">
            <div class="tummytime-milestone-icon">${m.icon}</div>
            <div class="tummytime-milestone-label">${m.label}</div>
        </div>
    `).join('');
}

// ==================== 辅食过敏测试 ====================
function initAllergyPage() {
    document.getElementById('addAllergyBtn2')?.addEventListener('click', showAddAllergyModal);
}

function loadAllergyTests() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/allergy-tests`).then(res => {
        if (res.success) {
            App.allergyTests = res.data;
            renderAllergyList(res.data);
        }
    });
}

function renderAllergyList(records) {
    // 两个页面共用这套渲染：健康档案页（allergyListTab）与辅食过敏测试页（allergyList2）
    const containers = ['allergyListTab', 'allergyList2']
        .map(id => document.getElementById(id))
        .filter(Boolean);
    if (containers.length === 0) return;
    if (records.length === 0) {
        containers.forEach(c => { c.innerHTML = '<p class="empty-tip">暂无过敏测试记录</p>'; });
        return;
    }
    const severityNames = { none: '无症状', mild: '轻微', moderate: '中度', severe: '严重' };
    const html = records.map(r => `
        <div class="allergy-item-card">
            <div class="allergy-item-header">
                <span class="allergy-item-food">${escapeHtml(r.food_name)}</span>
                <span class="allergy-item-date">${r.test_date}</span>
                <span class="allergy-item-severity ${escapeHtml(r.reaction)}">${severityNames[r.reaction] || escapeHtml(r.reaction)}</span>
            </div>
            ${r.symptoms ? `<div class="allergy-item-symptoms">症状: ${escapeHtml(r.symptoms)}</div>` : ''}
            ${r.note ? `<div class="allergy-item-symptoms">备注: ${escapeHtml(r.note)}</div>` : ''}
            <button class="allergy-delete" onclick="deleteAllergyTest(${r.id})">X</button>
        </div>
    `).join('');
    containers.forEach(c => { c.innerHTML = html; });
}

function showAddAllergyModal() {
    const today = getToday();
    const formHtml = `
        <div class="form-group">
            <label>食物名称</label>
            <input type="text" id="allergyFood" placeholder="如: 鸡蛋、牛奶">
        </div>
        <div class="form-group">
            <label>测试日期</label>
            <input type="date" id="allergyDate" value="${today}">
        </div>
        <div class="form-group">
            <label>反应程度</label>
            <select id="allergyReaction">
                <option value="none">无症状</option>
                <option value="mild">轻微</option>
                <option value="moderate">中度</option>
                <option value="severe">严重</option>
            </select>
        </div>
        <div class="form-group">
            <label>症状描述</label>
            <textarea id="allergySymptoms" rows="2" placeholder="如: 皮疹、呕吐、腹泻"></textarea>
        </div>
        <div class="form-group">
            <label>备注</label>
            <textarea id="allergyNote" rows="2" placeholder="其他备注"></textarea>
        </div>
    `;
    showModal('allergyModal');
    const body = document.getElementById('allergyModalBody');
    if (body) body.innerHTML = formHtml;
}

function submitAllergyTest() {
    const data = {
        food_name: document.getElementById('allergyFood')?.value,
        test_date: document.getElementById('allergyDate')?.value,
        reaction: document.getElementById('allergyReaction')?.value || 'none',
        severity: document.getElementById('allergyReaction')?.value || 'none',
        symptoms: document.getElementById('allergySymptoms')?.value || '',
        note: document.getElementById('allergyNote')?.value || '',
    };
    if (!data.food_name) { alert('请输入食物名称'); return; }
    
    api(`/api/babies/${App.currentBaby}/allergy-tests`, { method: 'POST', body: JSON.stringify(data) }).then(res => {
        if (res.success) {
            hideModal('allergyModal');
            loadAllergyTests();
        }
    });
}

function deleteAllergyTest(id) {
    if (!confirm('确定要删除这条过敏测试记录吗？')) return;
    api(`/api/babies/${App.currentBaby}/allergy-tests/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) loadAllergyTests();
    });
}

// ==================== 比价记账（多品类用品比价 + 支出记账） ====================

// 品类配置：label 显示名 / units 单位选项 / specHint 规格提示
const SHOP_CATEGORIES = {
    diaper:  { label: '纸尿裤', units: ['片'],           specHint: '尺码: NB/S/M/L/XL/XXL' },
    formula: { label: '奶粉',   units: ['g', 'ml', '罐'], specHint: '段位: 1段/2段/3段' },
    wipes:   { label: '湿巾',   units: ['片', '包'],      specHint: '规格: 80片/100抽' },
    food:    { label: '辅食',   units: ['g', 'ml', '包', '罐'], specHint: '规格: 225g/袋装' },
    care:    { label: '洗护',   units: ['ml', '瓶', '支'], specHint: '规格: 200ml' },
    toy:     { label: '玩具',   units: ['件', '套'],      specHint: '规格/适合月龄' },
    other:   { label: '其他',   units: ['件', '个', '套'], specHint: '规格' },
};

// 记账支出分类
const EXPENSE_CATEGORIES = {
    diaper:    '纸尿裤',
    formula:   '奶粉辅食',
    daily:     '日常用品',
    medical:   '医疗健康',
    toy:       '玩具绘本',
    education: '早教教育',
    travel:    '出行游玩',
    other:     '其他',
};

const EXPENSE_CAT_ICONS = {
    diaper:    '🧷',
    formula:   '🍼',
    daily:     '🧴',
    medical:   '💊',
    toy:       '🧸',
    education: '📚',
    travel:    '🚗',
    other:     '📦',
};

const EXPENSE_CAT_COLORS = {
    diaper:    '#6366f1',
    formula:   '#f59e0b',
    daily:     '#10b981',
    medical:   '#ef4444',
    toy:       '#8b5cf6',
    education: '#06b6d4',
    travel:    '#f97316',
    other:     '#6b7280',
};

// 页面状态
const shoppingState = {
    tab: 'compare',       // compare | ledger
    category: '',         // 比价筛选品类
    spec: '',
    brand: '',
    ledgerMonth: null,    // 记账当前月 YYYY-MM，null=当月
    editingPriceId: null, // 正在编辑的价格记录 id
    records: [],          // 当前比价列表缓存（编辑时直接取，不必再拉全量）
};

function initShoppingPage() {
    document.getElementById('addShoppingBtn')?.addEventListener('click', () => {
        if (shoppingState.tab === 'compare') showPriceModal();
        else showExpenseModal();
    });
    document.getElementById('shoppingSpecFilter')?.addEventListener('input', debounce(() => {
        shoppingState.spec = document.getElementById('shoppingSpecFilter').value.trim();
        loadProductPrices();
    }, 300));
    document.getElementById('shoppingBrandFilter')?.addEventListener('input', debounce(() => {
        shoppingState.brand = document.getElementById('shoppingBrandFilter').value.trim();
        loadProductPrices();
    }, 300));
    document.querySelectorAll('#shoppingCatChips .cat-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            document.querySelectorAll('#shoppingCatChips .cat-chip').forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            shoppingState.category = chip.dataset.cat || '';
            loadProductPrices();
        });
    });
    // 面板切换
    document.querySelectorAll('.shopping-tab').forEach(tab => {
        tab.addEventListener('click', () => switchShoppingTab(tab.dataset.shoppingTab));
    });
    // 记账月份切换
    document.getElementById('ledgerPrevMonth')?.addEventListener('click', () => shiftLedgerMonth(-1));
    document.getElementById('ledgerNextMonth')?.addEventListener('click', () => shiftLedgerMonth(1));
    document.getElementById('addExpenseBtn')?.addEventListener('click', showExpenseModal);
}

function switchShoppingTab(tab) {
    shoppingState.tab = tab;
    document.querySelectorAll('.shopping-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.shoppingTab === tab);
    });
    document.getElementById('shoppingComparePane').style.display = tab === 'compare' ? '' : 'none';
    document.getElementById('shoppingLedgerPane').style.display = tab === 'ledger' ? '' : 'none';
    if (tab === 'compare') loadProductPrices();
    else loadLedger();
}

function loadShoppingPage() {
    if (shoppingState.tab === 'compare') loadProductPrices();
    else loadLedger();
}

/* ---------- 用品比价 ---------- */

function loadProductPrices() {
    let url = '/api/product-prices?';
    if (shoppingState.category) url += `category=${shoppingState.category}&`;
    if (shoppingState.spec) url += `spec=${encodeURIComponent(shoppingState.spec)}&`;
    if (shoppingState.brand) url += `brand=${encodeURIComponent(shoppingState.brand)}&`;

    api(url).then(res => {
        if (res.success) {
            shoppingState.records = res.data || [];
            renderProductRank(shoppingState.records);
        }
    });
}

function renderProductRank(records) {
    const container = document.getElementById('shoppingPriceList');
    if (!container) return;
    if (records.length === 0) {
        container.innerHTML = '<p class="empty-tip">暂无价格记录，点右上角"＋ 添加"开始比价</p>';
        return;
    }

    // 按品类分组：单价只在「同品类 + 同单位」内可比。
    // 旧版把全库按单价升序排、跨品类标"最划算"，湿巾 ¥0.05/片 会压过纸尿裤 ¥1.2/片，
    // 标出来的冠军跟用户当前想买的品类毫无关系。
    const groups = new Map();
    records.forEach(r => {
        const key = r.category || 'other';
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(r);
    });

    container.innerHTML = Array.from(groups.entries()).map(([catKey, items]) => {
        const cat = SHOP_CATEGORIES[catKey] || { label: catKey };
        // 组内至少有两条同单位记录，才存在真正能比的价
        const comparable = items.some(i => (i.peer_count || 0) >= 2);
        const sub = items.length + ' 条' + (comparable ? '' : ' · 暂无同规格可比');
        return `
        <div class="price-group">
            <div class="price-group-head">
                <span class="price-group-name">${escapeHtml(cat.label)}</span>
                <span class="price-group-count">${sub}</span>
            </div>
            ${items.map(renderPriceCard).join('')}
        </div>`;
    }).join('');
}

function renderPriceCard(r) {
    const isBest = !!r.is_best;
    const unit = escapeHtml(r.unit || '件');
    const unitPriceText = r.unit_price != null ? `¥${r.unit_price.toFixed(2)}/${unit}` : '单价待填';

    // 差价提示只在组内真有可比对象时给：单独一条记录谈不上"贵了/便宜了"
    let delta = '';
    if (r.unit_price != null && (r.peer_count || 0) >= 2 && r.vs_best_pct != null) {
        delta = r.vs_best_pct <= 0
            ? '<span class="price-delta is-low">同规格最低</span>'
            : `<span class="price-delta">比同规格最低贵 ${r.vs_best_pct}%</span>`;
    }

    return `
    <div class="diaper-price-item-card ${isBest ? 'price-best' : ''}">
        ${isBest ? '<span class="price-best-badge">🏆 最划算</span>' : ''}
        <div class="diaper-price-item-header">
            <span class="diaper-price-brand">${escapeHtml(r.brand)}${r.series ? ' ' + escapeHtml(r.series) : ''}</span>
            <span class="diaper-price-unit">${unitPriceText}</span>
        </div>
        <div class="diaper-price-details">
            ${r.spec ? `<span class="diaper-price-detail">${escapeHtml(r.spec)}</span>` : ''}
            ${r.package_size > 0 ? `<span class="diaper-price-detail">${r.package_size}${unit}/包</span>` : ''}
            <span class="diaper-price-detail">¥${escapeHtml(String(r.price))}</span>
            ${r.purchase_channel ? `<span class="diaper-price-detail">${escapeHtml(r.purchase_channel)}</span>` : ''}
            ${r.purchase_date ? `<span class="diaper-price-detail">${escapeHtml(r.purchase_date)}</span>` : ''}
        </div>
        ${delta}
        ${r.note ? `<div class="diaper-price-note">${escapeHtml(r.note)}</div>` : ''}
        <div class="diaper-price-actions">
            <button class="price-action-btn" data-price-action="quick" data-brand="${escapeHtml(r.brand)}" data-price="${r.price}" data-cat="${escapeHtml(r.category || 'other')}">记一笔</button>
            <button class="price-action-btn" onclick="editPriceRecord(${r.id})">编辑</button>
            <button class="price-action-btn price-action-del" onclick="deletePriceRecord(${r.id})">删除</button>
        </div>
    </div>`;
}

function specInputHint(category) {
    return (SHOP_CATEGORIES[category] || {}).specHint || '规格';
}

function unitOptions(category, selected) {
    const units = (SHOP_CATEGORIES[category] || {}).units || ['件'];
    return units.map(u => `<option value="${u}" ${u === selected ? 'selected' : ''}>${u}</option>`).join('');
}

function categoryOptions(selected) {
    return Object.entries(SHOP_CATEGORIES).map(([k, v]) =>
        `<option value="${k}" ${k === selected ? 'selected' : ''}>${v.label}</option>`).join('');
}

function showPriceModal(existing) {
    shoppingState.editingPriceId = existing ? existing.id : null;
    const cat = existing ? existing.category : (shoppingState.category || 'diaper');
    const today = getToday();
    document.getElementById('shoppingModalTitle').textContent = existing ? '编辑价格记录' : '添加价格记录';
    document.getElementById('shoppingModalBody').innerHTML = `
        <div class="form-group">
            <label>品类</label>
            <select id="spCategory">${categoryOptions(cat)}</select>
        </div>
        <div class="form-group">
            <label>品牌 *</label>
            <input type="text" id="spBrand" maxlength="50" placeholder="如: 花王、帮宝适、爱他美" value="${existing ? escapeHtml(existing.brand) : ''}">
        </div>
        <div class="form-group">
            <label>系列</label>
            <input type="text" id="spSeries" maxlength="50" placeholder="如: Merries、一级帮、卓萃" value="${existing ? escapeHtml(existing.series || '') : ''}">
        </div>
        <div class="form-group">
            <label>规格</label>
            <input type="text" id="spSpec" maxlength="30" placeholder="${specInputHint(cat)}" value="${existing ? escapeHtml(existing.spec || '') : ''}">
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>包装量 *</label>
                <input type="number" id="spSize" min="0" step="0.01" placeholder="如: 54" value="${existing && existing.package_size > 0 ? existing.package_size : ''}">
            </div>
            <div class="form-group">
                <label>单位</label>
                <select id="spUnit">${unitOptions(cat, existing ? existing.unit : '片')}</select>
            </div>
        </div>
        <div class="form-group">
            <label>价格 (元) *</label>
            <input type="number" id="spPrice" min="0" step="0.01" placeholder="如: 109" value="${existing ? existing.price : ''}">
        </div>
        <div class="form-group">
            <label>购买渠道</label>
            <input type="text" id="spChannel" maxlength="50" placeholder="如: 天猫、京东、山姆" value="${existing ? escapeHtml(existing.purchase_channel || '') : ''}">
        </div>
        <div class="form-group">
            <label>购买日期</label>
            <input type="date" id="spDate" value="${existing && existing.purchase_date ? existing.purchase_date : today}">
        </div>
        <div class="form-group">
            <label>备注</label>
            <textarea id="spNote" rows="2" maxlength="200" placeholder="使用感受、活动信息等">${existing ? escapeHtml(existing.note || '') : ''}</textarea>
        </div>
        ${!existing ? `<label class="sp-also-expense"><input type="checkbox" id="spAlsoExpense" checked> 同时记一笔支出</label>` : ''}
    `;
    // 品类切换时联动单位选项与规格提示
    document.getElementById('spCategory')?.addEventListener('change', function() {
        document.getElementById('spUnit').innerHTML = unitOptions(this.value, SHOP_CATEGORIES[this.value].units[0]);
        document.getElementById('spSpec').placeholder = specInputHint(this.value);
    });
    showModal('shoppingModal');
}

function collectPriceForm() {
    return {
        category: document.getElementById('spCategory')?.value || 'other',
        brand: document.getElementById('spBrand')?.value.trim(),
        series: document.getElementById('spSeries')?.value.trim() || '',
        spec: document.getElementById('spSpec')?.value.trim() || '',
        package_size: parseFloat(document.getElementById('spSize')?.value) || 0,
        unit: document.getElementById('spUnit')?.value || '件',
        price: parseFloat(document.getElementById('spPrice')?.value) || 0,
        purchase_channel: document.getElementById('spChannel')?.value.trim() || '',
        purchase_date: document.getElementById('spDate')?.value || '',
        note: document.getElementById('spNote')?.value.trim() || '',
    };
}

function submitPriceRecord() {
    const data = collectPriceForm();
    if (!data.brand) { showToast('请输入品牌'); return; }
    if (data.price <= 0) { showToast('请输入正确价格'); return; }

    const editingId = shoppingState.editingPriceId;
    const req = editingId
        ? api(`/api/product-prices/${editingId}`, { method: 'PUT', body: JSON.stringify(data) })
        : api('/api/product-prices', { method: 'POST', body: JSON.stringify(data) });

    req.then(res => {
        if (res.success) {
            // 非编辑模式且勾选"同时记账"→ 创建对应支出
            const alsoExpense = !editingId && document.getElementById('spAlsoExpense')?.checked;
            if (alsoExpense) {
                api('/api/expenses', {
                    method: 'POST',
                    body: JSON.stringify({
                        category: data.category,
                        amount: data.price,
                        item_name: `${data.brand}${data.series ? ' ' + data.series : ''}${data.spec ? ' ' + data.spec : ''}`,
                        purchase_channel: data.purchase_channel,
                        expense_date: data.purchase_date || getToday(),
                        note: '由比价记录生成',
                    }),
                });
            }
            hideModal('shoppingModal');
            shoppingState.editingPriceId = null;
            showToast(editingId ? '已更新' : '已添加' + (alsoExpense ? '并记账' : ''));
            if (shoppingState.tab === 'compare') loadProductPrices();
        } else {
            showToast(res.message || '保存失败');
        }
    });
}

function editPriceRecord(id) {
    // 优先用当前列表缓存，省掉一次"为了编辑一条而拉全量"的请求
    const cached = (shoppingState.records || []).find(r => r.id === id);
    if (cached) { showPriceModal(cached); return; }
    api('/api/product-prices').then(res => {
        const record = (res.data || []).find(r => r.id === id);
        if (record) showPriceModal(record);
        else showToast('记录不存在，可能已被删除');
    });
}

function deletePriceRecord(id) {
    if (!confirm('确定要删除这条价格记录吗？')) return;
    api(`/api/product-prices/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast('已删除');
            loadProductPrices();
        }
    });
}

// 比价卡片快捷记账：预填品名/金额/分类后打开记账弹窗
// 购物价格/台账按钮事件委托：data-* 取代内联 onclick 拼接
// （HTML 属性中的实体在 JS 解析前会被解码，escapeHtml 在 onclick 内不防注入）
document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-price-action]');
    if (!btn) return;
    const d = btn.dataset;
    if (d.priceAction === 'quick') {
        quickExpenseFromPrice(d.brand || '', parseFloat(d.price) || 0, d.cat || 'other');
    } else if (d.priceAction === 'edit-expense') {
        showExpenseModal({
            id: d.expId ? parseInt(d.expId, 10) : undefined,
            category: d.cat || '',
            amount: parseFloat(d.amount) || 0,
            item_name: d.item || '',
            expense_date: d.date || '',
            purchase_channel: d.channel || '',
            note: d.note || '',
        });
    }
});

function quickExpenseFromPrice(brand, price, category) {
    showExpenseModal({
        item_name: brand,
        amount: price,
        category: category,
        expense_date: getToday(),
    });
}

/* ---------- 记账本 ---------- */

function shiftLedgerMonth(delta) {
    if (!shoppingState.ledgerMonth) {
        shoppingState.ledgerMonth = getToday().slice(0, 7);
    }
    let [y, m] = shoppingState.ledgerMonth.split('-').map(Number);
    m += delta;
    if (m === 0) { y--; m = 12; }
    if (m === 13) { y++; m = 1; }
    shoppingState.ledgerMonth = `${y}-${String(m).padStart(2, '0')}`;
    loadLedger();
}

function loadLedger() {
    const month = shoppingState.ledgerMonth || getToday().slice(0, 7);
    document.getElementById('ledgerMonthLabel').textContent = month.replace('-', '年') + '月';

    Promise.all([
        api(`/api/expenses?month=${month}`),
        api(`/api/expenses/summary?month=${month}`),
    ]).then(([listRes, sumRes]) => {
        if (sumRes.success) renderLedgerSummary(sumRes.data);
        if (listRes.success) renderLedgerList(listRes.data || []);
    });
}

function renderLedgerSummary(summary) {
    document.getElementById('ledgerMonthTotal').textContent = `¥${(summary.month_total || 0).toFixed(2)}`;

    // 环比上月：趋势数组最后一格就是当前查看的月份，倒数第二格是上月
    const trendAll = summary.trend || [];
    const deltaEl = document.getElementById('ledgerMonthDelta');
    if (deltaEl) {
        const prev = trendAll.length >= 2 ? trendAll[trendAll.length - 2].total : 0;
        const cur = trendAll.length ? trendAll[trendAll.length - 1].total : 0;
        if (!prev) {
            deltaEl.textContent = cur > 0 ? '上月无记录' : '';
            deltaEl.className = 'ledger-delta';
        } else {
            const pct = Math.round((cur - prev) / prev * 100);
            if (pct === 0) {
                deltaEl.textContent = `与上月持平（¥${prev.toFixed(2)}）`;
                deltaEl.className = 'ledger-delta';
            } else if (pct > 0) {
                deltaEl.textContent = `比上月多花 ${pct}%（上月 ¥${prev.toFixed(2)}）`;
                deltaEl.className = 'ledger-delta is-up';
            } else {
                deltaEl.textContent = `比上月少花 ${Math.abs(pct)}%（上月 ¥${prev.toFixed(2)}）`;
                deltaEl.className = 'ledger-delta is-down';
            }
        }
    }

    // 分类占比条（带颜色）
    const catsEl = document.getElementById('ledgerCatBars');
    const cats = summary.by_category || [];
    if (!cats.length) {
        catsEl.innerHTML = '<p class="ledger-cats-empty">本月暂无分类统计</p>';
    } else {
        const maxTotal = Math.max(...cats.map(c => c.total));
        catsEl.innerHTML = cats.map(c => {
            const color = EXPENSE_CAT_COLORS[c.category] || '#6b7280';
            const icon = EXPENSE_CAT_ICONS[c.category] || '📦';
            return `
            <div class="ledger-cat-row">
                <span class="ledger-cat-name">${icon} ${escapeHtml(c.label)}</span>
                <div class="ledger-cat-bar-wrap"><div class="ledger-cat-bar" style="width:${Math.max(8, Math.round(c.total / maxTotal * 100))}%;background:${color}"></div></div>
                <span class="ledger-cat-total">¥${c.total.toFixed(2)} (${c.count}笔)</span>
            </div>`;
        }).join('');
    }

    // 近 6 个月趋势柱（带颜色区分当前月）
    const trendEl = document.getElementById('ledgerTrendBars');
    const trend = summary.trend || [];
    const maxTrend = Math.max(...trend.map(t => t.total), 1);
    const curMonth = shoppingState.ledgerMonth || getToday().slice(0, 7);
    trendEl.innerHTML = trend.map(t => {
        const h = Math.max(6, Math.round(t.total / maxTrend * 72));
        const isCur = t.month === curMonth;
        const barColor = isCur ? 'linear-gradient(to top, #f59e0b, #fbbf24)' : 'linear-gradient(to top, #6366f1, #a5b4fc)';
        return `
        <div class="trend-col">
            <span class="trend-value">${t.total > 0 ? Math.round(t.total) : ''}</span>
            <div class="trend-bar ${isCur ? 'trend-bar-cur' : ''}" style="height:${h}px;background:${barColor}"></div>
            <span class="trend-month">${t.month.slice(5)}月</span>
        </div>`;
    }).join('');
}

function renderLedgerList(records) {
    const container = document.getElementById('ledgerList');
    if (!container) return;
    if (records.length === 0) {
        container.innerHTML = '<p class="empty-tip">本月暂无支出记录<br><small>点击"＋ 记一笔"开始记账</small></p>';
        return;
    }
    container.innerHTML = records.map(r => {
        const label = EXPENSE_CATEGORIES[r.category] || r.category;
        const icon = EXPENSE_CAT_ICONS[r.category] || '📦';
        const color = EXPENSE_CAT_COLORS[r.category] || '#6b7280';
        return `
        <div class="ledger-item">
            <div class="ledger-item-cat" style="background:${color}1a;color:${color}">${icon} ${escapeHtml(label)}</div>
            <div class="ledger-item-info">
                <span class="ledger-item-name">${escapeHtml(r.item_name || '未命名支出')}</span>
                <span class="ledger-item-meta">${escapeHtml(r.expense_date)}${r.purchase_channel ? ' · ' + escapeHtml(r.purchase_channel) : ''}${r.note ? ' · ' + escapeHtml(r.note) : ''}</span>
            </div>
            <span class="ledger-item-amount">¥${Number(r.amount).toFixed(2)}</span>
            <button class="price-action-btn" data-price-action="edit-expense" data-exp-id="${r.id}" data-cat="${escapeHtml(r.category || '')}" data-amount="${r.amount}" data-item="${escapeHtml(r.item_name || '')}" data-date="${escapeHtml(r.expense_date || '')}" data-channel="${escapeHtml(r.purchase_channel || '')}" data-note="${escapeHtml(r.note || '')}" title="编辑">✎</button>
            <button class="price-action-btn price-action-del" onclick="deleteExpenseRecord(${r.id})" title="删除">✕</button>
        </div>`;
    }).join('');
}

function showExpenseModal(prefill) {
    const today = getToday();
    const month = shoppingState.ledgerMonth || getToday().slice(0, 7);
    const defaultDate = prefill ? (prefill.expense_date || today) : (month === getToday().slice(0, 7) ? today : month + '-01');
    const isEdit = prefill && prefill.id;

    const catGrid = Object.entries(EXPENSE_CATEGORIES).map(([k, v]) => `
        <button type="button" class="expense-cat-btn ${prefill && prefill.category === k ? 'selected' : ''}" data-cat="${k}">
            <span class="exp-cat-icon">${EXPENSE_CAT_ICONS[k]}</span>
            <span>${v}</span>
        </button>
    `).join('');

    document.getElementById('expenseModalBody').innerHTML = `
        <input type="hidden" id="expenseEditId" value="${isEdit ? prefill.id : ''}">
        <div class="form-group">
            <label>支出分类 *</label>
            <div class="expense-cat-grid">${catGrid}</div>
            <input type="hidden" id="expCategory" value="${prefill ? prefill.category || '' : ''}">
        </div>
        <div class="form-group">
            <label>金额 (元) *</label>
            <input type="number" id="expAmount" min="0" step="0.01" placeholder="如: 109.00" value="${prefill ? prefill.amount || '' : ''}">
        </div>
        <div class="form-group">
            <label>品名</label>
            <input type="text" id="expItem" maxlength="100" placeholder="如: 花王 L码 1包" value="${prefill ? escapeHtml(prefill.item_name || '') : ''}">
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>日期 *</label>
                <input type="date" id="expDate" value="${defaultDate}">
            </div>
            <div class="form-group">
                <label>购买渠道</label>
                <input type="text" id="expChannel" maxlength="50" placeholder="如: 天猫" value="${prefill ? escapeHtml(prefill.purchase_channel || '') : ''}">
            </div>
        </div>
        <div class="form-group">
            <label>备注</label>
            <textarea id="expNote" rows="2" maxlength="200" placeholder="选填">${prefill ? escapeHtml(prefill.note || '') : ''}</textarea>
        </div>
    `;

    // 绑定分类选择
    document.getElementById('expenseModalBody').querySelectorAll('.expense-cat-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.getElementById('expenseModalBody').querySelectorAll('.expense-cat-btn').forEach(b => b.classList.remove('selected'));
            btn.classList.add('selected');
            document.getElementById('expCategory').value = btn.dataset.cat;
        });
    });

    // 更新弹窗标题
    const titleEl = document.querySelector('#expenseModal .modal-header h2');
    if (titleEl) titleEl.textContent = isEdit ? '编辑支出' : '记一笔支出';

    showModal('expenseModal');
}

function submitExpenseRecord() {
    const editId = document.getElementById('expenseEditId')?.value;
    const data = {
        category: document.getElementById('expCategory')?.value || 'other',
        amount: parseFloat(document.getElementById('expAmount')?.value) || 0,
        item_name: document.getElementById('expItem')?.value.trim() || '',
        expense_date: document.getElementById('expDate')?.value || '',
        purchase_channel: document.getElementById('expChannel')?.value.trim() || '',
        note: document.getElementById('expNote')?.value.trim() || '',
    };
    if (data.amount <= 0) { showToast('请输入正确金额'); return; }
    if (!data.expense_date) { showToast('请选择日期'); return; }
    if (!data.category) { showToast('请选择支出分类'); return; }

    const url = editId ? `/api/expenses/${editId}` : '/api/expenses';
    const method = editId ? 'PUT' : 'POST';

    api(url, { method: method, body: JSON.stringify(data) }).then(res => {
        if (res.success) {
            hideModal('expenseModal');
            showToast(editId ? '已更新' : '已记账');
            if (shoppingState.tab === 'ledger') loadLedger();
        } else {
            showToast(res.message || '保存失败');
        }
    });
}

function deleteExpenseRecord(id) {
    if (!confirm('确定要删除这条支出记录吗？')) return;
    api(`/api/expenses/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) {
            showToast('已删除');
            loadLedger();
        }
    });
}

// ==================== 用药提醒 ====================
function initMedReminderPage() {
    document.getElementById('addMedReminderBtn')?.addEventListener('click', showAddMedReminderModal);
}

function loadMedReminders() {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/medication-reminders`).then(res => {
        if (res.success) {
            App.medReminders = res.data;
            renderMedReminders(res.data);
        }
    });
}

function renderMedReminders(records) {
    const activeContainer = document.getElementById('activeRemindersList');
    const allContainer = document.getElementById('allRemindersList');
    const statsEl = document.getElementById('medReminderStats');
    if (!activeContainer || !allContainer) return;

    const active = records.filter(r => r.is_active);
    const inactive = records.filter(r => !r.is_active);

    // 更新统计
    if (statsEl) {
        statsEl.innerHTML = `
            <div class="stat-chip">活跃 <strong>${active.length}</strong> 个</div>
            <div class="stat-chip">全部 <strong>${records.length}</strong> 个</div>
        `;
        statsEl.style.display = records.length > 0 ? 'flex' : 'none';
    }

    if (records.length === 0) {
        activeContainer.innerHTML = '<p class="empty-tip">暂无活跃用药提醒<br><small>添加提醒后系统会按时推送通知</small></p>';
        allContainer.innerHTML = '';
        return;
    }

    activeContainer.innerHTML = active.length > 0 ? active.map(r => renderMedReminderCard(r)).join('') : '<p class="empty-tip">暂无活跃提醒</p>';
    allContainer.innerHTML = records.map(r => renderMedReminderCard(r)).join('');
}

function renderMedReminderCard(r) {
    const nextTime = r.next_dose_time ? new Date(r.next_dose_time).toLocaleString('zh-CN') : '未设置';
    return `
        <div class="med-reminder-item-card ${r.is_active ? '' : 'inactive'}">
            <button class="med-reminder-delete" onclick="deleteMedReminder(${r.id})">✕</button>
            <button class="care-edit med-reminder-edit" onclick="editMedReminder(${r.id})">✎</button>
            <div class="med-reminder-item-header">
                <span class="med-reminder-name">${escapeHtml(r.medication_name)}</span>
                <span class="med-reminder-dosage">${escapeHtml(r.dosage)}</span>
            </div>
            <div class="med-reminder-time">下次用药: ${nextTime}</div>
            ${r.frequency ? `<div class="med-reminder-dosage">频率: ${escapeHtml(r.frequency)}</div>` : ''}
            ${r.note ? `<div class="med-reminder-note">${escapeHtml(r.note)}</div>` : ''}
            <div class="med-reminder-actions">
                <button class="med-reminder-action-btn" onclick="toggleMedReminder(${r.id}, ${r.is_active ? 0 : 1})">${r.is_active ? '暂停' : '启用'}</button>
                <button class="med-reminder-action-btn" onclick="takeMedNow(${r.id})">已服药</button>
            </div>
        </div>
    `;
}

function editMedReminder(id) {
    if (!App.currentBaby) return;
    api(`/api/babies/${App.currentBaby}/medication-reminders/${id}`).then(res => {
        if (!res.success) return showToast.error('加载失败');
        const r = res.data;
        showModal(`
            <div class="modal-header"><h3>编辑用药提醒</h3></div>
            <div class="modal-body">
                <div class="form-group">
                    <label>药物名称</label>
                    <input type="text" id="editMedName" value="${escapeHtml(r.medication_name || '')}">
                </div>
                <div class="form-group">
                    <label>剂量</label>
                    <input type="text" id="editMedDosage" value="${escapeHtml(r.dosage || '')}">
                </div>
                <div class="form-group">
                    <label>用药频率</label>
                    <input type="text" id="editMedFrequency" value="${escapeHtml(r.frequency || '')}" placeholder="如: 每8小时一次">
                </div>
                <div class="form-group">
                    <label>下次用药时间</label>
                    <input type="datetime-local" id="editMedNextTime" value="${r.next_dose_time ? r.next_dose_time.slice(0, 16) : ''}">
                </div>
                <div class="form-group">
                    <label>备注</label>
                    <textarea id="editMedNote" rows="2">${escapeHtml(r.note || '')}</textarea>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="closeModal()">取消</button>
                <button class="btn btn-primary" onclick="saveMedReminderEdit(${r.id})">保存</button>
            </div>
        `);
    });
}

function saveMedReminderEdit(id) {
    if (!App.currentBaby) return;
    const data = {
        medication_name: document.getElementById('editMedName').value,
        dosage: document.getElementById('editMedDosage').value,
        frequency: document.getElementById('editMedFrequency').value,
        next_dose_time: document.getElementById('editMedNextTime').value,
        note: document.getElementById('editMedNote').value,
    };
    if (!data.medication_name) { showToast.warning('请输入药物名称'); return; }
    api(`/api/babies/${App.currentBaby}/medication-reminders/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data)
    }).then(res => {
        if (res.success) {
            showToast.success('已更新');
            closeModal();
            loadMedReminders();
        } else {
            showToast.error(res.message || '更新失败');
        }
    });
}

function showAddMedReminderModal() {
    const formHtml = `
        <div class="form-group">
            <label>药物名称</label>
            <input type="text" id="medName" placeholder="如: 布洛芬混悬液">
        </div>
        <div class="form-group">
            <label>剂量</label>
            <input type="text" id="medDosage" placeholder="如: 5ml">
        </div>
        <div class="form-group">
            <label>用药频率</label>
            <input type="text" id="medFrequency" placeholder="如: 每8小时一次">
        </div>
        <div class="form-group">
            <label>下次用药时间</label>
            <input type="datetime-local" id="medNextTime">
        </div>
        <div class="form-group">
            <label>备注</label>
            <textarea id="medNote" rows="2" placeholder="其他备注"></textarea>
        </div>
    `;
    showModal('medReminderModal');
    const body = document.getElementById('medReminderModalBody');
    if (body) body.innerHTML = formHtml;
}

function submitMedReminder() {
    const data = {
        medication_name: document.getElementById('medName')?.value,
        dosage: document.getElementById('medDosage')?.value || '',
        frequency: document.getElementById('medFrequency')?.value || '',
        next_dose_time: document.getElementById('medNextTime')?.value,
        note: document.getElementById('medNote')?.value || '',
    };
    if (!data.medication_name) { alert('请输入药物名称'); return; }
    
    api(`/api/babies/${App.currentBaby}/medication-reminders`, { method: 'POST', body: JSON.stringify(data) }).then(res => {
        if (res.success) {
            hideModal('medReminderModal');
            loadMedReminders();
        }
    });
}

function toggleMedReminder(id, isActive) {
    const reminder = App.medReminders?.find(r => r.id === id);
    if (!reminder) return;
    api(`/api/babies/${App.currentBaby}/medication-reminders/${id}`, {
        method: 'PUT',
        body: JSON.stringify({
            ...reminder,
            is_active: isActive
        })
    }).then(res => {
        if (res.success) loadMedReminders();
    });
}

function takeMedNow(id) {
    const reminder = App.medReminders?.find(r => r.id === id);
    if (!reminder) return;
    // 计算下次用药时间
    let nextTime = new Date();
    if (reminder.frequency) {
        // 简单解析频率（如"每8小时"）
        const match = reminder.frequency.match(/(\d+)\s*小时/);
        if (match) {
            nextTime.setHours(nextTime.getHours() + parseInt(match[1]));
        } else {
            nextTime.setHours(nextTime.getHours() + 8); // 默认8小时
        }
    } else {
        nextTime.setHours(nextTime.getHours() + 8);
    }
    
    api(`/api/babies/${App.currentBaby}/medication-reminders/${id}`, {
        method: 'PUT',
        body: JSON.stringify({
            ...reminder,
            next_dose_time: new Date(nextTime.getTime() - nextTime.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
        })
    }).then(res => {
        if (res.success) {
            showToast('已记录服药，下次用药时间已更新');
            loadMedReminders();
        }
    });
}

function deleteMedReminder(id) {
    if (!confirm('确定要删除这条用药提醒吗？')) return;
    api(`/api/babies/${App.currentBaby}/medication-reminders/${id}`, { method: 'DELETE' }).then(res => {
        if (res.success) loadMedReminders();
    });
}
