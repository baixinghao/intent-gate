"""Central configuration. Environment variables use the HG_ prefix; see .env.example.

intent-gate 是**轻量插件**：只跑 single 通道（对话框兜底），零凭据、零外部服务。
钉钉群通道与阻塞式决策闸门已剥离为姊妹篇 intent-gate-service（独立 MCP 服务，
见 ../intent-gate-service/）——所以本配置里没有任何钉钉字段。
"""

from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HG_", env_file=".env", extra="ignore")

    # ---- 意图对齐通道（DESIGN.md §2）----
    # single: 题目只回给宿主 agent，对话框兜底，钉钉全程不参与（唯一支持值）
    # group:  已剥离——钉钉群通道整体迁往姊妹篇 intent-gate-service（独立 MCP 服务）
    channel: str = "single"
    # 项目根目录（.harness 所在）。MCP stdio 通常被 agent 在项目根拉起，默认 "."
    workspace_root: str = "."
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _check_channel(self) -> "Settings":
        if self.channel != "single":
            # 旧 .env 里的 HG_CHANNEL=group 不能静默降级成 single——
            # 静默降级 = 题永远到不了群，用户还以为发出去了。fail-fast 给迁移指引。
            raise ValueError(
                "HG_CHANNEL=group 已不支持：钉钉群通道剥离为姊妹篇 intent-gate-service"
                "（独立 MCP 服务，见 ../intent-gate-service/README.md）。"
                "要么移除 HG_CHANNEL 走 single 通道（对话框兜底，零配置），"
                "要么安装 intent-gate-service 并在 MCP 客户端同时挂载两个服务。"
            )
        return self
