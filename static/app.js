// 全局状态管理
const state = {
  currentConversationId: null,
  currentSessionName: null,  // Google API 的完整 session name
  conversations: [],
  currentModel: 'business-gemini',
  uploadedFiles: [],
  theme: localStorage.getItem('theme') || 'light',
  statusCheckInterval: null
};

// ==================== Lucide 图标自动渲染 ====================

// 使用 MutationObserver 监听 DOM 变化，自动渲染新添加的 Lucide 图标
(function initLucideObserver() {
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
function showToast(options) {
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

function removeToast(toast) {
  if (!toast || !toast.parentNode) return;

  toast.classList.add('toast-hiding');
  setTimeout(() => {
    if (toast.parentNode) {
      toast.parentNode.removeChild(toast);
    }
  }, 300);
}

// 便捷方法
function toast(message, type = 'info') {
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
function renderMarkdown(content) {
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
function addCodeCopyButtons(container) {
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
  statusIndicator: document.getElementById('statusIndicator'),
  expiredModal: document.getElementById('expiredModal')
};

// 模型列表缓存
let modelsList = [];

// 初始化
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initEventListeners();
  loadModels();
  loadConversations();
  checkStatus();
  startStatusMonitoring();
});

// 主题切换
function initTheme() {
  document.documentElement.setAttribute('data-theme', state.theme);
}

function toggleTheme() {
  state.theme = state.theme === 'light' ? 'dark' : 'light';
  localStorage.setItem('theme', state.theme);
  document.documentElement.setAttribute('data-theme', state.theme);
}

// 事件监听
function initEventListeners() {
  elements.themeToggle.addEventListener('click', toggleTheme);
  elements.sidebarToggle.addEventListener('click', toggleSidebar);
  elements.newChatBtn.addEventListener('click', createNewConversation);
  elements.sendBtn.addEventListener('click', sendMessage);
  elements.attachBtn.addEventListener('click', () => elements.fileInput.click());
  elements.fileInput.addEventListener('change', handleFileSelect);

  // 模型选择器点击事件
  elements.modelSelector.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleModelDropdown();
  });

  // 输入框自动调整高度
  const messageInput = elements.messageInput;
  const lineHeight = 24;
  const maxLines = 5;
  const maxHeight = lineHeight * maxLines; // 5行高度 = 120px

  function autoResize() {
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

function toggleSidebar() {
  elements.sidebar.classList.toggle('hidden');
}

function updateSendButton() {
  const hasText = elements.messageInput.value.trim().length > 0;
  elements.sendBtn.disabled = !hasText;
}

// 状态检查
async function checkStatus() {
  try {
    const response = await fetch('/api/status');
    const data = await response.json();

    const statusDot = elements.statusIndicator.querySelector('.status-dot');
    const statusText = elements.statusIndicator.querySelector('.status-text');

    elements.statusIndicator.className = 'status-indicator';

    // 保存 signout_url 到全局状态
    state.signoutUrl = data.signout_url || null;
    state.currentUsername = data.username || null;

    // 仅在 expired 或 logged_in=false 时弹过期模态框
    // warning=true 时只提示状态异常，不弹模态
    if (data.expired || data.logged_in === false) {
      elements.statusIndicator.classList.add('error');
      statusText.textContent = '登录已过期';
      showExpiredModal();
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
      statusText.textContent = '未登录';
    }
  } catch (error) {
    console.error('状态检查失败:', error);
    elements.statusIndicator.classList.add('error');
    elements.statusIndicator.querySelector('.status-text').textContent = '检查失败';
  }
}

function startStatusMonitoring() {
  // 每分钟检查一次状态
  state.statusCheckInterval = setInterval(checkStatus, 60000);
}

function showExpiredModal() {
  elements.expiredModal.classList.add('show');
}

// 模型加载
async function loadModels() {
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
function updateModelDisplay() {
  const model = modelsList.find(m => m.id === state.currentModel);
  if (model) {
    elements.modelName.textContent = model.name;
  }
}

// 模型下拉浮窗
let modelDropdown = null;

function toggleModelDropdown() {
  if (modelDropdown) {
    closeModelDropdown();
  } else {
    openModelDropdown();
  }
}

function openModelDropdown() {
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
  modelDropdown.style.left = rect.left + 'px';
  modelDropdown.style.bottom = (window.innerHeight - rect.top + 8) + 'px';

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

function closeModelDropdown() {
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

function handleDropdownOutsideClick(e) {
  if (modelDropdown && !modelDropdown.contains(e.target) && !elements.modelSelector.contains(e.target)) {
    closeModelDropdown();
  }
}

// 会话管理
async function loadConversations() {
  try {
    const response = await fetch('/api/sessions');
    const sessions = await response.json();

    state.conversations = sessions;
    renderConversations();
  } catch (error) {
    console.error('加载会话失败:', error);
  }
}

function renderConversations() {
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

async function createNewConversation() {
  try {
    const response = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await response.json();

    state.currentConversationId = data.session_id;
    state.currentSessionName = data.session_name || null;  // 保存完整 session name
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

async function loadConversation(sessionId, sessionName = null) {
  try {
    state.currentConversationId = sessionId;
    state.currentSessionName = sessionName;  // 保存完整 session name

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
        appendMessage(msg.role, msg.content, msg.images, thinking);
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

async function deleteConversation(sessionId) {
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
function handleFileSelect(e) {
  const files = Array.from(e.target.files);
  state.uploadedFiles = files;
  renderFilePreview();
}

function renderFilePreview() {
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
async function sendMessage() {
  const message = elements.messageInput.value.trim();
  if (!message && state.uploadedFiles.length === 0) return;

  // 确保有会话
  if (!state.currentConversationId) {
    await createNewConversation();
  }

  elements.welcomeScreen.style.display = 'none';

  // 如果有文件，先上传
  let uploadedFileNames = [];
  let uploadResults = [];
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
          uploadResults.push(result);
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

  // 构建消息内容
  let finalMessage = message;
  let displayMessage = message;

  // 如果有上传的文件，在消息中添加提示
  if (uploadedFileNames.length > 0) {
    const fileList = uploadedFileNames.join(', ');
    if (!message) {
      finalMessage = `请分析上传的文件: ${fileList}`;
      displayMessage = `[已上传文件: ${fileList}]\n请分析这些文件`;
    } else {
      // 在消息前添加文件信息
      displayMessage = `[已上传文件: ${fileList}]\n${message}`;
    }
  }

  // 显示用户消息（包含文件信息）
  appendMessage('user', displayMessage);

  // 清空输入
  elements.messageInput.value = '';
  elements.messageInput.style.height = 'auto';
  elements.messageInput.style.overflowY = 'hidden';
  updateSendButton();

  console.log('[DEBUG] 发送消息, session_id:', state.currentConversationId);

  // 显示加载指示器
  const loadingId = showTypingIndicator();

  try {
    const response = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: state.currentModel,
        messages: [{ role: 'user', content: finalMessage }],
        stream: false,
        session_id: state.currentConversationId,
        session_name: state.currentSessionName,
        include_image_data: true
      })
    });

    removeTypingIndicator(loadingId);

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || '请求失败');
    }

    const data = await response.json();

    // 用响应中的 canonical session_id/session_name 更新 state（避免刷新后 ID 不匹配）
    if (data.session_id && data.session_id !== state.currentConversationId) {
      console.log('[DEBUG] 更新 session_id:', state.currentConversationId, '->', data.session_id);
      state.currentConversationId = data.session_id;
    }
    if (data.session_name) {
      state.currentSessionName = data.session_name;
    }

    const rawContent = data.choices[0].message.content;

    // 处理 content 可能是数组或字符串的情况
    let textContent = '';
    let inlineImages = [];

    if (Array.isArray(rawContent)) {
      // content 是数组格式 [{type: "text", text: "..."}, {type: "image_url", image_url: {...}}]
      rawContent.forEach(item => {
        if (item.type === 'text' && item.text) {
          textContent += item.text;
        } else if (item.type === 'image_url' && item.image_url) {
          inlineImages.push({
            url: item.image_url.url
          });
        }
      });
    } else {
      // content 是字符串
      textContent = rawContent;
    }

    // 合并 inline images 和 response images
    let allImages = [...inlineImages];
    if (data.images && data.images.length > 0) {
      data.images.forEach(img => {
        // 处理 images 数组中的图片，优先使用 local_path 通过服务器代理访问
        if (img.local_path) {
          allImages.push({
            url: `/api/images/${encodeURIComponent(img.file_name || img.file_id)}`
          });
        } else if (img.download_uri) {
          allImages.push({ url: img.download_uri });
        }
      });
    }

    // 获取思考链内容（如果有）
    let thinkingContent = null;
    const message = data.choices[0].message;
    if (message.thoughts && Array.isArray(message.thoughts)) {
      // thoughts 是数组，合并为字符串
      thinkingContent = message.thoughts.join('\n');
    } else if (message.thinking) {
      thinkingContent = message.thinking;
    } else if (data.thinking) {
      thinkingContent = data.thinking;
    }

    appendMessage('assistant', textContent, allImages, thinkingContent);

    // 更新会话列表
    await loadConversations();
  } catch (error) {
    removeTypingIndicator(loadingId);
    console.error('发送消息失败:', error);

    if (error.message.includes('401') || error.message.includes('过期')) {
      showExpiredModal();
    } else {
      appendMessage('assistant', `错误: ${error.message}`);
    }
  }
}

// 创建思考链显示块
function createThinkingBlock(thinking, isActive = false) {
  const block = document.createElement('div');
  block.className = 'thinking-block';

  const header = document.createElement('div');
  header.className = 'thinking-header';
  header.innerHTML = `
    <i data-lucide="sparkles" class="thinking-icon${isActive ? ' spinning' : ''}"></i>
    <span class="thinking-title${isActive ? ' thinking-active' : ''}">${isActive ? '正在思考...' : '显示思考过程'}</span>
    <i data-lucide="chevron-down" class="thinking-chevron"></i>
  `;

  const content = document.createElement('div');
  content.className = 'thinking-content';

  const text = document.createElement('div');
  text.className = 'thinking-text';
  text.textContent = thinking;

  content.appendChild(text);
  block.appendChild(header);
  block.appendChild(content);

  // 点击展开/收起
  header.addEventListener('click', () => {
    block.classList.toggle('expanded');
  });

  return block;
}

// 消息显示
function appendMessage(role, content, images = null, thinking = null) {
  const messageDiv = document.createElement('div');
  messageDiv.className = `message ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.textContent = role === 'user' ? 'U' : 'G';

  const contentDiv = document.createElement('div');
  contentDiv.className = 'message-content';

  // 如果有思考链，先显示思考链
  if (role === 'assistant' && thinking) {
    const thinkingBlock = createThinkingBlock(thinking);
    contentDiv.appendChild(thinkingBlock);
  }

  // 创建气泡容器
  const bubbleDiv = document.createElement('div');
  bubbleDiv.className = 'message-bubble';

  const textDiv = document.createElement('div');
  textDiv.className = 'message-text';

  // 用户消息使用纯文本，AI 回复使用 Markdown 渲染
  if (role === 'assistant' && content) {
    textDiv.innerHTML = renderMarkdown(content);
    // 为代码块添加复制按钮
    addCodeCopyButtons(textDiv);
  } else {
    textDiv.textContent = content;
  }

  bubbleDiv.appendChild(textDiv);
  contentDiv.appendChild(bubbleDiv);

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

  const timestamp = document.createElement('div');
  timestamp.className = 'message-timestamp';
  timestamp.textContent = new Date().toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit'
  });
  contentDiv.appendChild(timestamp);

  messageDiv.appendChild(avatar);
  messageDiv.appendChild(contentDiv);

  elements.messagesContainer.appendChild(messageDiv);
  scrollToBottom();
}

function showTypingIndicator() {
  const id = 'typing-' + Date.now();
  const messageDiv = document.createElement('div');
  messageDiv.className = 'message assistant';
  messageDiv.id = id;

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.textContent = 'G';

  const contentDiv = document.createElement('div');
  contentDiv.className = 'message-content';

  // 显示"正在思考"的动画效果
  const thinkingIndicator = document.createElement('div');
  thinkingIndicator.className = 'thinking-indicator';
  thinkingIndicator.innerHTML = `
    <i data-lucide="sparkles" class="thinking-icon spinning"></i>
    <span class="thinking-title thinking-active">正在思考...</span>
  `;

  contentDiv.appendChild(thinkingIndicator);
  messageDiv.appendChild(avatar);
  messageDiv.appendChild(contentDiv);

  elements.messagesContainer.appendChild(messageDiv);
  scrollToBottom();

  return id;
}

function removeTypingIndicator(id) {
  const element = document.getElementById(id);
  if (element) {
    element.remove();
  }
}

function scrollToBottom() {
  elements.chatContainer.scrollTop = elements.chatContainer.scrollHeight;
}

// 工具函数
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function showError(message) {
  appendMessage('assistant', `错误: ${message}`);
}

// ==================== 远程浏览器登录 ====================

// 远程浏览器状态
const browserState = {
  ws: null,
  connected: false,
  status: 'idle'
};

// 打开登录模态框
function openLoginModal() {
  document.getElementById('expiredModal').classList.remove('show');
  document.getElementById('loginModal').classList.add('show');
}

// 关闭登录模态框
function closeLoginModal() {
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
document.getElementById('startBrowserBtn').addEventListener('click', startBrowser);
document.getElementById('stopBrowserBtn').addEventListener('click', stopBrowser);

// 手动保存配置
document.getElementById('saveManualBtn').addEventListener('click', saveManualConfig);

async function startBrowser() {
  const statusDiv = document.getElementById('browserStatus');
  const containerDiv = document.getElementById('browserContainer');
  const startBtn = document.getElementById('startBrowserBtn');
  const stopBtn = document.getElementById('stopBrowserBtn');
  const inputBox = document.getElementById('browserInput');

  statusDiv.innerHTML = '<p>正在连接...</p>';
  startBtn.disabled = true;

  try {
    // 获取 WebSocket URL
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/browser`;

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
      containerDiv.style.display = 'none';
      statusDiv.style.display = 'block';
      statusDiv.innerHTML = '<p>浏览器已断开连接</p>';
      startBtn.style.display = 'inline-block';
      startBtn.disabled = false;
      stopBtn.style.display = 'none';
      inputBox.style.display = 'none';
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
  }
}

function stopBrowser() {
  if (browserState.ws) {
    browserState.ws.send(JSON.stringify({ action: 'stop' }));
    browserState.ws.close();
    browserState.ws = null;
  }
}

function handleBrowserMessage(data) {
  const statusDiv = document.getElementById('browserStatus');
  const containerDiv = document.getElementById('browserContainer');
  const screenImg = document.getElementById('browserScreen');
  const startBtn = document.getElementById('startBrowserBtn');
  const stopBtn = document.getElementById('stopBrowserBtn');
  const inputBox = document.getElementById('browserInput');

  switch (data.type) {
    case 'status':
      browserState.status = data.status;
      if (data.status === 'running') {
        statusDiv.style.display = 'none';
        containerDiv.style.display = 'block';
        startBtn.style.display = 'none';
        stopBtn.style.display = 'inline-block';
        inputBox.style.display = 'block';
      } else if (data.status === 'login_success') {
        statusDiv.style.display = 'block';
        statusDiv.innerHTML = `<p style="color: green;">${data.message}</p><button class="btn btn-primary" onclick="saveAndClose()">保存并关闭</button>`;
      } else {
        statusDiv.innerHTML = `<p>${data.message}</p>`;
      }
      break;

    case 'screenshot':
      screenImg.src = 'data:image/jpeg;base64,' + data.data;
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

function saveAndClose() {
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
async function saveManualConfig() {
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
async function logout() {
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
function showAccountMenu() {
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
