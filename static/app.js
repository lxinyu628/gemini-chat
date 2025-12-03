// 全局状态管理
const state = {
  currentConversationId: null,
  currentSessionName: null,  // Google API 的完整 session name
  conversations: [],
  currentModel: 'business-gemini',
  uploadedFiles: [],
  theme: localStorage.getItem('theme') || 'light',
  statusCheckInterval: null,
  signoutUrl: null,
  accountChooserUrl: null,
  autoBrowserStarted: false
};

// ==================== Lucide 图标自动渲染 ====================

// 使用 MutationObserver 监听 DOM 变化，自动渲染新添加的 Lucide 图标
(function initLucideObserver () {
  if (typeof lucide === 'undefined') return;

  // 防抖：避免频繁调用
  let pending = false;
  const renderIcons = () => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      lucide.createIcons();
      pending = false;
    });
  };

  // 监听 DOM 变化
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
        // 检查是否有新增的 lucide 图标元素
        for (const node of mutation.addedNodes) {
          if (node.nodeType === Node.ELEMENT_NODE) {
            // 跳过已经渲染后的 Lucide SVG 元素，避免无限循环
            if (node.classList?.contains('lucide') || node.querySelector?.('svg.lucide')) {
              continue;
            }
            // 只处理未渲染的 data-lucide 元素
            if (node.hasAttribute?.('data-lucide') || node.querySelector?.('[data-lucide]')) {
              renderIcons();
              return;
            }
          }
        }
      }
    }
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true
  });
})();

// ==================== Toast 通知系统 ====================

const ToastIcons = {
  success: `<i data-lucide="badge-check"></i>`,
  error: `<i data-lucide="badge-x"></i>`,
  warning: `<i data-lucide="badge-alert"></i>`,
  info: `<i data-lucide="badge-info"></i>`,
  confirm: `<i data-lucide="badge-question-mark"></i>`
};

/**
 * 显示 Toast 通知
 * @param {Object} options - 配置选项
 * @param {string} options.type - 类型: 'success' | 'error' | 'warning' | 'info'
 * @param {string} options.title - 标题
 * @param {string} options.message - 消息内容
 * @param {number} options.duration - 显示时长(ms)，默认 4000，设为 0 则不自动关闭
 * @param {boolean} options.closable - 是否可手动关闭，默认 true
 */
function showToast (options) {
  const {
    type = 'info',
    title = '',
    message = '',
    duration = 4000,
    closable = true
  } = options;

  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;

  const icon = ToastIcons[type] || ToastIcons.info;

  toast.innerHTML = `
        <div class="toast-icon">${icon}</div>
        <div class="toast-content">
            ${title ? `<div class="toast-title">${escapeHtml(title)}</div>` : ''}
            <div class="toast-message">${escapeHtml(message)}</div>
        </div>
        ${closable ? `
        <button class="toast-close" aria-label="关闭">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"/>
                <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
        </button>
        ` : ''}
    `;

  // 关闭按钮事件
  if (closable) {
    const closeBtn = toast.querySelector('.toast-close');
    closeBtn.addEventListener('click', () => removeToast(toast));
  }

  container.appendChild(toast);

  // 自动关闭
  if (duration > 0) {
    setTimeout(() => removeToast(toast), duration);
  }

  return toast;
}

function removeToast (toast) {
  if (!toast || !toast.parentNode) return;

  toast.classList.add('toast-hiding');
  setTimeout(() => {
    if (toast.parentNode) {
      toast.parentNode.removeChild(toast);
    }
  }, 300);
}

// 便捷方法
function toast (message, type = 'info') {
  const titles = {
    success: '成功',
    error: '错误',
    warning: '警告',
    info: '提示'
  };
  return showToast({ type, title: titles[type], message });
}

toast.success = (message, title = '成功') => showToast({ type: 'success', title, message });
toast.error = (message, title = '错误') => showToast({ type: 'error', title, message });
toast.warning = (message, title = '警告') => showToast({ type: 'warning', title, message });
toast.info = (message, title = '提示') => showToast({ type: 'info', title, message });

/**
 * 显示确认对话框
 * @param {Object|string} options - 配置选项或消息字符串
 * @param {string} options.title - 标题
 * @param {string} options.message - 消息内容
 * @param {string} options.confirmText - 确认按钮文字，默认 '确定'
 * @param {string} options.cancelText - 取消按钮文字，默认 '取消'
 * @param {string} options.type - 类型: 'warning' | 'danger' | 'info'，影响确认按钮颜色
 * @returns {Promise<boolean>} 用户点击确认返回 true，取消返回 false
 */
toast.confirm = function (options) {
  return new Promise((resolve) => {
    // 支持简单的字符串参数
    if (typeof options === 'string') {
      options = { message: options };
    }

    const {
      title = '确认',
      message = '',
      confirmText = '确定',
      cancelText = '取消',
      type = 'warning'
    } = options;

    // 创建遮罩层
    const overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';

    // 创建对话框
    const dialog = document.createElement('div');
    dialog.className = `confirm-dialog confirm-${type}`;

    const icon = ToastIcons[type] || ToastIcons.confirm;

    dialog.innerHTML = `
            <div class="confirm-header">
                <div class="confirm-icon">${icon}</div>
                <div class="confirm-title">${escapeHtml(title)}</div>
            </div>
            <div class="confirm-body">
                <div class="confirm-message">${escapeHtml(message)}</div>
            </div>
            <div class="confirm-footer">
                <button class="btn btn-secondary confirm-cancel">${escapeHtml(cancelText)}</button>
                <button class="btn btn-primary confirm-ok">${escapeHtml(confirmText)}</button>
            </div>
        `;

    // 添加到页面
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    // 动画显示
    requestAnimationFrame(() => {
      overlay.classList.add('show');
    });

    // 关闭对话框
    const close = (result) => {
      overlay.classList.remove('show');
      setTimeout(() => {
        document.body.removeChild(overlay);
        resolve(result);
      }, 200);
    };

    // 事件绑定
    const cancelBtn = dialog.querySelector('.confirm-cancel');
    const okBtn = dialog.querySelector('.confirm-ok');

    cancelBtn.addEventListener('click', () => close(false));
    okBtn.addEventListener('click', () => close(true));

    // 点击遮罩关闭
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        close(false);
      }
    });

    // ESC 键关闭
    const handleKeydown = (e) => {
      if (e.key === 'Escape') {
        close(false);
        document.removeEventListener('keydown', handleKeydown);
      } else if (e.key === 'Enter') {
        close(true);
        document.removeEventListener('keydown', handleKeydown);
      }
    };
    document.addEventListener('keydown', handleKeydown);

    // 自动聚焦确认按钮
    okBtn.focus();
  });
};

/**
 * 显示带输入框的对话框
 * @param {Object} options - 配置选项
 * @returns {Promise<string|null>} 用户输入的值，取消返回 null
 */
toast.prompt = function (options) {
  return new Promise((resolve) => {
    if (typeof options === 'string') {
      options = { message: options };
    }

    const {
      title = '请输入',
      message = '',
      placeholder = '',
      defaultValue = '',
      confirmText = '确定',
      cancelText = '取消'
    } = options;

    const overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';

    const dialog = document.createElement('div');
    dialog.className = 'confirm-dialog confirm-info';

    dialog.innerHTML = `
            <div class="confirm-header">
                <div class="confirm-icon">${ToastIcons.info}</div>
                <div class="confirm-title">${escapeHtml(title)}</div>
            </div>
            <div class="confirm-body">
                ${message ? `<div class="confirm-message">${escapeHtml(message)}</div>` : ''}
                <input type="text" class="confirm-input" placeholder="${escapeHtml(placeholder)}" value="${escapeHtml(defaultValue)}">
            </div>
            <div class="confirm-footer">
                <button class="btn btn-secondary confirm-cancel">${escapeHtml(cancelText)}</button>
                <button class="btn btn-primary confirm-ok">${escapeHtml(confirmText)}</button>
            </div>
        `;

    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    requestAnimationFrame(() => {
      overlay.classList.add('show');
    });

    const input = dialog.querySelector('.confirm-input');
    const cancelBtn = dialog.querySelector('.confirm-cancel');
    const okBtn = dialog.querySelector('.confirm-ok');

    const close = (result) => {
      overlay.classList.remove('show');
      setTimeout(() => {
        document.body.removeChild(overlay);
        resolve(result);
      }, 200);
    };

    cancelBtn.addEventListener('click', () => close(null));
    okBtn.addEventListener('click', () => close(input.value));

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        close(input.value);
      } else if (e.key === 'Escape') {
        close(null);
      }
    });

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        close(null);
      }
    });

    input.focus();
    input.select();
  });
};

