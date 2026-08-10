# intent-gate Architecture

> 设计原则：一个零依赖的确定性核心；所有外部系统（MCP、钉钉、HTTP）
> 都是边缘适配器；行为由配置决定，而不是代码分支。
>
> v0.2.0 起钉钉传输层与阻塞式决策闸门剥离为姊妹篇 **intent-gate-service**
> （独立 MCP 服务，见 [../../intent-gate-service/](../../intent-gate-service/)），
> intent-gate 只剩零依赖核心 + single 通道。

## Layers

```
agent (Claude Code / Pi agent)
  │ MCP tools: dispatch_question / collect_answers / resolve_question /
  │            record_inference / confirm_inferences / rebroadcast_pending /
  │            list_pending_questions / abandon_* + analyze/lint/mapping
  ▼
intent-gate（本仓，轻量插件）
  ├── alignment/   意图对齐业务层 + 契约函数（register_question / file_inbound_reply）
  ├── analysis/    需求解析引擎（playbook / engine / lint / mapper）
  ├── store.py     文件落盘层（.harness/requests/{需求名}/_review/）── 唯一事实源
  └── models.py / security.py   纯 stdlib 核心（token / 白名单 / 回复解析 / 限流）
                    ▲
                    │ 依赖（契约函数与纯 stdlib 核心复用，绝不另起实现）
                    │
intent-gate-service（姊妹篇，独立 MCP 服务，可选）
  ├── server.py    ask_human / list_pending / cancel_gate / group_dispatch / group_rebroadcast
  ├── gate.py      GateManager：关联、Future、超时、事件总线
  ├── bridge.py    群通道桥（落盘调 intent_gate 契约函数，发群调 dingtalk client）
  └── dingtalk/    出站 client（api 优先 / webhook 存量）+ 入站 http|stream + crypto
```

## The long-connection decision

Requirement: *SSE where feasible, no WebSocket-style long connection*.

| Channel | Feasible transports | Chosen |
|---|---|---|
| DingTalk → us (inbound) | HTTPS callback **or** proprietary stream SDK. **DingTalk offers no SSE.** | `http` mode: pure request/response, zero long connection. `stream` only when no public URL exists. (均在 intent-gate-service 侧) |
| us → agent-facing event feed | SSE is ideal (one-directional server push) | SSE at `GET /events`（intent-gate-service 侧） |
| us → MCP clients | stdio / SSE / streamable-HTTP | stdio default; `--mcp-transport sse`（两侧相同） |

## Correlation & security（intent-gate-service 侧）

1. `ask_human` opens a gate with token `HG-XXXX`, posts markdown to the group.
2. A human replies `@robot [HG-XXXX] <answer>`.
3. Inbound adapter verifies DingTalk signature + decrypts, then `GateManager`:
   - sender staffId must be in `HG_ALLOWED_SENDERS` (**fail-closed** when empty);
   - token must match a pending gate (implicit match allowed only when exactly
     one gate is pending and `HG_ALLOW_IMPLICIT_SINGLE_MATCH=true`);
   - rate-limited per sender.
4. The blocked MCP tool call returns `HUMAN_REPLY[who]: answer`.
5. Timeout returns a `NO_REPLY` fallback string instructing conservative action.

意图对齐答案走另一条非阻塞路：群回复落盘 `_review/inbox/`（复用
intent_gate 的 `file_inbound_reply` 契约函数），宿主经 `collect_answers`
领取、`resolve_question` 核销——两个服务通过文件契约衔接，互不持有对方状态。

## Failure posture

- Outbound send: 3 attempts, exponential backoff; failure raises into the tool
  result so the agent sees the escalation failed rather than hanging silently.
- Gate timeout: bounded by `HG_MAX_TIMEOUT_SEC`; agent always gets a string back.
- Slow SSE consumers: bounded queues, events dropped, never blocks the core.
- Callback ACK: DingTalk demands an encrypted `success` within 3s; signature or
  decrypt failures still ACK to avoid redelivery storms.
- 先落盘后发送：dispatch 题未落盘前不允许任何发送动作；发送失败题已在清单，
  rebroadcast 补发即可，进程猝死不丢 token。
