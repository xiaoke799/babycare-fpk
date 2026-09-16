/**
 * 首页「身体数据」卡渲染自检。
 *
 * 背景：后端 dashboard 早就返回 latest_growth，前端一直没用，
 * 所以首页看着像「没有身高体重」。现在补上显示，这里验证三种情况：
 *   1. 有完整测量值 → 数值和「测量于 X」都出得来
 *   2. 一条都没有   → 显示 --，给引导文案而不是空白
 *   3. 只填了体重   → 体重出得来，其余是 --（不能把 null 渲染成 "null"）
 *
 * 不跑浏览器：从 app.js 里按大括号配平抠出函数，喂桩 DOM 执行。
 */
const fs = require('fs');

const ROOT = 'D:/育儿板块/babycare-fpk';
const js = fs.readFileSync(ROOT + '/app/frontend/js/app.js', 'utf8');

let pass = 0, fail = 0;
function ok(cond, name, extra) {
    if (cond) { pass++; console.log('  [OK]   ' + name); }
    else { fail++; console.log('  [FAIL] ' + name + (extra !== undefined ? '  -> ' + extra : '')); }
}

// 抠出 renderHomeBodyData（按大括号配平，别用正则切——函数里有模板串和对象）
function grabFn(src, name) {
    const i = src.indexOf('function ' + name);
    if (i < 0) throw new Error('找不到函数 ' + name);
    const start = src.indexOf('{', i);
    let depth = 0, end = -1;
    for (let k = start; k < src.length; k++) {
        if (src[k] === '{') depth++;
        else if (src[k] === '}') { depth--; if (depth === 0) { end = k; break; } }
    }
    return src.slice(i, end + 1);
}

const store = {};
function el(id) {
    if (!store[id]) store[id] = { id, textContent: '' };
    return store[id];
}
const document = { getElementById: el };
const setText = (id, v) => { el(id).textContent = String(v); };

const code = grabFn(js, 'renderHomeBodyData');
const renderHomeBodyData = new Function('document', 'setText', code + '; return renderHomeBodyData;')(document, setText);

function reset() { for (const k in store) store[k].textContent = ''; }
const get = id => store[id] ? store[id].textContent : '(未渲染)';

console.log('\n=== 1. 有完整测量值 ===');
reset();
renderHomeBodyData({ height: 68.5, weight: 8.2, bmi: 17.5, head_circumference: 43.0, record_date: '2026-09-10' });
ok(get('homeLatestHeight') === '68.5', '身高显示 68.5', get('homeLatestHeight'));
ok(get('homeLatestWeight') === '8.2', '体重显示 8.2', get('homeLatestWeight'));
ok(get('homeLatestBmi') === '17.5', 'BMI 显示 17.5', get('homeLatestBmi'));
ok(get('homeLatestHead') === '43', '头围显示 43', get('homeLatestHead'));
ok(get('homeBodyDate').includes('2026-09-10'), '日期带出测量日', get('homeBodyDate'));

console.log('\n=== 2. 一条记录都没有 ===');
reset();
renderHomeBodyData(null);
ok(get('homeLatestHeight') === '--', '身高显示 --', get('homeLatestHeight'));
ok(get('homeLatestWeight') === '--', '体重显示 --', get('homeLatestWeight'));
ok(get('homeBodyDate').includes('还没有'), '给引导文案而不是空白', get('homeBodyDate'));
ok(!get('homeBodyDate').includes('null'), '文案里不能出现 null', get('homeBodyDate'));

console.log('\n=== 3. 只填了体重（其余字段是 null）===');
reset();
renderHomeBodyData({ height: null, weight: 8.2, bmi: null, head_circumference: null, record_date: '2026-09-10' });
ok(get('homeLatestWeight') === '8.2', '体重照常显示', get('homeLatestWeight'));
ok(get('homeLatestHeight') === '--', '身高是 --（不能显示 null）', get('homeLatestHeight'));
ok(get('homeLatestBmi') === '--', 'BMI 是 --（不能显示 null）', get('homeLatestBmi'));
ok(!get('homeLatestHead').includes('null'), '头围不能显示 null', get('homeLatestHead'));

console.log('\n=== 4. 后端把 0 当有效值（体重 0 不该被当成「没有」）===');
reset();
renderHomeBodyData({ height: 0, weight: 0, bmi: 0, head_circumference: 0, record_date: '2026-09-10' });
ok(get('homeLatestWeight') === '0', '体重 0 要显示出来，不是 --', get('homeLatestWeight'));

console.log('\n=== 5. 接线检查 ===');
ok(/renderHomeBodyData\(data\.latest_growth\)/.test(js), 'loadDashboard 真的调用了它');
ok(/getElementById\('homeBodyRecordBtn'\)\?\.addEventListener/.test(js), '「记一次」按钮已绑定');
ok(/label: '身高体重'/.test(js), '记录页有「身高体重」入口');
ok(/showRecordModal\('growth'\)/.test(js), '点进去是成长记录录入框');

console.log('\n==============================================');
console.log('通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('==============================================');
process.exit(fail ? 1 : 0);
