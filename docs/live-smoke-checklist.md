# 解锁后 5 分钟最小 live 冒烟

**前置：** Michael 口头确认解锁 spend。在此之前保持 `live_spend: locked`，不要设 `OHBE_ALLOW_LIVE_SPEND=1`，不要把 key 写进 YAML。

**范围：** 单任务 · `vllm/qwen3`（或目录已准入的等价 id）· `openbot` vs `inspect_react` · 不扩 `HARNESS_ORDER` live 白名单。

## 0. 解锁前自检（约 30s）

```bash
curl -sS http://127.0.0.1:8765/health
# 期望仍是 "live_spend":"locked" 直到口头确认后才改环境
```

确认目录里已有目标模型（合入 #43 的 catalog 条目）。

### 0.1 Plans 白名单（必做，与 spend 无关）

`POST /v1/rounds/plans` 只接受 **version-gated** 的 `GET /v1/catalog/harnesses` 白名单行。Workbench API 镜像**不**预装 OpenBot / Inspect；宿主机上的 `openbot` 二进制对容器不可见，且宿主 `--version` 也不是允许的 `fixture` / 40 位 SHA。因此默认 `presence: absent`、`plans_whitelist: []`，新建 session 方案会 **403 `harness_not_whitelisted`**。

夹具方案 `fixture-paired` 不受此门（种子数据）。`/harnesses` 页是竞品预装库，**不会**写入 plans 白名单。

任选其一（都不解锁 spend，都不扩 `HARNESS_ORDER`）：

**A. 进程内登记（重启 API 后需重做）**

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/catalog/harnesses \
  -H 'Content-Type: application/json' \
  -d '{"selections":[
    {"harness_id":"openbot","version":"fixture"},
    {"harness_id":"inspect-react","version":"0.3.277"}
  ]}'
# 期望 plans_whitelist 含 openbot、inspect-react；spend 仍为 none
```

**B. Desktop/私有 compose 环境钉（持久，推荐）**

`docker/workbench/compose.desktop.yaml`（及 Linux `compose.yaml`）为 API 设置：

- `OHBE_HARNESS_OPENBOT_VERSION=fixture`
- `OHBE_HARNESS_INSPECT_REACT_VERSION=0.3.277`

重建/重启 API 后 `presence` 应为 `preinstalled`，无需每次 POST。宿主真装 Inspect / 钉 SHA 时可用同名环境变量覆盖。

自检：

```bash
curl -sS http://127.0.0.1:8765/v1/catalog/harnesses \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["plans_whitelist"]); print([(h["harness_id"],h["presence"],h["whitelist_version"]) for h in d["harnesses"]])'
```

## 1. 口头确认后解锁（运维侧）

仅在进程环境设置（示例，勿提交）：

```bash
export OHBE_ALLOW_LIVE_SPEND=1
# 按私有部署清单提供 gateway / key；key 只进环境或请求，不进 YAML
```

重启 API 后再查 health：`live_spend` 应变为允许态（具体字段以当时 health 契约为准）。`spend_execution` 与双锁语义仍以 `docs/private-deploy-checklist.md` / `docs/live-pilot.md` 为准。

## 2. 准备页（模型池 + Harness）

1. `/pools`：确认 `vllm/qwen3`（或约定 id）已准入，对比槽只选这一条（或最小必要集合）。
2. 用 §0.1 确认 `openbot`、`inspect-react` 已在 **plans** 白名单（不要只看 `/harnesses` 预装页）；不要临时扩 live 白名单。

夹具目录 `/experiments` 只读，不在这里下发。

## 3. 评测页起草并确认（不执行）

1. `/rounds` → 添加方案，`paired_harness`，受试 `openbot`，参照由白名单配对为 `inspect_react`（API id：`inspect-react`）。
2. 槽位写入上述模型；生成套件后**只留 1 个任务**（或确认后只监测焦点任务）。
3. 确认套件 → 「去监测」。本页不应出现下发 / Tick。

CLI 等价（解锁前可建方案，`live:false`）：

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/rounds/plans \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"P0 paired smoke draft",
    "seed":7,"rounds":1,"mode":"paired_harness",
    "harness":{"harness_id":"openbot","name":"OpenBot","version":"fixture","parameters":{"profile":"litellm-only"}},
    "reference_harness":{"harness_id":"inspect-react","name":"Inspect react()","version":"0.3.277","parameters":{"solver":"react"}},
    "pool_id":"pool-pilot","model_ids":["vllm/qwen3"],
    "difficulty_mix":[{"difficulty":"easy","weight":1}],
    "capabilities":["echo"],"timeout_seconds":30,"live":false
  }'
# 期望 lifecycle=draft_plan、pool_kind=admitted、comparison_model_ids=["vllm/qwen3"]、spend=none
```

## 4. 监测页 Dry-run  sanity（可选，30s）

解锁前或后都可先 Dry-run：`live: false` 下发 → Tick → 自动切换间隔可见 → 失败窗格可「跳过并继续」。确认横幅仍为 `spend:none` / `executed:false` 当 live 未真正打开时。

## 5. 最小 live（约 3–4 分钟）

1. 监测页加载已确认方案。
2. 仅当 health 已解锁且 Michael 已确认后，按当时运维约定发起**单任务** live（具体开关以 API / 工作台当时契约为准；本清单不发明第二套解锁路径）。
3. 观察两个窗格对话：成功桩 / 失败跳过 / 占位；**缺失延迟或成本不得写成 0**。
4. 封存 → 报告页打开；报告标签与 spend 字段诚实。

## 6. 收尾

- 跑完关掉 `OHBE_ALLOW_LIVE_SPEND`，重启，确认 health 回到 `locked`。
- 不把密钥、trace 里的 key、或 live 报告里的密钥材料提交进 git。

相关：`docs/live-pilot.md`、`docs/private-deploy-checklist.md`、`docs/monitor.md`、`docs/model-intake.md`（`/v1/catalog/harnesses`）。
