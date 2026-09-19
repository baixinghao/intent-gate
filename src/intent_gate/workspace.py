"""工作区解析：本次调用的项目根（.harness 所在）从哪来。

设计——为什么项目根是**工具级参数**，而不是启动时绑定：

  MCP server 在宿主里是全局单例，一个进程服务该宿主的所有工作区。
  启动时绑定（cwd / HG_WORKSPACE_ROOT）只能表达"一个"项目根，换项目就得改
  配置并重启；而"设一次全局生效"的开关更糟——宿主会**并发**跑 subagent
  （Phase B 生成 subagent、workflow 扇出），全局可变状态会让并发 agent 互相
  污染项目根，且这种串项目极难复现。

  所以：project_path（工具级，优先）→ 缺省回落 workspace_root（启动时）。
  无状态，并发安全，换项目不用重启。

路径基准（🔴 纪律）：相对路径一律按**生效的 workspace_root** 解析，永不按
进程 cwd。账本落盘、PRD 读取、summary/lint 对象必须同一个基准——cwd 是宿主
进程的启动目录，与工作区无关，一旦两者不等，相对路径会静默跑到别的项目去。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["resolve_root", "resolve_under_root"]


def resolve_root(project_path: str | Path | None, default_root: str | Path) -> Path:
    """解析本次调用生效的项目根。

    project_path 为工具级显式指定，优先；None 或空白串回落 default_root
    （进程启动时的 workspace_root / HG_WORKSPACE_ROOT）。始终返回绝对路径，
    使同一项目的不同写法（相对、绝对、带 ~）解析为同一结果。
    """
    if project_path is None or str(project_path).strip() == "":
        raw: str | Path = default_root
    else:
        raw = project_path
    return Path(raw).expanduser().resolve()


def resolve_under_root(workspace_root: str | Path, path: str | Path) -> Path:
    """把入参路径按 workspace_root 解析：绝对路径原样返回，相对路径拼到根下。

    🔴 不碰 Path.cwd()——cwd 是宿主进程的启动目录，与工作区无关。
    """
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    return Path(workspace_root).expanduser().resolve() / p
