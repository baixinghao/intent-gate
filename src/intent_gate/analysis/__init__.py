"""需求解析引擎（doc-analysis 下沉，MCP 版）。

两条路（🔴 本引擎不含任何红蓝对抗/评审内容，那是应用层的事）：

  fresh  从0开始：读 PRD → 型态判定 → 复杂度 → 置信度灯 → 歧义点初筛
         → 落盘 _review/analysis-draft.md → 返回结构化题目（供 dispatch_question）
  resume 中断续跑：不碰 PRD，直接读 _review/ 下的文件现场
         （待决清单/对齐流水/推断清单/analysis-draft）→ 汇报进度与下一步动作

🔴 诚实声明：本引擎是【机械初筛】。型态判定靠关键词信号计数，
歧义点靠规则正则——它负责把"可能要问的题"结构化地摊出来并守住纪律
（先落盘、红黄灯门禁、选项格式），但语义终判永远归宿主 agent。
引擎漏判的歧义由宿主补上；引擎误判的，宿主可忽略不调 dispatch。
"""

from .engine import analyze_request  # noqa: F401
