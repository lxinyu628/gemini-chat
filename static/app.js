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

// 配置 marked（Markdown 渲染器）
if (typeof marked !== 'undefined') {
    marked.setOptions({
        highlight: function(code, lang) {
            if (typeof hljs !== 'undefined' && lang && hljs.getLanguage(lang)) {
                try {
                    return hljs.highlight(code, { language: lang }).value;
                } catch (e) {}
            }
            // 自动检测语言
            if (typeof hljs !== 'undefined') {
                try {
                    return hljs.highlightAuto(code).value;
                } catch (e) {}
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
    modelSelect: document.getElementById('modelSelect'),
    themeToggle: document.getElementById('themeToggle'),
    statusIndicator: document.getElementById('statusIndicator'),
    expiredModal: document.getElementById('expiredModal')
};

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
    elements.modelSelect.addEventListener('change', (e) => {
        state.currentModel = e.target.value;
    });

    // 输入框自动调整高度
    elements.messageInput.addEventListener('input', () => {
        elements.messageInput.style.height = 'auto';
        elements.messageInput.style.height = elements.messageInput.scrollHeight + 'px';
        updateSendButton();
    });

    // 回车发送消息
    elements.messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (!elements.sendBtn.disabled) {
                sendMessage();
            }
        }
    });
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

        if (data.logged_in === false || data.expired) {
            elements.statusIndicator.classList.add('error');
            statusText.textContent = '登录已过期';
            showExpiredModal();
        } else if (data.warning) {
            elements.statusIndicator.classList.add('warning');
            statusText.textContent = data.message || '状态异常';
        } else if (data.logged_in) {
            elements.statusIndicator.classList.add('online');
            // 显示用户名或简单的"已登录"状态
            if (data.username) {
                statusText.textContent = data.username;
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
            elements.modelSelect.innerHTML = '';
            data.data.forEach(model => {
                const option = document.createElement('option');
                option.value = model.id;
                // 显示名称，description 作为 title 提示
                option.textContent = model.name || model.id;
                if (model.description) {
                    option.title = model.description;
                }
                elements.modelSelect.appendChild(option);
            });
            state.currentModel = 'auto';
        }
    } catch (error) {
        console.error('加载模型失败:', error);
        // 加载失败时使用默认模型列表
        const defaultModels = [
            { id: 'auto', name: '自动选择', description: '自动选择最佳模型' },
            { id: 'gemini-2.5-flash', name: 'Gemini 2.5 Flash', description: '快速响应' },
            { id: 'gemini-2.5-pro', name: 'Gemini 2.5 Pro', description: '更强推理能力' },
            { id: 'business-gemini', name: 'Business Gemini', description: '企业版' }
        ];

        elements.modelSelect.innerHTML = '';
        defaultModels.forEach(model => {
            const option = document.createElement('option');
            option.value = model.id;
            option.textContent = model.name;
            option.title = model.description;
            elements.modelSelect.appendChild(option);
        });

        state.currentModel = 'auto';
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
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                    </svg>
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

        if (data.messages && data.messages.length > 0) {
            data.messages.forEach(msg => {
                appendMessage(msg.role, msg.content, msg.images);
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
    if (!confirm('确定要删除这个对话吗？')) {
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
    if (!message) return;

    // 确保有会话
    if (!state.currentConversationId) {
        await createNewConversation();
    }

    elements.welcomeScreen.style.display = 'none';

    // 显示用户消息
    appendMessage('user', message);

    // 清空输入
    elements.messageInput.value = '';
    elements.messageInput.style.height = 'auto';
    updateSendButton();

    // 显示加载指示器
    const loadingId = showTypingIndicator();

    try {
        const response = await fetch('/v1/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: state.currentModel,
                messages: [{ role: 'user', content: message }],
                stream: false,
                session_id: state.currentConversationId,
                include_image_data: true
            })
        });

        removeTypingIndicator(loadingId);

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '请求失败');
        }

        const data = await response.json();
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

        appendMessage('assistant', textContent, allImages);

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

// 消息显示
function appendMessage(role, content, images = null) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = role === 'user' ? 'U' : 'G';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

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

    const typingDiv = document.createElement('div');
    typingDiv.className = 'typing-indicator';
    typingDiv.innerHTML = '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';

    contentDiv.appendChild(typingDiv);
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
                alert('配置已保存！页面将刷新。');
                location.reload();
            } else {
                alert('保存失败: ' + data.message);
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
        csesidx: document.getElementById('manualCsesidx').value.trim(),
        group_id: document.getElementById('manualGroupId').value.trim(),
        host_c_oses: document.getElementById('manualHostCoses').value.trim()
    };

    if (!config.secure_c_ses || !config.csesidx || !config.group_id) {
        alert('请填写必要字段：secure_c_ses, csesidx, group_id');
        return;
    }

    try {
        const response = await fetch('/api/session/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        const result = await response.json();

        if (result.success) {
            alert('配置已保存！页面将刷新。');
            location.reload();
        } else {
            alert('保存失败: ' + (result.error || '未知错误'));
        }
    } catch (error) {
        alert('保存失败: ' + error.message);
    }
}

// 状态指示器点击打开登录
document.getElementById('statusIndicator').addEventListener('click', () => {
    openLoginModal();
});

// 清理
window.addEventListener('beforeunload', () => {
    if (state.statusCheckInterval) {
        clearInterval(state.statusCheckInterval);
    }
    stopBrowser();
});