// 配置 marked（Markdown 渲染器）
if (typeof marked !== 'undefined') {
  marked.setOptions({
    highlight: function (code, lang) {
      if (typeof hljs !== 'undefined' && lang && hljs.getLanguage(lang)) {
        try {
          return hljs.highlight(code, { language: lang }).value;
        } catch (e) { }
      }
      // 自动检测语言
      if (typeof hljs !== 'undefined') {
        try {
          return hljs.highlightAuto(code).value;
        } catch (e) { }
      }
      return code;
    },
    breaks: true,  // 支持换行
    gfm: true,     // GitHub 风格 Markdown
  });
}

// 渲染 Markdown 内容
function renderMarkdown (content) {
  if (typeof marked === 'undefined') {
    // 如果 marked 未加载，返回转义后的 HTML
    return escapeHtml(content).replace(/\n/g, '<br>');
  }
  try {
    return marked.parse(content);
  } catch (e) {
    console.error('Markdown 渲染失败:', e);
    return escapeHtml(content).replace(/\n/g, '<br>');
  }
}

// 为代码块添加复制按钮
function addCodeCopyButtons (container) {
  const codeBlocks = container.querySelectorAll('pre');
  codeBlocks.forEach(pre => {
    // 创建复制按钮
    const copyBtn = document.createElement('button');
    copyBtn.className = 'code-copy-btn';
    copyBtn.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>
            <span>复制</span>
        `;

    copyBtn.addEventListener('click', async () => {
      const code = pre.querySelector('code');
      const text = code ? code.textContent : pre.textContent;

      try {
        await navigator.clipboard.writeText(text);
        copyBtn.innerHTML = `
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="20 6 9 17 4 12"></polyline>
                    </svg>
                    <span>已复制</span>
                `;
        setTimeout(() => {
          copyBtn.innerHTML = `
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                        </svg>
                        <span>复制</span>
                    `;
        }, 2000);
      } catch (e) {
        console.error('复制失败:', e);
      }
    });

    // 包装 pre 元素
    const wrapper = document.createElement('div');
    wrapper.className = 'code-block-wrapper';
    pre.parentNode.insertBefore(wrapper, pre);
    wrapper.appendChild(pre);
    wrapper.appendChild(copyBtn);
  });
}

// DOM 元素
const elements = {
  sidebar: document.getElementById('sidebar'),
  sidebarToggle: document.getElementById('sidebarToggle'),
  newChatBtn: document.getElementById('newChatBtn'),
  conversationsList: document.getElementById('conversationsList'),
  chatContainer: document.getElementById('chatContainer'),
  welcomeScreen: document.getElementById('welcomeScreen'),
  messagesContainer: document.getElementById('messagesContainer'),
  messageInput: document.getElementById('messageInput'),
  sendBtn: document.getElementById('sendBtn'),
  attachBtn: document.getElementById('attachBtn'),
  fileInput: document.getElementById('fileInput'),
  filePreview: document.getElementById('filePreview'),
  modelSelector: document.getElementById('modelSelector'),
  modelName: document.getElementById('modelName'),
  themeToggle: document.getElementById('themeToggle'),
  sidebarBackdrop: document.getElementById('sidebarBackdrop'),
  statusIndicator: document.getElementById('statusIndicator'),
  expiredModal: document.getElementById('expiredModal'),
  versionInfo: document.getElementById('versionInfo')
};

// 模型列表缓存
let modelsList = [];

// 初始化
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initEventListeners();
  handleResponsiveSidebar();
  loadModels();
  loadConversations();
  checkStatus();
  startStatusMonitoring();
  loadVersionInfo();
});

// 主题切换
function initTheme () {
  document.documentElement.setAttribute('data-theme', state.theme);
}

function toggleTheme () {
  state.theme = state.theme === 'light' ? 'dark' : 'light';
  localStorage.setItem('theme', state.theme);
  document.documentElement.setAttribute('data-theme', state.theme);
}

function handleResponsiveSidebar () {
  const isMobile = window.innerWidth <= 1024;
  if (isMobile) {
    document.body.classList.remove('sidebar-open');
    elements.sidebar.classList.remove('hidden');
  } else {
    document.body.classList.remove('sidebar-open');
    elements.sidebar.classList.remove('hidden');
  }
}

// 事件监听
function initEventListeners () {
  elements.themeToggle.addEventListener('click', toggleTheme);
  elements.sidebarToggle.addEventListener('click', toggleSidebar);
  if (elements.sidebarBackdrop) {
    elements.sidebarBackdrop.addEventListener('click', () => {
      document.body.classList.remove('sidebar-open');
    });
  }
  elements.newChatBtn.addEventListener('click', createNewConversation);
  elements.sendBtn.addEventListener('click', sendMessage);
  elements.attachBtn.addEventListener('click', () => elements.fileInput.click());
  elements.fileInput.addEventListener('change', handleFileSelect);

  // 模型选择器点击事件
  elements.modelSelector.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleModelDropdown();
  });
  window.addEventListener('resize', handleResponsiveSidebar);

  // 输入框自动调整高度
  const messageInput = elements.messageInput;
  const lineHeight = 24;
  const maxLines = 5;
  const maxHeight = lineHeight * maxLines; // 5行高度 = 120px

  function autoResize () {
    // 先重置高度以获取正确的 scrollHeight
    messageInput.style.height = 'auto';
    const scrollHeight = messageInput.scrollHeight - 28; // 减去 padding
    const newHeight = Math.min(scrollHeight, maxHeight);
    messageInput.style.height = Math.max(newHeight, lineHeight) + 'px';

    // 当内容超过5行时，显示滚动条
    messageInput.style.overflowY = scrollHeight > maxHeight ? 'auto' : 'hidden';

    updateSendButton();
  }

  messageInput.addEventListener('input', autoResize);

  // 回车发送消息，Shift+Enter 换行
  messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      if (e.shiftKey) {
        // Shift+Enter: 允许换行，不做处理
        return;
      } else {
        // Enter: 发送消息
        e.preventDefault();
        if (!elements.sendBtn.disabled) {
          sendMessage();
        }
      }
    }
  });

  // 粘贴时自动调整高度
  messageInput.addEventListener('paste', () => {
    setTimeout(autoResize, 0);
  });

  // 初始化高度
  autoResize();
}

function toggleSidebar () {
  const isMobile = window.innerWidth <= 1024;
  if (isMobile) {
    document.body.classList.toggle('sidebar-open');
  } else {
    elements.sidebar.classList.toggle('hidden');
  }
}

function closeSidebarOnMobile () {
  if (window.innerWidth <= 1024) {
    document.body.classList.remove('sidebar-open');
  }
}

function updateSendButton () {
  const hasText = elements.messageInput.value.trim().length > 0;
  elements.sendBtn.disabled = !hasText;
}

// 状态检查
async function checkStatus () {
  try {
    const response = await fetch('/api/status');
    const data = await response.json();

    const statusDot = elements.statusIndicator.querySelector('.status-dot');
    const statusText = elements.statusIndicator.querySelector('.status-text');

    elements.statusIndicator.className = 'status-indicator';

    // 保存 signout_url 到全局状态
    state.signoutUrl = data.signout_url || null;
    state.currentUsername = data.username || null;
    state.accountChooserUrl = data.account_chooser_url || state.accountChooserUrl;

    // 仅在 expired 或 logged_in=false 时显示过期状态
    // 不再自动弹出模态框，改为显示"点此登录"链接，由用户自行触发
    if (data.expired || data.logged_in === false) {
      elements.statusIndicator.classList.add('error');
      statusText.innerHTML = '登录已过期，<a href="javascript:void(0)" class="login-link" onclick="openLoginModal()">点此登录</a>';
    } else if (data.warning) {
      // warning 状态：可能 Cookie 校验失败但可继续使用
      elements.statusIndicator.classList.add('warning');
      statusText.textContent = '登录异常，可能 Cookie 校验失败但可继续使用';
      // 不弹模态框，只在控制台输出调试信息
      if (data.debug) {
        console.warn('登录状态警告:', data.message, data.debug);
      }
    } else if (data.logged_in) {
      elements.statusIndicator.classList.add('online');
      // 显示用户名或简单的"已登录"状态
      if (data.username) {
        statusText.textContent = `已登录: ${data.username}`;
      } else {
        statusText.textContent = '已登录';
      }
    } else {
      elements.statusIndicator.classList.add('error');
      statusText.innerHTML = '未登录，<a href="javascript:void(0)" class="login-link" onclick="openLoginModal()">点此登录</a>';
    }
  } catch (error) {
    console.error('状态检查失败:', error);
    elements.statusIndicator.classList.add('error');
    elements.statusIndicator.querySelector('.status-text').innerHTML = '检查失败，<a href="javascript:void(0)" class="login-link" onclick="openLoginModal()">点此登录</a>';
  }
}

function startStatusMonitoring () {
  // 每分钟检查一次状态
  state.statusCheckInterval = setInterval(checkStatus, 60000);
}

async function loadVersionInfo () {
  try {
    const response = await fetch('/api/version');
    const data = await response.json();
    if (elements.versionInfo && data.version) {
      elements.versionInfo.textContent = `v${data.version}`;
    }
  } catch (error) {
    console.error('获取版本信息失败:', error);
  }
}

function showExpiredModal () {
  elements.expiredModal.classList.add('show');
  // 不再自动打开登录模态框和启动浏览器，由用户点击触发
}

// 模型加载
async function loadModels () {
  try {
    // 从 /v1/models 接口获取模型列表
    const response = await fetch('/v1/models');
    const data = await response.json();

    if (data.data && data.data.length > 0) {
      modelsList = data.data.map(model => ({
        id: model.id,
        name: model.name || model.id,
        description: model.description || ''
      }));
      state.currentModel = 'auto';
      updateModelDisplay();
    }
  } catch (error) {
    console.error('加载模型失败:', error);
    // 加载失败时使用默认模型列表
    modelsList = [
      { id: 'auto', name: '自动', description: 'Gemini Enterprise 会选择最合适的选项' },
      { id: 'gemini-2.5-flash', name: 'Gemini 2.5 Flash', description: '适用于执行日常任务' },
      { id: 'gemini-2.5-pro', name: 'Gemini 2.5 Pro', description: '最适用于执行复杂任务' },
      { id: 'gemini-3-pro-preview', name: 'Gemini 3 Pro Preview', description: '先进的推理模型' },
    ];
    state.currentModel = 'auto';
    updateModelDisplay();
  }
}

// 更新模型显示名称
function updateModelDisplay () {
  const model = modelsList.find(m => m.id === state.currentModel);
  if (model) {
    elements.modelName.textContent = model.name;
  }
}

// 模型下拉浮窗
let modelDropdown = null;

function toggleModelDropdown () {
  if (modelDropdown) {
    closeModelDropdown();
  } else {
    openModelDropdown();
  }
}

function openModelDropdown () {
  if (modelDropdown) return;

  // 创建下拉浮窗
  modelDropdown = document.createElement('div');
  modelDropdown.className = 'model-dropdown';

  // Gemini 图标 SVG
  const geminiIconSvg = `<svg width="24" height="24" viewBox="0 0 128 128" fill="none" xmlns="http://www.w3.org/2000/svg">
    <mask id="mask_dropdown_icon" style="mask-type:alpha" maskUnits="userSpaceOnUse" x="8" y="8" width="112" height="112">
      <path d="M63.892 8C62.08 38.04 38.04 62.08 8 63.892V64.108C38.04 65.92 62.08 89.96 63.892 120H64.108C65.92 89.96 89.96 65.92 120 64.108V63.892C89.96 62.08 65.92 38.04 64.108 8H63.892Z" fill="url(#paint_dd_0)"/>
    </mask>
    <g mask="url(#mask_dropdown_icon)">
      <path d="M64 0C99.3216 0 128 28.6784 128 64C128 99.3216 99.3216 128 64 128C28.6784 128 0 99.3216 0 64C0 28.6784 28.6784 0 64 0Z" fill="url(#paint_dd_1)"/>
    </g>
    <defs>
      <linearGradient id="paint_dd_0" x1="100.892" y1="30.04" x2="22.152" y2="96.848" gradientUnits="userSpaceOnUse">
        <stop stop-color="#217BFE"/><stop offset="0.14" stop-color="#1485FC"/><stop offset="0.27" stop-color="#078EFB"/><stop offset="0.52" stop-color="#548FFD"/><stop offset="0.78" stop-color="#A190FF"/><stop offset="0.89" stop-color="#AF94FE"/><stop offset="1" stop-color="#BD99FE"/>
      </linearGradient>
      <linearGradient id="paint_dd_1" x1="47.988" y1="82.52" x2="96.368" y2="32.456" gradientUnits="userSpaceOnUse">
        <stop stop-color="#217BFE"/><stop offset="0.14" stop-color="#1485FC"/><stop offset="0.27" stop-color="#078EFB"/><stop offset="0.52" stop-color="#548FFD"/><stop offset="0.78" stop-color="#A190FF"/><stop offset="0.89" stop-color="#AF94FE"/><stop offset="1" stop-color="#BD99FE"/>
      </linearGradient>
    </defs>
  </svg>`;

  // 渲染模型列表
  modelsList.forEach(model => {
    const item = document.createElement('div');
    item.className = 'model-dropdown-item' + (model.id === state.currentModel ? ' active' : '');
    item.innerHTML = `
      <div class="model-item-icon">${geminiIconSvg}</div>
      <div class="model-item-info">
        <div class="model-item-name">${escapeHtml(model.name)}</div>
        <div class="model-item-desc">${escapeHtml(model.description)}</div>
      </div>
      <i data-lucide="check" class="model-item-check"></i>
    `;
    item.addEventListener('click', () => {
      state.currentModel = model.id;
      updateModelDisplay();
      closeModelDropdown();
    });
    modelDropdown.appendChild(item);
  });

  document.body.appendChild(modelDropdown);

  // 定位浮窗
  const rect = elements.modelSelector.getBoundingClientRect();
  const dropdownRect = modelDropdown.getBoundingClientRect();
  const padding = 12;
  const maxLeft = window.innerWidth - dropdownRect.width - padding;
  const left = Math.max(padding, Math.min(rect.left, maxLeft));
  modelDropdown.style.left = `${left}px`;
  modelDropdown.style.bottom = `${window.innerHeight - rect.top + 8}px`;

  // 显示动画
  requestAnimationFrame(() => {
    modelDropdown.classList.add('show');
    elements.modelSelector.classList.add('open');
    // 渲染 lucide 图标
    if (typeof lucide !== 'undefined') {
      lucide.createIcons({ nodes: modelDropdown.querySelectorAll('[data-lucide]') });
    }
  });

  // 点击外部关闭
  setTimeout(() => {
    document.addEventListener('click', handleDropdownOutsideClick);
  }, 0);
}

function closeModelDropdown () {
  if (!modelDropdown) return;

  modelDropdown.classList.remove('show');
  elements.modelSelector.classList.remove('open');

  setTimeout(() => {
    if (modelDropdown && modelDropdown.parentNode) {
      modelDropdown.parentNode.removeChild(modelDropdown);
    }
    modelDropdown = null;
  }, 200);

  document.removeEventListener('click', handleDropdownOutsideClick);
}

function handleDropdownOutsideClick (e) {
  if (modelDropdown && !modelDropdown.contains(e.target) && !elements.modelSelector.contains(e.target)) {
    closeModelDropdown();
  }
}

// 会话管理
async function loadConversations () {
  try {
    const response = await fetch('/api/sessions');

    // 检查响应状态
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      const errorMsg = errorData.detail || `HTTP ${response.status}`;
      console.error('加载会话失败:', errorMsg);

      // 403 错误表示权限问题，显示提示
      if (response.status === 403) {
        toast.error(errorMsg, '权限错误');
      }
      // 其他错误静默处理，使用空列表
      state.conversations = [];
      renderConversations();
      return;
    }

    const sessions = await response.json();

    state.conversations = sessions;
    renderConversations();
  } catch (error) {
    console.error('加载会话失败:', error);
    state.conversations = [];
    renderConversations();
  }
}

function renderConversations () {
  elements.conversationsList.innerHTML = '';

  state.conversations.forEach(conv => {
    const item = document.createElement('div');
    item.className = 'conversation-item';
    if (conv.session_id === state.currentConversationId) {
      item.classList.add('active');
    }

    // 使用 session_name（完整名称）用于删除，session_id 用于显示
    const deleteId = conv.session_name || conv.session_id;

    item.innerHTML = `
      <div class="conversation-title">${escapeHtml(conv.title)}</div>
      <div class="conversation-actions">
        <button class="conversation-delete" data-id="${deleteId}">
          <i data-lucide="trash-2"></i>
        </button>
      </div>
    `;

    item.addEventListener('click', (e) => {
      if (!e.target.closest('.conversation-delete')) {
        loadConversation(conv.session_id, conv.session_name);
      }
    });

    const deleteBtn = item.querySelector('.conversation-delete');
    deleteBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      deleteConversation(deleteId);
    });

    elements.conversationsList.appendChild(item);
  });
}

async function createNewConversation () {
  try {
    const response = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await response.json();

    state.currentConversationId = data.session_id;
    state.currentSessionName = data.session_name || null;  // 保存完整 session name
    closeSidebarOnMobile();
    elements.messagesContainer.innerHTML = '';
    elements.welcomeScreen.style.display = 'flex';
    elements.messageInput.value = '';
    elements.messageInput.focus();

    await loadConversations();
  } catch (error) {
    console.error('创建会话失败:', error);
    showError('创建会话失败');
  }
}

async function loadConversation (sessionId, sessionName = null) {
  try {
    state.currentConversationId = sessionId;
    state.currentSessionName = sessionName;  // 保存完整 session name
    closeSidebarOnMobile();

    // 构建 URL，包含 session_name 参数（如果有）
    let url = `/api/sessions/${sessionId}/messages`;
    if (sessionName) {
      url += `?session_name=${encodeURIComponent(sessionName)}`;
    }

    const response = await fetch(url);
    const data = await response.json();

    elements.messagesContainer.innerHTML = '';
    elements.welcomeScreen.style.display = 'none';

    // API 返回的可能是数组或 { messages: [...] }
    const messages = Array.isArray(data) ? data : (data.messages || []);

    if (messages.length > 0) {
      messages.forEach(msg => {
        // 处理思考链
        let thinking = null;
        if (msg.thoughts && Array.isArray(msg.thoughts)) {
          thinking = msg.thoughts.join('\n');
        } else if (msg.thinking) {
          thinking = msg.thinking;
        }
        // 传递 error_info 和 skipped 标志
        appendMessage(msg.role, msg.content, msg.images, thinking, msg.error_info, msg.skipped, msg.attachments, msg.timestamp, msg.thinking_duration_ms);
      });
    } else {
      elements.welcomeScreen.style.display = 'flex';
    }

    renderConversations();
    elements.messageInput.focus();
  } catch (error) {
    console.error('加载会话失败:', error);
    showError('加载会话失败');
  }
}

async function deleteConversation (sessionId) {
  const confirmed = await toast.confirm({
    title: '删除对话',
    message: '确定要删除这个对话吗？此操作无法撤销。',
    confirmText: '删除',
    cancelText: '取消',
    type: 'danger'
  });

  if (!confirmed) {
    return;
  }

  try {
    // sessionId 可能是完整的 session_name 或简短的 session_id
    await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE'
    });

    // 检查是否删除的是当前会话
    const isCurrentSession = state.currentConversationId === sessionId ||
      state.currentSessionName === sessionId ||
      (sessionId.includes('/') && sessionId.endsWith(state.currentConversationId));

    if (isCurrentSession) {
      state.currentConversationId = null;
      state.currentSessionName = null;
      elements.messagesContainer.innerHTML = '';
      elements.welcomeScreen.style.display = 'flex';
    }

    await loadConversations();
  } catch (error) {
    console.error('删除会话失败:', error);
    showError('删除会话失败');
  }
}

// 文件上传
function handleFileSelect (e) {
  const files = Array.from(e.target.files);
  state.uploadedFiles = files;
  renderFilePreview();
}

function renderFilePreview () {
  elements.filePreview.innerHTML = '';

  state.uploadedFiles.forEach((file, index) => {
    const item = document.createElement('div');
    item.className = 'file-preview-item';
    item.innerHTML = `
            <span>${escapeHtml(file.name)}</span>
            <button class="file-preview-remove" data-index="${index}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M18 6L6 18M6 6l12 12"/>
                </svg>
            </button>
        `;

    const removeBtn = item.querySelector('.file-preview-remove');
    removeBtn.addEventListener('click', () => {
      state.uploadedFiles.splice(index, 1);
      renderFilePreview();
    });

    elements.filePreview.appendChild(item);
  });
}

// 消息发送
async function sendMessage () {
  const message = elements.messageInput.value.trim();
  if (!message && state.uploadedFiles.length === 0) return;
  const hadUploads = state.uploadedFiles.length > 0;

  // 确保有会话
  if (!state.currentConversationId) {
    await createNewConversation();
  }

  elements.welcomeScreen.style.display = 'none';

  // 如果有文件，先上传
  let uploadedFileNames = [];
  const pendingAttachments = [];
  if (state.uploadedFiles.length > 0) {
    console.log('[DEBUG] 开始上传文件, session_id:', state.currentConversationId);

    try {
      for (const file of state.uploadedFiles) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('session_id', state.currentConversationId);
        if (state.currentSessionName) {
          formData.append('session_name', state.currentSessionName);
        }

        console.log('[DEBUG] 上传文件:', file.name, 'size:', file.size);

        const uploadResp = await fetch('/api/upload', {
          method: 'POST',
          body: formData
        });

        if (uploadResp.ok) {
          const result = await uploadResp.json();
          uploadedFileNames.push(file.name);
          pendingAttachments.push({
            file_id: result.file_id,
            file_name: file.name,
            mime_type: file.type || result.content_type,
            size: file.size,
            session_name: result.session_name || state.currentSessionName
          });
          console.log('[DEBUG] 文件上传成功:', result);
        } else {
          const errorText = await uploadResp.text();
          console.error('[DEBUG] 文件上传失败:', errorText);
        }
      }
    } catch (err) {
      console.error('[DEBUG] 文件上传错误:', err);
    }

    // 清空已上传的文件
    state.uploadedFiles = [];
    renderFilePreview();

    console.log('[DEBUG] 上传完成, 成功:', uploadedFileNames.length, '个文件');
  }

  if (!message && hadUploads && pendingAttachments.length === 0) {
    toast.error('文件上传失败，请重试');
    return;
  }

  // 构建消息内容
  let finalMessage = message;
  let displayMessage = message;

  // 如果有上传的文件，为模型添加文件提示，但不在 UI 中插入文件名
  if (pendingAttachments.length > 0) {
    finalMessage = message || '请结合我上传的文件进行分析。';
  }

  // 显示用户消息（包含文件信息）
  appendMessage('user', displayMessage || '', null, null, null, false, pendingAttachments, new Date().toISOString());

  // 清空输入
  elements.messageInput.value = '';
  elements.messageInput.style.height = 'auto';
  elements.messageInput.style.overflowY = 'hidden';
  updateSendButton();

  console.log('[DEBUG] 发送消息, session_id:', state.currentConversationId);

  // 显示加载指示器
  const loadingId = showTypingIndicator();

  let assistantMsgDiv = null;
  let contentDiv = null;
  let thinkingBlock = null;
  let thinkingTimer = null;
  let thinkingStart = null;

  try {
    const response = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: state.currentModel,
        messages: [{ role: 'user', content: finalMessage }],
        stream: true,
        session_id: state.currentConversationId,
        session_name: state.currentSessionName,
        file_ids: pendingAttachments.map(f => f.file_id).filter(Boolean),
        include_image_data: true,
        include_thoughts: true
      })
    });

    if (!response.ok) {
      removeTypingIndicator(loadingId);
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || error.message || '请求失败');
    }

    // 移除加载提示，准备渲染流式内容
    removeTypingIndicator(loadingId);

    // 预创建助手消息占位
    assistantMsgDiv = appendMessage('assistant', '', null, null, null, false, null, new Date().toISOString());
    const textEl = assistantMsgDiv.querySelector('.message-text');
    contentDiv = assistantMsgDiv.querySelector('.message-content');

    thinkingStart = Date.now();
    if (contentDiv) {
      thinkingBlock = createThinkingBlock('正在思考...', true, 0);
      contentDiv.insertBefore(thinkingBlock, contentDiv.firstChild);
      thinkingTimer = setInterval(() => {
        updateThinkingBlock(thinkingBlock, { isActive: true, durationMs: Date.now() - thinkingStart });
      }, 200);
      if (typeof lucide !== 'undefined') {
        lucide.createIcons({ nodes: [thinkingBlock] });
      }
    }

    const decoder = new TextDecoder();
    const reader = response.body.getReader();
    let buffer = '';
    let fullText = '';
    let imagesData = null;
    let thinkingParts = [];
    let done = false;

    const appendStreamChunk = (dataStr) => {
      if (dataStr === '[DONE]') {
        done = true;
        return;
      }
      let payload = null;
      try {
        payload = JSON.parse(dataStr);
      } catch (e) {
        console.warn('流数据解析失败:', dataStr);
        return;
      }

      if (payload.error) {
        throw new Error(payload.error.message || '请求失败');
      }

      const delta = payload.choices?.[0]?.delta || {};
      const deltaContent = delta.content || '';
      const deltaThought = delta.thought || '';
      const messageThoughts = payload.thoughts || payload.choices?.[0]?.message?.thoughts;

      if (deltaContent) {
        fullText += deltaContent;
        if (textEl) {
          textEl.textContent = fullText;
        }
      }

      if (deltaThought) {
        thinkingParts.push(deltaThought);
      }
      if (messageThoughts) {
        if (Array.isArray(messageThoughts)) {
          thinkingParts.push(...messageThoughts);
        } else {
          thinkingParts.push(messageThoughts);
        }
      }

      if (payload.images) {
        imagesData = payload.images;
      }
    };

    while (true) {
      const { value, done: streamDone } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !streamDone });

      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const rawEvent = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 2);
        if (!rawEvent.startsWith('data:')) continue;
        const dataStr = rawEvent.slice(5).trim();
        if (!dataStr) continue;
        appendStreamChunk(dataStr);
      }

      if (streamDone) break;
      if (done) break;
    }

    if (thinkingTimer) {
      clearInterval(thinkingTimer);
    }
    const thinkingDurationMs = thinkingStart ? (Date.now() - thinkingStart) : 0;

    // 流结束后渲染最终 Markdown
    if (textEl) {
      textEl.innerHTML = renderMarkdown(fullText);
      addCodeCopyButtons(textEl);
    }

    if (imagesData && imagesData.length > 0) {
      renderImagesForMessage(assistantMsgDiv, imagesData);
    }

    const thinkingContent = thinkingParts.length > 0 ? thinkingParts.join('\n') : null;
    if (contentDiv && thinkingBlock) {
      if (thinkingContent) {
        const newBlock = createThinkingBlock(thinkingContent, false, thinkingDurationMs);
        thinkingBlock.replaceWith(newBlock);
        thinkingBlock = newBlock;
      } else {
        updateThinkingBlock(thinkingBlock, {
          thinkingText: '模型未返回思考链',
          isActive: false,
          durationMs: thinkingDurationMs
        });
      }
    }

    if (assistantMsgDiv) {
      ensureAssistantActions(assistantMsgDiv, fullText);
    }

    if (assistantMsgDiv && typeof lucide !== 'undefined') {
      lucide.createIcons({ nodes: [assistantMsgDiv] });
    }

    // 更新会话列表
    await loadConversations();
  } catch (error) {
    if (thinkingTimer) {
      clearInterval(thinkingTimer);
    }
    if (contentDiv && thinkingBlock) {
      updateThinkingBlock(thinkingBlock, {
        thinkingText: '请求失败，未生成思考链',
        isActive: false,
        durationMs: thinkingStart ? (Date.now() - thinkingStart) : 0
      });
    }
    removeTypingIndicator(loadingId);
    console.error('发送消息失败:', error);

    if (error.message.includes('401') || error.message.includes('过期')) {
      showExpiredModal();
    } else {
      appendMessage('assistant', `错误: ${error.message}`, null, null, null, false, null, new Date().toISOString());
    }
  }
}

// 创建思考链显示块
function createThinkingBlock (thinking, isActive = false, thinkingDurationMs = null) {
  const block = document.createElement('div');
  block.className = 'thinking-block';
  block.dataset.durationMs = thinkingDurationMs != null ? thinkingDurationMs : '';
  block.dataset.active = isActive ? '1' : '0';

  const header = document.createElement('div');
  header.className = 'thinking-header';

  const icon = document.createElement('i');
  icon.setAttribute('data-lucide', 'sparkles');
  icon.className = `thinking-icon${isActive ? ' spinning' : ''}`;

  const titleSpan = document.createElement('span');
  titleSpan.className = `thinking-title${isActive ? ' thinking-active' : ''}`;
  titleSpan.textContent = `${isActive ? '正在思考...' : '显示思考过程'}${formatThinkingDuration(thinkingDurationMs)}`;

  const chevron = document.createElement('i');
  chevron.setAttribute('data-lucide', 'chevron-down');
  chevron.className = 'thinking-chevron';

  header.appendChild(icon);
  header.appendChild(titleSpan);
  header.appendChild(chevron);

  const content = document.createElement('div');
  content.className = 'thinking-content';

  const text = document.createElement('div');
  text.className = 'thinking-text';
  text.innerHTML = renderMarkdown(thinking);

  // 添加斜体提示文字
  const hint = document.createElement('div');
  hint.className = 'thinking-hint';
  hint.innerHTML = '<em>思考详情目前仅支持英语。</em>';

  content.appendChild(text);
  content.appendChild(hint);
  block.appendChild(header);
  block.appendChild(content);

  // 点击展开/收起，并切换标题文字
  header.addEventListener('click', () => {
    block.classList.toggle('expanded');
    const titleSpanEl = header.querySelector('.thinking-title');
    const isActiveNow = block.dataset.active === '1';
    if (!isActiveNow && titleSpanEl) {
      const base = block.classList.contains('expanded') ? '隐藏思考过程' : '显示思考过程';
      const durationText = formatThinkingDuration(block.dataset.durationMs ? Number(block.dataset.durationMs) : null);
      titleSpanEl.textContent = `${base}${durationText}`;
    }
  });

  return block;
}

function updateThinkingBlock (block, { thinkingText, isActive, durationMs }) {
  if (!block) return;
  const titleSpan = block.querySelector('.thinking-title');
  const icon = block.querySelector('.thinking-icon');
  const textDiv = block.querySelector('.thinking-text');

  if (durationMs != null && !isNaN(durationMs)) {
    block.dataset.durationMs = durationMs;
  }

  if (thinkingText !== undefined && textDiv) {
    textDiv.innerHTML = renderMarkdown(thinkingText);
  }

  const durationText = formatThinkingDuration(block.dataset.durationMs ? Number(block.dataset.durationMs) : null);

  if (isActive !== undefined) {
    block.dataset.active = isActive ? '1' : '0';
    if (titleSpan) {
      const base = isActive ? '正在思考...' : (block.classList.contains('expanded') ? '隐藏思考过程' : '显示思考过程');
      titleSpan.textContent = `${base}${durationText}`;
      titleSpan.classList.toggle('thinking-active', isActive);
    }
    if (icon) {
      icon.classList.toggle('spinning', isActive);
    }
  } else if (titleSpan) {
    const base = block.classList.contains('expanded') ? '隐藏思考过程' : '显示思考过程';
    titleSpan.textContent = `${base}${durationText}`;
  }

}

// 解析用户消息中的文件信息
function parseUserMessageFileInfo (content) {
  // 匹配格式: [已上传文件: xxx.png, yyy.pdf]\n消息内容
  const fileInfoRegex = /^\[已上传文件: ([^\]]+)\]\n?/;
  const match = content.match(fileInfoRegex);

  if (match) {
    const fileNames = match[1].split(', ').map(name => name.trim());
    const textContent = content.slice(match[0].length);
    return { fileNames, textContent };
  }

  return { fileNames: null, textContent: content };
}

// 消息显示
function appendMessage (role, content, images = null, thinking = null, errorInfo = null, isSkipped = false, attachments = null, timestampIso = null, thinkingDurationMs = null) {
  const messageDiv = document.createElement('div');
  messageDiv.className = `message ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.textContent = role === 'user' ? 'U' : 'G';

  const contentDiv = document.createElement('div');
  contentDiv.className = 'message-content';

  // 如果有思考链，先显示思考链
  if (role === 'assistant' && thinking) {
    const thinkingBlock = createThinkingBlock(thinking, false, thinkingDurationMs);
    contentDiv.appendChild(thinkingBlock);
  }

  // 解析用户消息中的文件信息
  let fileNames = null;
  let textContent = content;
  if (role === 'user' && content) {
    const parsed = parseUserMessageFileInfo(content);
    fileNames = parsed.fileNames;
    textContent = parsed.textContent;
  }

  // 创建气泡容器
  const bubbleDiv = document.createElement('div');
  bubbleDiv.className = 'message-bubble';

  const textDiv = document.createElement('div');
  textDiv.className = 'message-text';
  let hasTextContent = false;

  // 如果是跳过的消息（错误/策略违规），显示特殊格式
  if (role === 'assistant' && isSkipped && errorInfo) {
    bubbleDiv.className = 'message-bubble error-bubble';

    const errorContainer = document.createElement('div');
    errorContainer.className = 'error-message-container';

    // 第一行：图标 + 标题
    const errorHeader = document.createElement('div');
    errorHeader.className = 'error-message-header';
    errorHeader.innerHTML = `
      <i data-lucide="shield-alert" class="error-icon"></i>
      <span class="error-title">${escapeHtml(errorInfo.title)}</span>
    `;

    errorContainer.appendChild(errorHeader);

    // 第二行：详细原因（如果有）
    if (errorInfo.detail) {
      const errorDetail = document.createElement('div');
      errorDetail.className = 'error-message-detail';
      errorDetail.textContent = errorInfo.detail;
      errorContainer.appendChild(errorDetail);
    }

    textDiv.appendChild(errorContainer);
    hasTextContent = true;
  }
  // 用户消息使用纯文本，AI 回复使用 Markdown 渲染
  else if (role === 'assistant' && content) {
    textDiv.innerHTML = renderMarkdown(content);
    // 为代码块添加复制按钮
    addCodeCopyButtons(textDiv);
    hasTextContent = true;
  } else if (content) {
    textDiv.textContent = content;
    hasTextContent = true;
  }

  // 如果有附件但没有正文，给一个轻量提示文案
  if (!hasTextContent && attachments && attachments.length > 0) {
    textDiv.textContent = '已上传的文件';
    textDiv.classList.add('message-hint');
    hasTextContent = true;
  }

  if (hasTextContent) {
    bubbleDiv.appendChild(textDiv);
  }

  // 当没有正文也没有附件时，保持原有结构
  if (!hasTextContent && (!attachments || attachments.length === 0)) {
    bubbleDiv.appendChild(textDiv);
  }

  contentDiv.appendChild(bubbleDiv);

  // 附件预览区域 - 作为独立气泡显示在消息下方
  if (attachments && attachments.length > 0) {
    const fileBubbleDiv = document.createElement('div');
    fileBubbleDiv.className = 'message-bubble file-info-bubble';

    const attachmentsDiv = document.createElement('div');
    attachmentsDiv.className = 'message-attachments';

    attachments.forEach(att => {
      const item = document.createElement('div');
      item.className = 'attachment-item';

      // 根据文件名或 MIME 类型选择图标
      const fileName = att.file_name || att.name || att.file_id || '未命名文件';
      const mime = att.mime_type || att.mimeType || '';
      const ext = fileName.split('.').pop().toLowerCase();
      let iconName = 'file';
      if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico'].includes(ext) || mime.startsWith('image/')) {
        iconName = 'image';
      } else if (['pdf'].includes(ext) || mime === 'application/pdf') {
        iconName = 'file-text';
      } else if (['doc', 'docx'].includes(ext) || mime.includes('word')) {
        iconName = 'file-type';
      } else if (['xls', 'xlsx'].includes(ext) || mime.includes('spreadsheet') || mime.includes('excel')) {
        iconName = 'file-spreadsheet';
      } else if (['ppt', 'pptx'].includes(ext) || mime.includes('presentation') || mime.includes('powerpoint')) {
        iconName = 'presentation';
      } else if (['mp4', 'avi', 'mov', 'webm', 'mkv'].includes(ext) || mime.startsWith('video/')) {
        iconName = 'file-video';
      } else if (['mp3', 'wav', 'ogg', 'flac', 'aac'].includes(ext) || mime.startsWith('audio/')) {
        iconName = 'file-audio';
      } else if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext) || mime.includes('zip') || mime.includes('compressed')) {
        iconName = 'file-archive';
      } else if (['txt', 'md', 'json', 'xml', 'csv'].includes(ext) || mime.startsWith('text/')) {
        iconName = 'file-text';
      } else if (['js', 'ts', 'py', 'java', 'c', 'cpp', 'h', 'css', 'html', 'jsx', 'tsx'].includes(ext)) {
        iconName = 'file-code';
      }

      // 创建图标元素
      const iconEl = document.createElement('i');
      iconEl.setAttribute('data-lucide', iconName);
      iconEl.className = 'attachment-icon';

      // 创建信息容器
      const infoEl = document.createElement('div');
      infoEl.className = 'attachment-info';

      const nameEl = document.createElement('div');
      nameEl.className = 'attachment-name';
      nameEl.textContent = fileName;

      const metaEl = document.createElement('div');
      metaEl.className = 'attachment-meta';
      const mimeDisplay = mime || '未知类型';
      const sizeVal = att.byte_size ?? att.size;
      const metaParts = [mimeDisplay];
      if (sizeVal) {
        metaParts.push(formatFileSize(sizeVal));
      }
      metaEl.textContent = metaParts.join(' · ');

      infoEl.appendChild(nameEl);
      infoEl.appendChild(metaEl);
      item.appendChild(iconEl);
      item.appendChild(infoEl);
      attachmentsDiv.appendChild(item);
    });

    fileBubbleDiv.appendChild(attachmentsDiv);
    contentDiv.appendChild(fileBubbleDiv);
  }

  // 兼容旧格式：如果是用户消息且有文件信息（从文本解析），创建独立的文件信息气泡
  if (role === 'user' && fileNames && fileNames.length > 0 && (!attachments || attachments.length === 0)) {
    const fileBubbleDiv = document.createElement('div');
    fileBubbleDiv.className = 'message-bubble file-info-bubble';

    const fileInfoDiv = document.createElement('div');
    fileInfoDiv.className = 'file-info-content';

    fileNames.forEach(fileName => {
      const fileItem = document.createElement('div');
      fileItem.className = 'file-info-item';

      // 根据文件扩展名选择图标
      const ext = fileName.split('.').pop().toLowerCase();
      let iconName = 'file';
      if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp'].includes(ext)) {
        iconName = 'image';
      } else if (['pdf'].includes(ext)) {
        iconName = 'file-text';
      } else if (['doc', 'docx'].includes(ext)) {
        iconName = 'file-text';
      } else if (['xls', 'xlsx'].includes(ext)) {
        iconName = 'file-spreadsheet';
      } else if (['mp4', 'avi', 'mov', 'webm'].includes(ext)) {
        iconName = 'file-video';
      } else if (['mp3', 'wav', 'ogg'].includes(ext)) {
        iconName = 'file-audio';
      } else if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) {
        iconName = 'file-archive';
      }

      fileItem.innerHTML = `
        <i data-lucide="${iconName}" class="file-info-icon"></i>
        <span class="file-info-name">${escapeHtml(fileName)}</span>
      `;
      fileInfoDiv.appendChild(fileItem);
    });

    fileBubbleDiv.appendChild(fileInfoDiv);
    contentDiv.appendChild(fileBubbleDiv);
  }

  // 添加图片
  if (images && images.length > 0) {
    const imagesDiv = document.createElement('div');
    imagesDiv.className = 'message-images';

    images.forEach(img => {
      const imgWrapper = document.createElement('div');
      imgWrapper.className = 'message-image';

      const imgElement = document.createElement('img');
      imgElement.src = img.url || `data:image/png;base64,${img.data}`;
      imgElement.alt = 'Generated image';

      imgWrapper.appendChild(imgElement);
      imagesDiv.appendChild(imgWrapper);
    });

    contentDiv.appendChild(imagesDiv);
  }

  // AI 回答添加操作按钮（复制、下载）
  if (role === 'assistant' && content) {
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'message-actions';

    // 复制按钮
    const copyBtn = document.createElement('button');
    copyBtn.className = 'message-action-btn';
    copyBtn.title = '复制';
    copyBtn.innerHTML = `<i data-lucide="copy"></i>`;
    copyBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(content);
        copyBtn.innerHTML = `<i data-lucide="check"></i>`;
        copyBtn.classList.add('copied');
        setTimeout(() => {
          copyBtn.innerHTML = `<i data-lucide="copy"></i>`;
          copyBtn.classList.remove('copied');
          if (typeof lucide !== 'undefined') {
            lucide.createIcons({ nodes: [copyBtn] });
          }
        }, 2000);
        if (typeof lucide !== 'undefined') {
          lucide.createIcons({ nodes: [copyBtn] });
        }
      } catch (e) {
        console.error('复制失败:', e);
      }
    });

    // 下载按钮
    const downloadBtn = document.createElement('button');
    downloadBtn.className = 'message-action-btn';
    downloadBtn.title = '下载';
    downloadBtn.innerHTML = `<i data-lucide="download"></i>`;
    downloadBtn.addEventListener('click', () => {
      const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `gemini-response-${Date.now()}.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    });

    actionsDiv.appendChild(copyBtn);
    actionsDiv.appendChild(downloadBtn);
    contentDiv.appendChild(actionsDiv);
  }

  const tsDiv = document.createElement('div');
  tsDiv.className = 'message-timestamp';
  tsDiv.textContent = formatTimestamp(timestampIso);
  contentDiv.appendChild(tsDiv);

  messageDiv.appendChild(avatar);
  messageDiv.appendChild(contentDiv);

  elements.messagesContainer.appendChild(messageDiv);
  scrollToBottom();

  if (typeof lucide !== 'undefined') {
    lucide.createIcons({ nodes: [messageDiv] });
  }

  return messageDiv;
}

function renderImagesForMessage (messageDiv, images = []) {
  if (!messageDiv || !images || images.length === 0) return;

  const contentDiv = messageDiv.querySelector('.message-content');
  if (!contentDiv) return;

  let imagesDiv = messageDiv.querySelector('.message-images');
  if (!imagesDiv) {
    imagesDiv = document.createElement('div');
    imagesDiv.className = 'message-images';
    contentDiv.appendChild(imagesDiv);
  } else {
    imagesDiv.innerHTML = '';
  }

  images.forEach(img => {
    const imgWrapper = document.createElement('div');
    imgWrapper.className = 'message-image';

    const imgElement = document.createElement('img');
    if (img.local_path || img.file_name || img.file_id) {
      // 优先通过后端代理访问本地缓存
      const fileName = encodeURIComponent(img.file_name || img.file_id);
      imgElement.src = `/api/images/${fileName}`;
    } else if (img.download_uri) {
      imgElement.src = img.download_uri;
    } else if (img.url) {
      imgElement.src = img.url;
    } else if (img.data) {
      imgElement.src = `data:image/png;base64,${img.data}`;
    } else {
      return;
    }

    imgElement.alt = 'Generated image';
    imgWrapper.appendChild(imgElement);
    imagesDiv.appendChild(imgWrapper);
  });
}

function ensureAssistantActions (messageDiv, content) {
  if (!messageDiv || !content) return;
  if (messageDiv.querySelector('.message-actions')) return;

  const contentDiv = messageDiv.querySelector('.message-content');
  if (!contentDiv) return;

  const actionsDiv = document.createElement('div');
  actionsDiv.className = 'message-actions';

  // 复制按钮
  const copyBtn = document.createElement('button');
  copyBtn.className = 'message-action-btn';
  copyBtn.title = '复制';
  copyBtn.innerHTML = `<i data-lucide="copy"></i>`;
  copyBtn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(content);
      copyBtn.innerHTML = `<i data-lucide="check"></i>`;
      copyBtn.classList.add('copied');
      setTimeout(() => {
        copyBtn.innerHTML = `<i data-lucide="copy"></i>`;
        copyBtn.classList.remove('copied');
        if (typeof lucide !== 'undefined') {
          lucide.createIcons({ nodes: [copyBtn] });
        }
      }, 2000);
      if (typeof lucide !== 'undefined') {
        lucide.createIcons({ nodes: [copyBtn] });
      }
    } catch (e) {
      console.error('复制失败:', e);
    }
  });

  // 下载按钮
  const downloadBtn = document.createElement('button');
  downloadBtn.className = 'message-action-btn';
  downloadBtn.title = '下载';
  downloadBtn.innerHTML = `<i data-lucide="download"></i>`;
  downloadBtn.addEventListener('click', () => {
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `gemini-response-${Date.now()}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  });

  actionsDiv.appendChild(copyBtn);
  actionsDiv.appendChild(downloadBtn);
  contentDiv.appendChild(actionsDiv);
}

