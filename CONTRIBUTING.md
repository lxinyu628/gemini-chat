# 贡献指南

感谢您对 Gemini Chat 项目的贡献兴趣！本文档将指导您如何参与项目开发。

## 目录

- [行为准则](#行为准则)
- [如何贡献](#如何贡献)
- [开发环境设置](#开发环境设置)
- [代码规范](#代码规范)
- [提交规范](#提交规范)
- [Pull Request 流程](#pull-request-流程)
- [报告问题](#报告问题)

## 行为准则

参与本项目的贡献者需要遵守以下准则：

- 尊重所有参与者
- 使用友好、包容的语言
- 接受建设性批评
- 关注对社区最有利的事情

## 如何贡献

### 报告 Bug

1. 在 [Issues](https://github.com/ccpopy/gemini-chat/issues) 中搜索是否已存在相同问题
2. 如果不存在，创建新 Issue，包含：
   - 清晰的标题和描述
   - 复现步骤
   - 期望行为 vs 实际行为
   - 环境信息（操作系统、Python 版本等）

### 提出新功能

1. 在 Issues 中创建功能请求
2. 描述功能的用途和预期行为
3. 讨论可能的实现方案

### 提交代码

1. Fork 本仓库
2. 创建功能分支
3. 编写代码和测试
4. 提交 Pull Request

## 开发环境设置

### 1. 克隆仓库

```bash
git clone https://github.com/ccpopy/gemini-chat.git
cd gemini-chat
```

### 2. 创建虚拟环境

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. 安装依赖

```bash
# 安装项目依赖
pip install -r requirements.txt

# 安装开发依赖
pip install -e ".[dev]"

# 安装 Playwright 浏览器
playwright install chromium
```

### 4. 配置开发环境

```bash
# 复制配置模板
cp config.example.json config.json

# 编辑配置（可选）
```

### 5. 运行测试

```bash
# 运行所有测试
pytest

# 运行测试并显示覆盖率
pytest --cov=biz_gemini --cov-report=html

# 运行特定测试文件
pytest tests/test_auth.py

# 运行特定测试
pytest tests/test_auth.py::TestCreateJwt::test_jwt_structure
```

## 代码规范

### Python 代码风格

本项目遵循以下代码规范：

- **PEP 8**: Python 代码风格指南
- **行长度**: 最大 120 字符
- **缩进**: 4 空格
- **引号**: 优先使用双引号

### 格式化工具

```bash
# 使用 Black 格式化代码
black biz_gemini tests

# 使用 Ruff 检查代码
ruff check biz_gemini tests

# 自动修复可修复的问题
ruff check --fix biz_gemini tests
```

### 类型注解

- 所有公共函数必须有类型注解
- 使用 `typing` 模块中的类型
- 复杂类型使用 `TypedDict` 或 `dataclass`

```python
from typing import Optional, List, Dict

def process_data(items: List[str], config: Optional[Dict] = None) -> bool:
    """处理数据的函数。"""
    ...
```

### 文档字符串

使用 Google 风格的文档字符串：

```python
def example_function(param1: str, param2: int = 0) -> bool:
    """函数的简短描述。

    更详细的描述（如果需要）。

    Args:
        param1: 参数 1 的描述。
        param2: 参数 2 的描述，默认为 0。

    Returns:
        返回值的描述。

    Raises:
        ValueError: 当参数无效时抛出。

    Example:
        >>> example_function("test", 42)
        True
    """
    ...
```

### 注释语言

- 代码注释统一使用**中文**
- 保留英文专业术语（如 JWT、Cookie、API 等）
- 文档字符串使用中文

## 提交规范

### Commit Message 格式

采用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/) 规范：

```
<类型>(<范围>): <描述>

[可选的正文]

[可选的脚注]
```

### 类型

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档更新 |
| `style` | 代码格式（不影响功能） |
| `refactor` | 重构（不是新功能也不是修复） |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建过程或辅助工具变动 |

### 示例

```bash
# 新功能
feat(auth): 添加 JWT 自动刷新功能

# Bug 修复
fix(client): 修复会话创建时的竞态条件

# 文档
docs: 更新 API 使用说明

# 重构
refactor(config): 简化配置加载逻辑
```

## Pull Request 流程

### 1. 创建分支

```bash
# 从 main 创建功能分支
git checkout main
git pull origin main
git checkout -b feature/your-feature-name
```

### 2. 开发和测试

```bash
# 编写代码
# ...

# 运行测试
pytest

# 运行代码检查
ruff check biz_gemini
black --check biz_gemini
```

### 3. 提交更改

```bash
git add .
git commit -m "feat(scope): your commit message"
```

### 4. 推送并创建 PR

```bash
git push origin feature/your-feature-name
```

然后在 GitHub 上创建 Pull Request。

### PR 要求

- [ ] 代码通过所有测试
- [ ] 代码通过 lint 检查
- [ ] 新功能包含测试
- [ ] 更新相关文档
- [ ] PR 描述清晰说明变更内容

### PR 模板

```markdown
## 变更说明

简要描述此 PR 的变更内容。

## 变更类型

- [ ] 新功能
- [ ] Bug 修复
- [ ] 文档更新
- [ ] 重构
- [ ] 其他

## 测试

描述如何测试这些变更。

## 检查清单

- [ ] 代码遵循项目规范
- [ ] 已添加必要的测试
- [ ] 文档已更新
- [ ] 所有测试通过
```

## 报告问题

### Issue 模板

**Bug 报告**

```markdown
## 问题描述

清晰描述遇到的问题。

## 复现步骤

1. 执行 '...'
2. 点击 '...'
3. 查看错误

## 期望行为

描述你期望发生什么。

## 实际行为

描述实际发生了什么。

## 环境信息

- 操作系统: [例如 Windows 10, Ubuntu 22.04]
- Python 版本: [例如 3.11.0]
- 项目版本: [例如 1.0.0]

## 日志/截图

如果适用，添加日志或截图。
```

**功能请求**

```markdown
## 功能描述

清晰描述你希望添加的功能。

## 使用场景

描述这个功能的使用场景。

## 建议实现

如果有想法，描述可能的实现方式。

## 其他

任何其他相关信息。
```

## 联系方式

- GitHub Issues: https://github.com/ccpopy/gemini-chat/issues
- 项目主页: https://github.com/ccpopy/gemini-chat

感谢您的贡献！
