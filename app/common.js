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
