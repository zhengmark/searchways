/* ── 认证前端逻辑 ────────────────────────── */
const AUTH_TOKEN_KEY = 'auth_token_v1';
const AUTH_USER_KEY = 'auth_username_v1';

let currentUsername = null;

function getToken() {
  return localStorage.getItem(AUTH_TOKEN_KEY);
}
function setAuth(token, username) {
  localStorage.setItem(AUTH_TOKEN_KEY, token);
  localStorage.setItem(AUTH_USER_KEY, username);
  currentUsername = username;
  updateNavUI();
}
function clearAuth() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem(AUTH_USER_KEY);
  currentUsername = null;
  updateNavUI();
}
function isLoggedIn() {
  return !!getToken();
}

// ── 导航栏 UI ──────────────────────────────
function updateNavUI() {
  const navUser = document.getElementById('navUser');
  if (!navUser) return;
  if (currentUsername) {
    navUser.innerHTML = `
      <span class="user-name">${escapeHtml(currentUsername)}</span>
      <button class="auth-btn logout-btn" onclick="handleLogout()">登出</button>`;
    document.getElementById('inputSection').classList.remove('locked');
  } else {
    navUser.innerHTML = `
      <button class="auth-btn" onclick="showAuthModal('login')">登录</button>
      <button class="auth-btn" onclick="showAuthModal('register')">注册</button>`;
    document.getElementById('inputSection').classList.add('locked');
  }
}

// ── API 请求（带 token）────────────────────
async function authFetch(url, options = {}) {
  const token = getToken();
  const headers = { ...(options.headers || {}) };
  if (!headers['Content-Type'] && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  if (token) {
    headers['Authorization'] = 'Bearer ' + token;
  }
  return fetch(url, { ...options, headers });
}

// ── 弹窗 ────────────────────────────────────
function showAuthModal(mode) {
  const overlay = document.getElementById('authOverlay');
  const title = document.getElementById('authModalTitle');
  const submitBtn = document.getElementById('authSubmitBtn');
  const switchText = document.getElementById('authSwitchText');
  const errorEl = document.getElementById('authError');

  document.getElementById('authMode').value = mode;
  title.textContent = mode === 'login' ? '登录' : '注册';
  submitBtn.textContent = mode === 'login' ? '登录' : '注册';
  switchText.innerHTML = mode === 'login'
    ? '还没有账号？<a onclick="showAuthModal(\'register\')">立即注册</a>'
    : '已有账号？<a onclick="showAuthModal(\'login\')">去登录</a>';
  errorEl.textContent = '';
  errorEl.classList.remove('show');
  document.getElementById('authUsername').value = '';
  document.getElementById('authPassword').value = '';
  const pwd2 = document.getElementById('authPassword2');
  pwd2.value = '';
  document.getElementById('confirmField').style.display = mode === 'register' ? 'block' : 'none';

  overlay.classList.add('active');
  setTimeout(() => document.getElementById('authUsername').focus(), 100);
}

function hideAuthModal() {
  document.getElementById('authOverlay').classList.remove('active');
}

// ── 提交 ────────────────────────────────────
async function submitAuth() {
  const mode = document.getElementById('authMode').value;
  const username = document.getElementById('authUsername').value.trim();
  const password = document.getElementById('authPassword').value;
  const errorEl = document.getElementById('authError');
  const submitBtn = document.getElementById('authSubmitBtn');

  errorEl.textContent = '';
  errorEl.classList.remove('show');

  if (!username || !password) {
    errorEl.textContent = '请填写用户名和密码';
    errorEl.classList.add('show');
    return;
  }
  if (mode === 'register') {
    const pwd2 = document.getElementById('authPassword2').value;
    if (password !== pwd2) {
      errorEl.textContent = '两次输入的密码不一致';
      errorEl.classList.add('show');
      return;
    }
  }

  submitBtn.disabled = true;
  try {
    const resp = await fetch('/api/auth/' + (mode === 'login' ? 'login' : 'register'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      errorEl.textContent = data.detail || '操作失败';
      errorEl.classList.add('show');
      submitBtn.disabled = false;
      return;
    }
    setAuth(data.token, data.username);
    hideAuthModal();
    // 重新加载历史
    renderHistoryList();
  } catch (err) {
    errorEl.textContent = '网络错误，请稍后重试';
    errorEl.classList.add('show');
    submitBtn.disabled = false;
  }
}

async function handleLogout() {
  clearAuth();
  renderHistoryList();
}

// ── 初始化：检查 token 有效性 ──────────────
async function initAuth() {
  const token = getToken();
  if (!token) {
    currentUsername = null;
    updateNavUI();
    return;
  }
  // 快速恢复用户名，同时后台验证
  currentUsername = localStorage.getItem(AUTH_USER_KEY);
  updateNavUI();
  try {
    const resp = await authFetch('/api/auth/me');
    const data = await resp.json();
    if (data.username) {
      currentUsername = data.username;
      localStorage.setItem(AUTH_USER_KEY, data.username);
    } else {
      clearAuth();
    }
  } catch {
    // token 过期，保留本地状态允许离线使用
  }
  updateNavUI();
}

// ── 工具 ────────────────────────────────────
function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ── 页面加载时初始化 ────────────────────────
document.addEventListener('DOMContentLoaded', initAuth);
