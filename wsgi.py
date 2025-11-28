"""WSGI 入口文件 - 用于 gunicorn 等生产服务器"""

import os
import sys

# 确保项目目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入 FastAPI 应用
from server import app

# 暴露给 WSGI 服务器
application = app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
