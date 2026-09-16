/**
 * 健康档案「合并四个独立页」的前端结构自检。
 *
 * 背景：把 page-vaccines / page-temperature / page-med-reminder / page-allergy-detail
 * 四个独立页并进健康档案的 Tab 里。删掉 section 后最容易出两类事故：
 *   1) 深链/导航还指向被删的页 -> switchPage 找不到 section -> 白屏
 *   2) 区块搬走了，但按钮的绑定或渲染容器 id 没跟上 -> 点了没反应 / 渲染时抛错
 *
 * 这里不跑浏览器，纯静态抠代码做交叉验证：
 *   HTML 的 Tab 按钮  <->  JS 的别名表 <-> 各 Tab 的加载函数 <-> 加载函数用到的容器 id
 */
const fs = require('fs');

const ROOT = 'D:/育儿板块/babycare-fpk';
const js = fs.readFileSync(ROOT + '/app/frontend/js/app.js', 'utf8');
const html = fs.readFileSync(ROOT + '/app/frontend/index.html', 'utf8');

let pass = 0, fail = 0;
const failures = [];
function ok(cond, name, extra) {
    if (cond) { pass++; console.log('  [OK]   ' + name); }
    else { fail++; failures.push(name); console.log('  [FAIL] ' + name + (extra ? ' -> ' + extra : '')); }
}
function section(t) { console.log('\n=== ' + t + ' ==='); }

// ---------- 工具：从源码里抠出对象字面量 ----------
function grabObject(src, varName) {
    // 必须定位到「声明处」。直接用 indexOf(varName) 会先撞上 switchPage 里的
    // HEALTH_TAB_PAGES[page] 这类引用，那里后面没有 {，抠出来是空的。
    const decl = new RegExp('(?:const|let|var)\\s+' + varName + '\\s*=');
    const dm = decl.exec(src);
    const i = dm ? dm.index : src.indexOf(varName);
    if (i < 0) return null;
    const start = src.indexOf('{', i);
    if (start < 0) return null;
    let depth = 0, end = -1;
    for (let k = start; k < src.length; k++) {
        if (src[k] === '{') depth++;
        else if (src[k] === '}') { depth--; if (depth === 0) { end = k; break; } }
    }
    return end < 0 ? null : src.slice(start, end + 1);
}

