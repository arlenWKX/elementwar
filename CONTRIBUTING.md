# 贡献指南

感谢参与本项目！请阅读以下指南以保证代码质量。

## 开发环境搭建

```bash
# 克隆仓库
git clone <repo-url>
cd elementwar-backend

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# 或 .venv\Scripts\activate  # Windows

# 安装依赖
make dev  # 等同于 pip install -r requirements.txt + 测试工具
```

## 开发工作流

1. **创建分支**：`git checkout -b feature/your-feature` 或 `fix/your-bugfix`
2. **编写代码**：遵循现有代码风格
3. **运行测试**：`make test-fast`（快速反馈）+ `make test-chemkit`（化学引擎回归）
4. **代码检查**：`make lint && make format`
5. **提交**：使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范
   - `feat: 新增 XXX 功能`
   - `fix: 修复 XXX 问题`
   - `docs: 更新文档`
   - `refactor: 重构 XXX`
   - `test: 补充测试`
6. **推送并提 PR**

## 代码风格

- Python 3.10+
- 类型注解必须
- 函数 docstring 推荐 Google 风格
- 单行不超过 110 字符
- 用 `ruff format` 自动格式化

## 测试要求

- 新功能必须配套单元测试
- 修改 chemkit 数据（beta.json / pka.json 等）后必须跑 `make test-chemkit`，确保 541 个用例全过
- Bug 修复需附回归测试

## 项目结构

参见 [docs/DESIGN.md](docs/DESIGN.md) 的"目录结构"章节。

## 安全规范

- **永远不要在代码、提交信息、日志中包含密钥、密码、私钥**
- `.env` 已在 `.gitignore`，仅 `.env.example` 可提交
- 若不小心提交了密钥，立即：
  1. 撤销该密钥
  2. 用 `git filter-branch` 或 BFG 清理历史
  3. 通知协作者

## 报告 Bug

请使用 GitHub Issues，模板：

```
**复现步骤**：
1. ...
2. ...

**期望行为**：

**实际行为**：

**环境**：
- Python 版本：
- 操作系统：
- chemkit 版本：
