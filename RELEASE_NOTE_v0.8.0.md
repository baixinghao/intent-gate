## Highlights

- **DeepSeek Harness 全量接入**：`intent-gate install --target dsh` 一键接线——cordis.patch.yml 合并不覆盖、幂等可重复，uninstall 只拆自己的线
- **Skills 分发**：5 个 skills 装入 `$DSH_HOME/skills/`；因 dsh-mcp-client 只桥接 MCP 工具不桥接 prompt，playbook 以 `doc-analysis-playbook` skill 形态一并安装
- **修复 wheel 缺资源**：force-include 改整目录映射，pipx 安装态 `install --target dsh` 不再崩
- **修复 Windows GBK 崩溃**：CLI 强制 UTF-8 输出，emoji 注入文本不再 UnicodeEncodeError
- **测试**：155 个单测全绿（新增 15 个 dsh 安装器测试 + GBK 回归测试）

## Install

```bash
pipx install intent-gate-mcp
intent-gate install --target dsh   # 或 cursor / codex
```

**Full Changelog**: https://github.com/baixinghao/intent-gate/compare/v0.7.0...v0.8.0