function showTypingIndicator () {
  const id = 'typing-' + Date.now();
  const messageDiv = document.createElement('div');
  messageDiv.className = 'message assistant';
  messageDiv.id = id;

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.textContent = 'G';

  const contentDiv = document.createElement('div');
  contentDiv.className = 'message-content';

  const tsDiv = document.createElement('div');
  tsDiv.className = 'message-timestamp';
  tsDiv.textContent = formatTimestamp();

  // 显示"正在思考"的动画效果
  const thinkingIndicator = document.createElement('div');
  thinkingIndicator.className = 'thinking-indicator';
  thinkingIndicator.innerHTML = `
    <i data-lucide="sparkles" class="thinking-icon spinning"></i>
    <span class="thinking-title thinking-active">正在思考...</span>
  `;

  contentDiv.appendChild(thinkingIndicator);
  contentDiv.appendChild(tsDiv);
  messageDiv.appendChild(avatar);
  messageDiv.appendChild(contentDiv);

  elements.messagesContainer.appendChild(messageDiv);
  scrollToBottom();

  return id;
}

function removeTypingIndicator (id) {
  const element = document.getElementById(id);
  if (element) {
    element.remove();
  }
}

function scrollToBottom () {
  elements.chatContainer.scrollTop = elements.chatContainer.scrollHeight;
}

