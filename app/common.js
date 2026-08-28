/* 主页面和明细页共用的工具函数。
 *
 * 抽出来是必要的而不是可选的: amt6() 的精度规则、authHeader() 的取值方式
 * 都修过一轮，如果存两份，早晚有一份会漏改。
 */

// ---- 认证 ----------------------------------------------------------------
// 凭据只存在当前标签页的 sessionStorage 里，服务端不保存任何 session。
// 同标签页内跳转(index -> detail)凭据仍在；新标签页/收藏夹打开则没有，
// 会被 toLogin() 带着 returnTo 送去登录页，登录后再回来。
const AUTH_KEY = 'bp_auth';

function authHeader(){
  const t = sessionStorage.getItem(AUTH_KEY);
  return t ? {'Authorization': 'Basic ' + t} : {};
}

function toLogin(){
  sessionStorage.removeItem(AUTH_KEY);
  const back = location.pathname + location.search;
  const q = (back && back !== '/') ? '?returnTo=' + encodeURIComponent(back) : '';
  location.replace('/login.html' + q);
}

// 页面若是用 http://user:pass@host/ 这类带凭据的地址打开的，相对 URL 会继承
// 那段凭据，而 fetch() 拒绝带凭据的 URL。所有请求都经这里构造。
function apiURL(path, params){
  const u = new URL(path, location.href);
  u.username = ''; u.password = '';
  Object.entries(params || {}).forEach(([k, v]) => u.searchParams.set(k, v));
  return u.toString();
}

