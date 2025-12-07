#!/bin/bash

# Business Gemini 服务管理脚本
# 支持: start, stop, restart, status, reload, logs

set -e

# 默认配置（可通过环境变量覆盖）
DEFAULT_HOST="0.0.0.0"
DEFAULT_PORT="8000"
DEFAULT_WORKERS="4"
DEFAULT_LOG_LEVEL="INFO"

# 项目目录
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="${PROJECT_DIR}/config.json"
LOG_DIR="${PROJECT_DIR}/log"
PID_FILE="${LOG_DIR}/server.pid"
ACCESS_LOG="${LOG_DIR}/access.log"
ERROR_LOG="${LOG_DIR}/error.log"
VENV_DIR="${PROJECT_DIR}/venv"

# 确保日志目录存在
mkdir -p "$LOG_DIR"

# 颜色输出
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 从配置文件读取配置
read_config() {
    if [ -f "$CONFIG_FILE" ]; then
        # 使用 Python 读取 JSON 配置
        python3 -c "
import json
try:
    with open('$CONFIG_FILE', 'r') as f:
        cfg = json.load(f)
    server = cfg.get('server', {})
    print(f\"{server.get('host', '$DEFAULT_HOST')}|{server.get('port', $DEFAULT_PORT)}|{server.get('workers', $DEFAULT_WORKERS)}|{server.get('log_level', '$DEFAULT_LOG_LEVEL')}\")
except:
    print('$DEFAULT_HOST|$DEFAULT_PORT|$DEFAULT_WORKERS|$DEFAULT_LOG_LEVEL')
" 2>/dev/null || echo "$DEFAULT_HOST|$DEFAULT_PORT|$DEFAULT_WORKERS|$DEFAULT_LOG_LEVEL"
    else
        echo "$DEFAULT_HOST|$DEFAULT_PORT|$DEFAULT_WORKERS|$DEFAULT_LOG_LEVEL"
    fi
}

# 激活虚拟环境
activate_venv() {
    if [ -d "$VENV_DIR" ]; then
        echo -e "${BLUE}[*] 激活虚拟环境...${NC}"
        source "${VENV_DIR}/bin/activate"
    else
        echo -e "${YELLOW}[!] 未找到虚拟环境，使用系统 Python${NC}"
    fi
}

# 启动服务
start_server() {
    echo -e "${GREEN}[*] 启动 Business Gemini 服务...${NC}"
    
    # 检查是否已在运行
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo -e "${YELLOW}[!] 服务已在运行 (PID: $PID)${NC}"
            return 1
        else
            echo -e "${YELLOW}[!] 发现旧的 PID 文件，清理中...${NC}"
            rm -f "$PID_FILE"
        fi
    fi
    
    # 激活虚拟环境
    activate_venv
    
    # 读取配置
    CONFIG=$(read_config)
    BIND_HOST=$(echo "$CONFIG" | cut -d'|' -f1)
    BIND_PORT=$(echo "$CONFIG" | cut -d'|' -f2)
    WORKERS=$(echo "$CONFIG" | cut -d'|' -f3)
    LOG_LEVEL=$(echo "$CONFIG" | cut -d'|' -f4)
    
    # 环境变量优先
    BIND_HOST=${SERVER_HOST:-$BIND_HOST}
    BIND_PORT=${SERVER_PORT:-$BIND_PORT}
    WORKERS=${SERVER_WORKERS:-$WORKERS}
    LOG_LEVEL=${SERVER_LOG_LEVEL:-$LOG_LEVEL}
    
    # 检查Redis配置，决定worker数量
    REDIS_ENABLED=$(python3 -c "
import json
try:
    with open('$CONFIG_FILE', 'r') as f:
        cfg = json.load(f)
    redis_cfg = cfg.get('redis', {})
    print('true' if redis_cfg.get('enabled', False) else 'false')
except:
    print('false')
" 2>/dev/null || echo 'false')
    
    if [ "$REDIS_ENABLED" = "true" ]; then
        echo -e "${GREEN}[*] Redis已启用，使用多worker模式 (workers: $WORKERS)${NC}"
    else
        echo -e "${YELLOW}[!] Redis未启用，强制使用单worker模式以避免502错误${NC}"
        WORKERS=1
    fi
    
    echo -e "${GREEN}[*] 绑定地址: ${BIND_HOST}:${BIND_PORT}${NC}"
    echo -e "${GREEN}[*] Worker 数量: ${WORKERS}${NC}"
    echo -e "${GREEN}[*] 日志级别: ${LOG_LEVEL}${NC}"
    
    # 检查依赖
    python3 -c "import uvicorn, fastapi" 2>/dev/null || {
        echo -e "${RED}[!] 缺少必要依赖，请运行: pip install -r requirements.txt${NC}"
        return 1
    }
    
    # 进入项目目录
    cd "$PROJECT_DIR"
    
    # 优先使用 gunicorn
    if command -v gunicorn &> /dev/null; then
        echo -e "${BLUE}[*] 使用 Gunicorn 启动...${NC}"
        gunicorn "wsgi:application" \
            --bind "${BIND_HOST}:${BIND_PORT}" \
            --workers "$WORKERS" \
            --worker-class uvicorn.workers.UvicornWorker \
            --timeout 300 \
            --keep-alive 300 \
            --log-level "${LOG_LEVEL,,}" \
            --access-logfile "$ACCESS_LOG" \
            --error-logfile "$ERROR_LOG" \
            --pid "$PID_FILE" \
            --daemon
    else
        # 备选：使用 uvicorn
        echo -e "${YELLOW}[*] Gunicorn 未安装，使用 Uvicorn 启动...${NC}"
        nohup python3 -m uvicorn server:app \
            --host "$BIND_HOST" \
            --port "$BIND_PORT" \
            --log-level "${LOG_LEVEL,,}" \
            --timeout-keep-alive 300 \
            --no-access-log \
            > "$ACCESS_LOG" 2> "$ERROR_LOG" &
        echo $! > "$PID_FILE"
    fi
    
    # 等待启动
    sleep 2
    
    # 验证启动成功
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo -e "${GREEN}[✓] 服务启动成功 (PID: $PID)${NC}"
            echo -e "${GREEN}[✓] 访问地址: http://${BIND_HOST}:${BIND_PORT}${NC}"
            return 0
        fi
    fi
    
    echo -e "${RED}[!] 服务启动失败，请查看日志:${NC}"
    echo -e "${RED}    ${ERROR_LOG}${NC}"
    tail -20 "$ERROR_LOG" 2>/dev/null || echo "无错误日志"
    return 1
}

# 停止服务
stop_server() {
    echo -e "${YELLOW}[*] 停止 Business Gemini 服务...${NC}"
    
    if [ ! -f "$PID_FILE" ]; then
        echo -e "${RED}[!] 服务未运行（无 PID 文件）${NC}"
        return 1
    fi
    
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo -e "${BLUE}[*] 正在停止进程 (PID: $PID)...${NC}"
        kill "$PID" 2>/dev/null || true
        
        # 等待进程结束
        for i in {1..10}; do
            if ! ps -p "$PID" > /dev/null 2>&1; then
                break
            fi
            sleep 1
        done
        
        # 强制停止
        if ps -p "$PID" > /dev/null 2>&1; then
            echo -e "${YELLOW}[*] 强制停止进程...${NC}"
            kill -9 "$PID" 2>/dev/null || true
        fi
        
        rm -f "$PID_FILE"
        echo -e "${GREEN}[✓] 服务已停止${NC}"
    else
        echo -e "${YELLOW}[!] 进程不存在 (PID: $PID)${NC}"
        rm -f "$PID_FILE"
    fi
}

# 重启服务
restart_server() {
    echo -e "${BLUE}[*] 重启服务...${NC}"
    stop_server
    sleep 2
    start_server
}

# 查看状态
show_status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            CONFIG=$(read_config)
            BIND_HOST=$(echo "$CONFIG" | cut -d'|' -f1)
            BIND_PORT=$(echo "$CONFIG" | cut -d'|' -f2)
            
            echo -e "${GREEN}[✓] 服务运行中${NC}"
            echo -e "    PID: $PID"
            echo -e "    地址: http://${BIND_HOST}:${BIND_PORT}"
            echo -e "    运行时间: $(ps -o etime= -p "$PID" | tr -d ' ')"
            return 0
        else
            echo -e "${RED}[!] 服务未运行（PID 文件存在但进程不存在）${NC}"
            return 1
        fi
    else
        echo -e "${RED}[!] 服务未运行${NC}"
        return 1
    fi
}

# 重载配置
reload_config() {
    echo -e "${BLUE}[*] 重载配置...${NC}"
    
    if [ ! -f "$PID_FILE" ]; then
        echo -e "${RED}[!] 服务未运行，无法重载配置${NC}"
        return 1
    fi
    
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        # 发送 HUP 信号重载配置（Gunicorn 支持）
        kill -HUP "$PID" 2>/dev/null && \
            echo -e "${GREEN}[✓] 配置重载成功${NC}" || \
            echo -e "${YELLOW}[!] 重载失败，请尝试重启服务${NC}"
    else
        echo -e "${RED}[!] 服务未运行${NC}"
        return 1
    fi
}

# 查看日志
show_logs() {
    LOG_TYPE=${1:-error}
    
    case "$LOG_TYPE" in
        access)
            LOG_FILE="$ACCESS_LOG"
            ;;
        error)
            LOG_FILE="$ERROR_LOG"
            ;;
        *)
            LOG_FILE="$ERROR_LOG"
            ;;
    esac
    
    if [ -f "$LOG_FILE" ]; then
        echo -e "${BLUE}[*] 显示日志: $LOG_FILE${NC}"
        echo -e "${BLUE}[*] 按 Ctrl+C 退出${NC}"
        tail -f "$LOG_FILE"
    else
        echo -e "${RED}[!] 日志文件不存在: $LOG_FILE${NC}"
        echo -e "${YELLOW}[*] 可用的日志文件：${NC}"
        ls -lh "$LOG_DIR"/*.log 2>/dev/null || echo "无日志文件"
        return 1
    fi
}

# 主逻辑
case "$1" in
    start)
        start_server
        ;;
    stop)
        stop_server
        ;;
    restart)
        restart_server
        ;;
    status)
        show_status
        ;;
    reload)
        reload_config
        ;;
    logs)
        show_logs "$2"
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status|reload|logs [access|error]}"
        echo ""
        echo "命令说明:"
        echo "  start   - 启动服务"
        echo "  stop    - 停止服务"
        echo "  restart - 重启服务"
        echo "  status  - 查看服务状态"
        echo "  reload  - 重载配置（不重启服务）"
        echo "  logs    - 查看日志 (默认 error，可选 access/error)"
        echo ""
        echo "环境变量:"
        echo "  SERVER_HOST      - 绑定地址 (默认: 0.0.0.0)"
        echo "  SERVER_PORT      - 绑定端口 (默认: 8000)"
        echo "  SERVER_WORKERS   - Worker 数量 (默认: 4)"
        echo "  SERVER_LOG_LEVEL - 日志级别 (默认: INFO)"
        exit 1
        ;;
esac

exit $?