// 工具函数
function formatTimestamp (ts) {
  const d = ts ? new Date(ts) : new Date();
  if (Number.isNaN(d.getTime())) return '';

  const pad = (n) => String(n).padStart(2, '0');
  const yyyy = d.getFullYear();
  const mm = pad(d.getMonth() + 1);
  const dd = pad(d.getDate());
  const hh = pad(d.getHours());
  const min = pad(d.getMinutes());
  const ss = pad(d.getSeconds());
  return `${yyyy}-${mm}-${dd} ${hh}:${min}:${ss}`;
}

function formatFileSize (bytes) {
  if (!bytes || isNaN(bytes)) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = bytes;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex++;
  }
  const displaySize = size % 1 === 0 ? size : size.toFixed(1);
  return `${displaySize} ${units[unitIndex]}`;
}

function formatThinkingDuration (ms) {
  if (ms == null || isNaN(ms)) return '';
  const seconds = Math.max(0, ms) / 1000;
  const precision = seconds >= 10 ? 1 : 2;
  return ` · 已思考${seconds.toFixed(precision)}s`;
}

function escapeHtml (text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function showError (message) {
  appendMessage('assistant', `错误: ${message}`, null, null, null, false, null, new Date().toISOString());
}

// ==================== 远程浏览器登录 ====================

// 远程浏览器状态
const browserState = {
  ws: null,
  connected: false,
  status: 'idle',
  lastUrl: null  // 记录上次的 URL，用于比较避免重复更新 DOM
};

function autoStartBrowserLogin () {
  if (state.autoBrowserStarted || browserState.connected) return;
  state.autoBrowserStarted = true;

  // 优先使用后端返回的 accountChooserUrl（包含正确的 group_id 和 csesidx）
  // 如果没有（首次登录），直接访问 business.gemini.google 让 Google 处理登录流程
  const startUrl = state.accountChooserUrl || 'https://business.gemini.google/';

  startBrowser({ useProfile: true, startUrl, auto: true });
}

// 打开登录模态框
function openLoginModal () {
  document.getElementById('expiredModal').classList.remove('show');
  document.getElementById('loginModal').classList.add('show');
}

// 关闭登录模态框
function closeLoginModal () {
  document.getElementById('loginModal').classList.remove('show');
  stopBrowser();
}

// 标签页切换
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const tabName = btn.dataset.tab;

    // 更新按钮状态
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    // 更新内容
    document.querySelectorAll('.tab-content').forEach(content => {
      content.classList.remove('active');
    });
    document.getElementById(tabName + 'Tab').classList.add('active');
  });
});

