# Business Gemini API 服务

生产级的 Google Business Gemini API 服务，提供 OpenAI 兼容的聊天接口和 Web 管理界面。

## 特性

- ✅ **OpenAI 兼容 API** - 支持标准 OpenAI Chat Completions API 格式
- ✅ **统一配置管理** - 单一 `config.json` 文件管理所有配置
- ✅ **环境变量支持** - 通过环境变量覆盖配置，适合容器化部署
- ✅ **配置热重载** - 修改配置文件无需重启服务即可生效
- ✅ **跨平台服务管理** - Windows 和 Linux 统一的管理脚本
- ✅ **Web 端登录** - Linux 环境支持浏览器自动登录
- ✅ **代理支持** - 支持 HTTP/SOCKS5/SOCKS5H 代理
- ✅ **会话管理** - 多会话支持，历史消息同步
- ✅ **图片生成** - 支持 Gemini 图片生成功能

## 快速开始

### 1. 环境准备

```bash
# 克隆项目（或下载项目文件）
cd /path/to/gemini

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器（用于登录）
playwright install chromium
```

### 2. 配置

复制配置模板并编辑：

```bash
# 复制配置文件模板
cp config.example.json config.json

# 编辑配置文件
# Windows:
notepad config.json
# Linux/Mac:
vim config.json
```

**配置说明**：
```json
{
  "server": {
    "host": "0.0.0.0",      // 绑定地址
    "port": 8000,           // 绑定端口
    "workers": 4,           // Worker 进程数
    "log_level": "INFO"     // 日志级别
  },
  "proxy": {
    "enabled": true,        // 是否启用代理
    "url": "socks5h://127.0.0.1:10808",  // 代理地址
    "timeout": 30           // 代理超时（秒）
  },
  "session": {
    // 登录后自动填充，无需手动配置
  }
}
```

### 3. 登录

首次使用需要登录 Google Business Gemini:

```bash
# Windows:
python app.py login

# Linux/Mac:
python3 app.py login
```

登录成功后，会话信息会自动保存到 `config.json` 的 `session` 部分。

### 4. 启动服务

#### Windows

```cmd
manage.bat start          # 启动服务
manage.bat status         # 查看状态
manage.bat logs           # 查看日志
manage.bat restart        # 重启服务
manage.bat stop           # 停止服务
```

#### Linux/Mac

```bash
chmod +x manage.sh        # 赋予执行权限（首次）
./manage.sh start         # 启动服务
./manage.sh status        # 查看状态
./manage.sh logs          # 查看日志
./manage.sh reload        # 重载配置（不重启）
./manage.sh restart       # 重启服务
./manage.sh stop          # 停止服务
```

### 5. 访问服务

启动后访问：
- **Web 界面**: http://localhost:8000
- **API 端点**: http://localhost:8000/v1/chat/completions
- **API 文档**: http://localhost:8000/docs

## 主要 API 端点

### OpenAI 兼容 API

```bash
POST /v1/chat/completions
```

示例请求：
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "business-gemini",
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'
```

### 配置管理 API

```bash
POST /api/config/reload   # 手动重载配置
GET  /api/status          # 获取登录状态
```

### Web 登录 API

```bash
POST /api/login/start     # 启动浏览器登录
GET  /api/login/status    # 查询登录状态
POST /api/login/cancel    # 取消登录
```

### 会话管理 API

```bash
GET    /api/sessions             # 列出所有会话
POST   /api/sessions             # 创建新会话
GET    /api/sessions/{id}/messages  # 获取会话历史
DELETE /api/sessions/{id}        # 删除会话
```

## 环境变量配置

除了 `config.json`，还可以通过环境变量配置（优先级更高）：

```bash
# 服务器配置
export SERVER_HOST=0.0.0.0
export SERVER_PORT=8000
export SERVER_WORKERS=4
export SERVER_LOG_LEVEL=INFO

# 代理配置
export PROXY_URL=socks5h://127.0.0.1:10808

# 会话配置（通常不需要手动设置）
export BIZ_GEMINI_SECURE_C_SES=...
export BIZ_GEMINI_GROUP_ID=...
```

或使用 `.env` 文件（复制 `.env.example` 为 `.env`）：

```bash
cp .env.example .env
vim .env
```

## 配置热重载

服务支持配置热重载，修改 `config.json` 后：

**自动重载**（推荐）:
- 保存文件后会自动检测并重载配置
- 新配置会在下次请求时生效

**手动重载**:
```bash
# Linux/Mac
./manage.sh reload

# Windows（不支持热重载，需要重启）
manage.bat restart

# 或通过 API
curl -X POST http://localhost:8000/api/config/reload
```

## Linux 环境 Web 端登录

在 Linux 服务器（无桌面环境）上，可以通过 Web 界面完成登录：

1. 访问 Web 界面
2. 点击"重新登录"按钮
3. 后台会启动 headless 浏览器
4. 按提示完成登录流程
5. 登录成功后会自动更新配置

或通过 API：
```bash
# 启动登录（headless 模式）
curl -X POST http://localhost:8000/api/login/start?headless=true

# 查询登录状态
curl http://localhost:8000/api/login/status
```

## 日志

日志文件位于 `log/` 目录：
- `access.log` - 访问日志
- `error.log` - 错误日志

查看实时日志：
```bash
# Linux/Mac
./manage.sh logs [access|error]

# Windows
manage.bat logs [access|error]

# 或直接查看文件
tail -f log/error.log
```

## 故障排除

### 1. 启动失败

检查依赖是否完整：
```bash
pip install -r requirements.txt
playwright install chromium
```

查看错误日志：
```bash
cat log/error.log
```

### 2. 登录失败

- 检查代理是否正常运行
- 确保浏览器驱动已安装：`playwright install chromium`
- 查看详细错误信息

### 3. 代理问题

编辑 `config.json`，设置 `proxy.enabled` 为 `false` 以禁用代理：
```json
{
  "proxy": {
    "enabled": false
  }
}
```

### 4. 配置重载不生效

Linux/Mac 使用 `./manage.sh reload`，Windows 需要重启服务。

## 开发模式

直接运行服务（用于开发和调试）：

```bash
python server.py
# 或
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

## 生产部署建议

1. **使用 Gunicorn**（推荐）
   - 脚本会自动使用 Gunicorn 如果已安装
   - 多 worker 支持，提高并发性能

2. **反向代理**
   - 建议使用 Nginx 作为反向代理
   - 配置 SSL/TLS 证书

3. **进程管理**
   - Linux 可使用 systemd 管理服务
   - 或使用 supervisor

4. **日志轮转**
   - 配置 logrotate 防止日志文件过大

5. **监控**
   - 使用 Prometheus + Grafana 监控服务状态
   - 配置告警规则

## 许可证

MIT License

## 支持

如有问题，请提交 Issue 或联系开发者。