// 从 { host:'health', tab:'vaccine' } 这类字面量里解析
function parsePairs(literal) {
    const out = {};
    const re = /['"]?([A-Za-z0-9_\-]+)['"]?\s*:\s*\{([^}]*)\}/g;
    let m;
    while ((m = re.exec(literal))) {
        const key = m[1];
        const body = m[2];
        const host = /host\s*:\s*['"]([^'"]+)['"]/.exec(body);
        const tab = /tab\s*:\s*['"]([^'"]+)['"]/.exec(body);
        out[key] = { host: host ? host[1] : null, tab: tab ? tab[1] : null };
    }
    return out;
}

// ---------- HTML 侧事实 ----------
const healthTabBtns = [...html.matchAll(/class="health-tab[^"]*"\s+data-tab="([^"]+)"/g)].map(m => m[1]);
const healthTabContents = [...html.matchAll(/class="health-tab-content[^"]*"\s+id="tab-([^"]+)"/g)].map(m => m[1]);
const growthTabBtns = [...html.matchAll(/data-tab="([^"]+)"[^>]*class="[^"]*growth-tab/g)].map(m => m[1]);
const allHtmlIds = new Set([...html.matchAll(/\bid\s*=\s*["']([A-Za-z0-9_\-]+)["']/g)].map(m => m[1]));
const deletedPages = ['page-vaccines', 'page-temperature', 'page-med-reminder', 'page-allergy-detail'];

section('1. 四个独立页的 section 确实已删除');
deletedPages.forEach(p => ok(!html.includes('id="' + p + '"'), p + ' 已不存在于 HTML'));

section('2. Tab 按钮与 Tab 内容一一对应（多一个少一个都会白屏/空洞）');
const btns = [...new Set(healthTabBtns)];
ok(btns.length === healthTabContents.length,
    `健康档案 Tab 按钮数(${btns.length}) == 内容数(${healthTabContents.length})`);
btns.forEach(t => ok(healthTabContents.includes(t), '按钮 ' + t + ' 有对应的 #tab-' + t));

section('3. 别名表：被删的页必须能路由到健康档案的某个 Tab');
const healthAliases = parsePairs(grabObject(js, 'HEALTH_TAB_PAGES') || '{}');
const growthAliases = parsePairs(grabObject(js, 'GROWTH_TAB_PAGES') || '{}');
ok(Object.keys(healthAliases).length === 4,
    'HEALTH_TAB_PAGES 有 4 个别名，实际 ' + Object.keys(healthAliases).length);
[['vaccines', 'vaccine'], ['temperature', 'temperature'],
 ['med-reminder', 'reminders'], ['allergy-detail', 'allergy']].forEach(([page, tab]) => {
    ok(healthAliases[page] && healthAliases[page].host === 'health',
        page + ' -> host=health', JSON.stringify(healthAliases[page]));
    ok(healthAliases[page] && healthAliases[page].tab === tab,
        page + ' -> tab=' + tab, healthAliases[page] && healthAliases[page].tab);
    ok(btns.includes(tab), '目标 Tab "' + tab + '" 在 HTML 里存在');
});
ok(growthAliases['sleep-analysis'] && growthAliases['sleep-analysis'].host === 'growth',
    'GROWTH_TAB_PAGES 仍是 {host,tab} 形式（旧写法已被 switchPage 兼容）');
ok(Object.values(growthAliases).every(v => v.host), 'GROWTH_TAB_PAGES 每项都有 host');

section('4. switchPage 能解析健康检查档案的别名');
ok(/GROWTH_TAB_PAGES\[page\]\s*\|\|\s*HEALTH_TAB_PAGES\[page\]/.test(js),
    'switchPage 同时查两张别名表');
ok(/function\s+activatePageTab/.test(js) && /activatePageTab\(tabTarget\.host,\s*tabTarget\.tab\)/.test(js),
    'switchPage 调用 activatePageTab(host, tab)');
ok(/function\s+activateHealthTab/.test(js), '存在 activateHealthTab');
ok(/App\.currentPage\s*=\s*page/.test(js), '别名页最终把 currentPage 改回别名（否则刷新/高亮认错页）');

section('5. 导航里不再有指向已删页的入口');
const navHubs = grabObject(js, 'NAV_HUBS') || '';
deletedPages.forEach(p => {
    // NAV_HUBS 里页面是以 'page-id' 字符串形式出现的
    ok(!new RegExp("['\"]" + p + "['\"]").test(navHubs), 'NAV_HUBS 已移除 ' + p);
});

section('6. loadHealthTabData 覆盖每个 Tab，且没有重复 case');
const fnStart = js.indexOf('function loadHealthTabData');
const fnBody = js.slice(fnStart, js.indexOf('\n}', fnStart));
const cases = [...fnBody.matchAll(/case\s+'([^']+)'/g)].map(m => m[1]);
ok(cases.length === new Set(cases).size,
    'switch 里没有重复 case（重复会让后面那个永远不执行）',
    cases.join(','));
btns.forEach(t => ok(cases.includes(t), 'Tab "' + t + '" 有对应的加载分支'));

section('7. 每个 Tab 的加载函数都有定义');
// 一个 case 可能跨多行调好几个函数，所以按 case 切段后整段收集
const caseChunks = fnBody.split(/case\s+'[^']+':/).slice(1);
const loaders = caseChunks.flatMap(chunk => {
    const head = chunk.split(/\bbreak\b/)[0];       // 只取到 break 之前
    return [...head.matchAll(/([A-Za-z_$][\w$]*)\s*\(/g)].map(m => m[1]);
});
[...new Set(loaders)].forEach(fn => {
    ok(new RegExp('function\\s+' + fn + '\\s*\\(').test(js), '加载函数 ' + fn + '() 已定义');
});

section('8. 合并进来的三块渲染目标容器在 HTML 里存在');
[['体温', ['temperatureSummary', 'temperatureCanvas', 'temperatureList']],
 ['用药提醒', ['medReminderStats', 'activeRemindersList', 'allRemindersList']],
 ['过敏测试', ['allergyList2']],
 ['疫苗统计/时间线', ['vaccinesStats', 'vaccineTimelineBox']]
].forEach(([name, ids]) => {
    ids.forEach(id => ok(allHtmlIds.has(id), name + ' 容器 #' + id + ' 存在'));
});

section('9. 用药提醒走的是 loadMedReminders（不是 loadMedicationPage）');
ok(loaders.includes('loadMedReminders'), 'reminders 分支调用 loadMedReminders');
ok(!loaders.includes('loadMedicationPage'),
    'reminders 分支不再调用 loadMedicationPage（那是「用药记录」，容器不存在会静默 return）');

section('10. 孤儿渲染入口已处理');
const vpStart = js.indexOf('function loadVaccinesPage');
const vpBody = js.slice(vpStart, js.indexOf('\n}', vpStart));
ok(!vpBody.includes("getElementById('vaccinesList')"),
    'loadVaccinesPage 不再渲染已删除的 #vaccinesList');
ok(vpBody.includes('loadVaccineRecords') && vpBody.includes('loadVaccineStats'),
    'loadVaccinesPage 委托给健康档案的实现');
ok(/getElementById\('addVaccineBtn'\)\?\./.test(js),
    'initVaccineModal 对已删除的 #addVaccineBtn 加了 ?.（否则抛错会带崩后续绑定）');

console.log('\n==============================================');
console.log('通过 ' + pass + ' 项，失败 ' + fail + ' 项');
if (fail) { console.log('失败项：'); failures.forEach(f => console.log('  - ' + f)); }
console.log('==============================================');
process.exit(fail ? 1 : 0);