// 启动远程浏览器
document.getElementById('startBrowserBtn').addEventListener('click', () => startBrowser());
document.getElementById('stopBrowserBtn').addEventListener('click', stopBrowser);

// 手动保存配置
document.getElementById('saveManualBtn').addEventListener('click', saveManualConfig);

async function startBrowser (options = {}) {
  const { useProfile = false, auto = false } = options || {};
  let { startUrl = null } = options || {};

  // 优先使用后端返回的 accountChooserUrl（包含正确的 group_id 和 csesidx，用于 session 复用）
  // 如果没有（首次登录），直接访问 business.gemini.google 让 Google 处理登录流程
  if (!startUrl) {
    startUrl = state.accountChooserUrl || 'https://business.gemini.google/';
  }

  if (browserState.connected) return;

  const statusDiv = document.getElementById('browserStatus');
  const containerDiv = document.getElementById('browserContainer');
  const startBtn = document.getElementById('startBrowserBtn');
  const stopBtn = document.getElementById('stopBrowserBtn');
  const inputBox = document.getElementById('browserInput');

  statusDiv.innerHTML = auto ? '<p>检测到过期，正在自动启动远程浏览器...</p>' : '<p>正在连接...</p>';
  startBtn.disabled = true;

  try {
    // 获取 WebSocket URL
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const params = new URLSearchParams();
    if (useProfile) params.set('use_profile', '1');
    if (startUrl) params.set('start_url', startUrl);
    const qs = params.toString();
    const wsUrl = `${protocol}//${window.location.host}/ws/browser${qs ? `?${qs}` : ''}`;

    browserState.ws = new WebSocket(wsUrl);

    browserState.ws.onopen = () => {
      browserState.connected = true;
      statusDiv.innerHTML = '<p>浏览器启动中...</p>';
    };

    browserState.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      handleBrowserMessage(data);
    };

    browserState.ws.onclose = () => {
      browserState.connected = false;
      browserState.ws = null;
      state.autoBrowserStarted = false;
      containerDiv.style.display = 'none';
      statusDiv.style.display = 'block';
      statusDiv.innerHTML = '<p>浏览器已断开连接</p>';
      startBtn.style.display = 'inline-block';
      startBtn.disabled = false;
      stopBtn.style.display = 'none';
      inputBox.style.display = 'none';
      // 清空 browserUrl 显示
      const urlDiv = document.getElementById('browserUrl');
      if (urlDiv) {
        urlDiv.style.display = 'none';
        urlDiv.textContent = '';
      }
      // 重置上次 URL 记录
      browserState.lastUrl = null;
    };

    browserState.ws.onerror = (error) => {
      console.error('WebSocket 错误:', error);
      statusDiv.innerHTML = '<p>连接失败，请重试</p>';
      startBtn.disabled = false;
    };

  } catch (error) {
    console.error('启动浏览器失败:', error);
    statusDiv.innerHTML = `<p>启动失败: ${error.message}</p>`;
    startBtn.disabled = false;
    state.autoBrowserStarted = false;
  }
}