// ---- 格式化 --------------------------------------------------------------
function esc(s){
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// 金额: 后端传字符串以保精度，这里只做展示层格式化。固定 2 位。
function money(v, opts){
  opts = opts || {};
  if(v === null || v === undefined || v === '') return '<span class="dim">—</span>';
  const n = Number(v);
  if(!isFinite(n)) return esc(String(v));
  const s = n.toLocaleString('en-US',{minimumFractionDigits:2, maximumFractionDigits:2});
  return '<span class="num">' + (opts.atLeast ? '≥ ' : '') + s + '</span>';
}

// 原始金额 / 抹零专用: 至少 2 位、最多 6 位小数，去掉多余尾零。
// 抹零差额常在 1e-5 量级(实测 0.000025)，用 money() 的 2 位会显示成 "0.00" ——
// 而这两列的全部意义就是解释"为什么应付是 0"，格式化掉就白显示了。
function amt6(v){
  if(v === null || v === undefined || v === '') return '<span class="dim">—</span>';
  const n = Number(v);
  if(!isFinite(n)) return esc(String(v));
  const frac = Math.abs(n).toFixed(6).split('.')[1].replace(/0+$/, '');
  const dec = frac.length > 2 ? frac.length : 2;
  return '<span class="num">' + n.toLocaleString('en-US',
      {minimumFractionDigits: dec, maximumFractionDigits: dec}) + '</span>';
}

function nonZero(v){ const n = Number(v); return isFinite(n) && n !== 0; }

function fmtLocal(iso){
  if(!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString('zh-CN',{hour12:false});
}

function errText(e){
  if(!e) return '';
  const bits = [];
  if(e.code) bits.push('['+esc(e.code)+']');
  bits.push(esc(e.message || '未知错误'));
  if(e.rate_limit) bits.push('(接口限流，稍后重试)');
  return bits.join(' ');
}

function defaultPeriod(){
  const d = new Date();
  return d.getUTCFullYear() + '-' + String(d.getUTCMonth()+1).padStart(2,'0');
}

// 账期格式校验，前端也挡一道，省得拿脏参数去打接口
const PERIOD_RE = /^\d{4}-(0[1-9]|1[0-2])$/;

// ---- 外壳(侧边栏) --------------------------------------------------------
// 侧边栏由 JS 渲染而不是两个页面各写一份 HTML: 导航项、退出按钮的显示条件、
// 收起逻辑都得两页一致，存两份早晚有一份漏改(common.js 开头那段注释同理)。
//
// 收起状态存 localStorage(不是 sessionStorage) —— 这是界面偏好，不是凭据，
// 关标签页后应当留着。**读取必须在 <head> 里的内联脚本完成**，等到这里再读
// 就已经画过一帧展开态了，切页时侧边栏会闪一下。
const SIDE_KEY = 'bp_side';

const ICON = {
  overview: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
    + '<rect x="3" y="3" width="7.5" height="7.5" rx="1.6"/>'
    + '<rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6"/>'
    + '<rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6"/>'
    + '<rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6"/></svg>',
  bill: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M6 2.75h8L19 7.5V20.5a.75.75 0 0 1-.75.75H6a.75.75 0 0 1-.75-.75'
    + 'V3.5A.75.75 0 0 1 6 2.75Z"/><path d="M13.75 2.9V7.5H18.6"/>'
    + '<path d="M8.5 12.5h7M8.5 16.5h4.5"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M14.5 6.5 9 12l5.5 5.5"/></svg>',
  logout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M14.5 4.25h3.25A1 1 0 0 1 18.75 5.25v13.5a1 1 0 0 1-1 1H14.5"/>'
    + '<path d="M10 8.25 6.25 12 10 15.75"/><path d="M6.5 12h8"/></svg>',
  menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="1.8" stroke-linecap="round">'
    + '<path d="M4 7h16M4 12h16M4 17h16"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="1.8" stroke-linecap="round">'
    + '<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 4.5 4.5"/></svg>'
};

function sideMode(){
  return document.documentElement.dataset.side === 'mini' ? 'mini' : 'full';
}

function setSideMode(mode){
  document.documentElement.dataset.side = mode;
  try{ localStorage.setItem(SIDE_KEY, mode); }catch(e){ /* 隐私模式下失败无所谓 */ }
  const btn = document.getElementById('sideToggle');
  if(btn){
    const label = btn.querySelector('span');
    if(label) label.textContent = (mode === 'mini' ? '展开' : '收起');
    btn.classList.toggle('flip', mode === 'mini');
    btn.title = (mode === 'mini' ? '展开侧边栏' : '收起侧边栏');
  }
}

// active: 'overview' | 'bill'
function initShell(active){
  const side = document.getElementById('side');
  if(!side) return;

  // title 不只是悬浮提示: 收起态下 <span> 是 display:none，隐藏文字不计入
  // 无障碍名，只剩图标的按钮就变成"没有名字的按钮"了。title 兜住这个。
  const item = (key, href, text, icon) =>
      '<a class="nav-i' + (key === active ? ' on' : '') + '" href="' + href
    + '" title="' + text + '">' + icon + '<span>' + text + '</span></a>';

  // 回总览时带 ?restore=1: 总览页据此用 sessionStorage 里的快照渲染，
  // 而不是把所有账号的接口重查一遍(它自己那套返回逻辑就认这个标记)。
  const home = (active === 'overview') ? '/' : '/?restore=1';

  side.innerHTML =
      '<a class="brand" href="' + home + '">'
    +   '<img src="/static/Kuromi_Icon_50px_20260828.png" width="28" height="28" alt="">'
    +   '<span class="txt"><b>Kuromi 平台</b>'
    +     '<span class="tag">BytePlus 授信看板</span></span>'
    + '</a>'
    + '<nav class="nav">'
    +   item('overview', home, '总览', ICON.overview)
    +   item('bill', '/bill.html', '账单', ICON.bill)
    + '</nav>'
    + '<div class="side-foot">'
    +   '<button class="nav-i collapse" id="sideToggle" type="button">'
    +     ICON.chevron + '<span>收起</span></button>'
    // 只有确实带着凭据时才给退出按钮(未启用验证时没有可退的东西)
    +   (sessionStorage.getItem(AUTH_KEY)
        ? '<button class="nav-i danger" id="logout" type="button" title="退出登录">'
          + ICON.logout + '<span>退出登录</span></button>' : '')
    + '</div>';

  setSideMode(sideMode());          // 同步按钮文案/箭头方向

  document.getElementById('sideToggle').addEventListener('click', () =>
    setSideMode(sideMode() === 'mini' ? 'full' : 'mini'));

  const out = document.getElementById('logout');
  if(out) out.addEventListener('click', toLogin);

  // 窄屏: 侧边栏变抽屉，汉堡按钮开、遮罩/Esc 关
  const openDrawer = on => document.body.classList.toggle('side-open', on);
  const hamb = document.getElementById('hamb');
  if(hamb){
    hamb.innerHTML = ICON.menu;
    hamb.addEventListener('click', () =>
      openDrawer(!document.body.classList.contains('side-open')));
  }
  const back = document.getElementById('backdrop');
  if(back) back.addEventListener('click', () => openDrawer(false));
  document.addEventListener('keydown', ev => {
    if(ev.key === 'Escape') openDrawer(false);
  });
}

// ---- 表格排序 ------------------------------------------------------------
function isEmptyVal(v){ return v === null || v === undefined || v === ''; }

// 稳定排序。两条规则值得说明:
//   1. 空值恒定垫底，**不随升降翻转** —— 把"没有单价"当 0 会让它插进最便宜
//      那一档里，看起来像数据算错了。
//   2. 同值保持原顺序(拿原下标兜底)，否则反复点表头行会乱跳。
function sortRows(rows, get, numeric, dir){
  return rows
    .map((r, i) => ({r: r, i: i}))
    .sort((x, y) => {
      const a = get(x.r), b = get(y.r);
      const ea = isEmptyVal(a), eb = isEmptyVal(b);
      if(ea !== eb) return ea ? 1 : -1;
      if(!ea){
        let c;
        if(numeric){
          const na = Number(a), nb = Number(b);
          // 非数字(接口偶尔给 "按量" 之类)退化成文本比，别让 NaN 把顺序搅乱
          if(isFinite(na) && isFinite(nb)) c = na === nb ? 0 : (na < nb ? -1 : 1);
          else c = String(a).localeCompare(String(b), 'zh');
        }else{
          c = String(a).localeCompare(String(b), 'zh');
        }
        if(c) return dir < 0 ? -c : c;
      }
      return x.i - y.i;
    })
    .map(o => o.r);
}