function stopBrowser () {
  if (browserState.ws) {
    browserState.ws.send(JSON.stringify({ action: 'stop' }));
    browserState.ws.close();
    browserState.ws = null;
  }
  state.autoBrowserStarted = false;
}

function handleBrowserMessage (data) {
  const statusDiv = document.getElementById('browserStatus');
  const containerDiv = document.getElementById('browserContainer');
  const screenImg = document.getElementById('browserScreen');
  const startBtn = document.getElementById('startBrowserBtn');
  const stopBtn = document.getElementById('stopBrowserBtn');
  const inputBox = document.getElementById('browserInput');
  const urlDiv = document.getElementById('browserUrl');

  switch (data.type) {
    case 'status':
      browserState.status = data.status;
      if (data.status === 'running') {
        statusDiv.style.display = 'none';
        containerDiv.style.display = 'block';
        startBtn.style.display = 'none';
        stopBtn.style.display = 'inline-block';
        inputBox.style.display = 'block';
        if (urlDiv) {
          urlDiv.style.display = 'none';
          urlDiv.textContent = '';
        }
      } else if (data.status === 'login_success') {
        statusDiv.style.display = 'block';
        statusDiv.innerHTML = `<p style="color: green;">${data.message}</p><button class="btn btn-primary" onclick="saveAndClose()">保存并关闭</button>`;
        if (urlDiv) {
          urlDiv.style.display = 'none';
          urlDiv.textContent = '';
        }
      } else if (data.status === 'waiting_group_id') {
        // 登录成功但缺少 group_id，提示用户操作
        statusDiv.style.display = 'block';
        statusDiv.innerHTML = `<p style="color: orange;">${data.message}</p>`;
        // 保持浏览器容器显示，让用户可以继续操作
        containerDiv.style.display = 'block';
        stopBtn.style.display = 'inline-block';
        inputBox.style.display = 'block';
      } else {
        statusDiv.innerHTML = `<p>${data.message}</p>`;
        if (urlDiv) {
          urlDiv.style.display = 'none';
          urlDiv.textContent = '';
        }
      }
      break;

    case 'screenshot':
      screenImg.src = 'data:image/jpeg;base64,' + data.data;
      // 只有当 URL 变化时才更新 DOM，避免重复操作造成性能消耗
      if (data.url && urlDiv && data.url !== browserState.lastUrl) {
        browserState.lastUrl = data.url;
        urlDiv.style.display = 'flex';
        urlDiv.innerHTML = `<i data-lucide="map-pin-check"></i><span>${data.url}</span>`;
      }
      break;

    case 'login_success':
      statusDiv.style.display = 'block';
      containerDiv.style.display = 'none';
      statusDiv.innerHTML = `
                <p style="color: green;">登录成功！</p>
                <button class="btn btn-primary" onclick="saveAndClose()">保存配置并关闭</button>
            `;
      break;

    case 'config_saved':
      if (data.success) {
        toast.success('配置已保存！页面将在 2 秒后刷新。');
        setTimeout(() => location.reload(), 2000);
      } else {
        toast.error(data.message, '保存失败');
      }
      break;
  }
}

function saveAndClose () {
  if (browserState.ws && browserState.connected) {
    browserState.ws.send(JSON.stringify({ action: 'save_config' }));
  }
}

// 浏览器屏幕点击处理
document.getElementById('browserScreen').addEventListener('click', (e) => {
  if (!browserState.ws || !browserState.connected) return;

  const rect = e.target.getBoundingClientRect();
  const scaleX = 1280 / rect.width;  // 假设服务器端浏览器宽度为 1280
  const scaleY = 800 / rect.height;  // 假设服务器端浏览器高度为 800

  const x = Math.round((e.clientX - rect.left) * scaleX);
  const y = Math.round((e.clientY - rect.top) * scaleY);

  browserState.ws.send(JSON.stringify({
    action: 'click',
    x: x,
    y: y
  }));
});

// 浏览器输入框处理
document.getElementById('browserInput').addEventListener('keydown', (e) => {
  if (!browserState.ws || !browserState.connected) return;

  if (e.key === 'Enter') {
    e.preventDefault();
    const text = e.target.value;
    if (text) {
      browserState.ws.send(JSON.stringify({
        action: 'type',
        text: text
      }));
      e.target.value = '';
    }
    // 同时发送 Enter 键
    browserState.ws.send(JSON.stringify({
      action: 'key',
      key: 'Enter'
    }));
  } else if (e.key === 'Escape') {
    browserState.ws.send(JSON.stringify({
      action: 'key',
      key: 'Escape'
    }));
  } else if (e.key === 'Tab') {
    e.preventDefault();
    browserState.ws.send(JSON.stringify({
      action: 'key',
      key: 'Tab'
    }));
  } else if (e.key === 'Backspace') {
    browserState.ws.send(JSON.stringify({
      action: 'key',
      key: 'Backspace'
    }));
  }
});

// 浏览器屏幕滚动处理
document.getElementById('browserScreen').addEventListener('wheel', (e) => {
  if (!browserState.ws || !browserState.connected) return;

  e.preventDefault();
  browserState.ws.send(JSON.stringify({
    action: 'scroll',
    deltaX: e.deltaX,
    deltaY: e.deltaY
  }));
});

// 手动保存配置
async function saveManualConfig () {
  const config = {
    secure_c_ses: document.getElementById('manualSecureCses').value.trim(),
    group_id: document.getElementById('manualGroupId').value.trim(),
    host_c_oses: document.getElementById('manualHostCoses').value.trim(),
    nid: document.getElementById('manualNid').value.trim(),
    csesidx: document.getElementById('manualCsesidx').value.trim()  // 可选
  };

  if (!config.secure_c_ses || !config.group_id) {
    toast.error('请填写必要字段：__Secure-C_SES 和 group_id');
    return;
  }

  // 显示加载提示
  const saveBtn = document.getElementById('saveManualBtn');
  const originalText = saveBtn.textContent;
  saveBtn.textContent = '保存中...';
  saveBtn.disabled = true;

  try {
    const response = await fetch('/api/session/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    });

    const result = await response.json();

    if (result.success) {
      toast.success('配置已保存！页面将在 2 秒后刷新。');
      setTimeout(() => location.reload(), 2000);
    } else {
      toast.error(result.error || '未知错误', '保存失败');
      saveBtn.textContent = originalText;
      saveBtn.disabled = false;
    }
  } catch (error) {
    toast.error(error.message, '保存失败');
    saveBtn.textContent = originalText;
    saveBtn.disabled = false;
  }
}

// 退出登录
async function logout () {
  const confirmed = await toast.confirm({
    title: '退出登录',
    message: '确定要退出登录吗？退出后需要重新登录才能使用。',
    confirmText: '退出',
    cancelText: '取消',
    type: 'warning'
  });

  if (!confirmed) {
    return;
  }

  try {
    const response = await fetch('/api/logout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });

    const result = await response.json();

    if (result.success) {
      toast.success('已退出登录，页面将在 2 秒后刷新。');
      setTimeout(() => location.reload(), 2000);
    } else {
      toast.error(result.error || '退出失败');
    }
  } catch (error) {
    toast.error(error.message, '退出失败');
  }
}

// 显示账号操作菜单
function showAccountMenu () {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';

    const dialog = document.createElement('div');
    dialog.className = 'confirm-dialog confirm-info';

    dialog.innerHTML = `
            <div class="confirm-header">
                <div class="confirm-icon">${ToastIcons.info}</div>
                <div class="confirm-title">当前账号</div>
            </div>
            <div class="confirm-body">
                <div class="confirm-message">${escapeHtml(state.currentUsername || '未知用户')}</div>
            </div>
            <div class="confirm-footer" style="flex-direction: column; gap: 8px;">
                <button class="btn btn-secondary account-menu-btn" data-action="relogin" style="width: 100%;">重新登录</button>
                <button class="btn btn-secondary account-menu-btn" data-action="logout" style="width: 100%; color: var(--error-color);">退出登录</button>
                <button class="btn btn-secondary account-menu-btn" data-action="close" style="width: 100%;">关闭</button>
            </div>
        `;

    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    requestAnimationFrame(() => {
      overlay.classList.add('show');
    });

    const close = (result) => {
      overlay.classList.remove('show');
      setTimeout(() => {
        document.body.removeChild(overlay);
        resolve(result);
      }, 200);
    };

    // 按钮点击事件
    dialog.querySelectorAll('.account-menu-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        close(btn.dataset.action);
      });
    });

    // 点击遮罩关闭
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        close('close');
      }
    });

    // ESC 键关闭
    const handleKeydown = (e) => {
      if (e.key === 'Escape') {
        close('close');
        document.removeEventListener('keydown', handleKeydown);
      }
    };
    document.addEventListener('keydown', handleKeydown);
  });
}

// 状态指示器点击显示菜单
document.getElementById('statusIndicator').addEventListener('click', async (e) => {
  e.stopPropagation();

  // 如果已登录，显示选项菜单
  if (state.currentUsername) {
    const action = await showAccountMenu();

    if (action === 'logout') {
      logout();
    } else if (action === 'relogin') {
      openLoginModal();
    }
    // action === 'close' 时不做任何操作
  } else {
    // 未登录，直接打开登录模态框
    openLoginModal();
  }
});

// 清理
window.addEventListener('beforeunload', () => {
  if (state.statusCheckInterval) {
    clearInterval(state.statusCheckInterval);
  }
  stopBrowser();
});
