# 私有部署联调清单

拓扑已经定下来：

```text
私有服务器上的 OpenBot  ->  同一台机器上的 OHBE  ->  租用 GPU 服务器上的 LiteLLM  ->  本地 vLLM / Ollama
```

OpenBot 不搬到 GPU 机。GPU 机只跑开源模型和 LiteLLM，不接云厂商 API。
OHBE 装在私有 OpenBot 主机上，基准流量只去 GPU 机的代理。

这份清单是安装和第一次联调要核对的步骤。它不包含密钥，也不发起付费调用。
`.github/workflows/ci.yml` 没有 `workflow_dispatch`，也不会跑 `benchmark run`。
`.github/workflows/live-pilot.yml` 仍然只在有人从 Actions 页手动触发时运行，
并且在密钥为空时先退出。密钥以后再配，不要为了联调去改这两处触发方式。

协议细节见 `docs/live-pilot.md`。冒烟清单是
`experiments/live-pilot-smoke.yaml`。

## 1. 私有 OpenBot 主机上安装 OHBE

在私有服务器上克隆本仓库。不要从 CI 克隆私有的 OpenBot 仓库。OpenBot
二进制已经在这台机器上，OHBE 只通过 `OPENBOT_BIN` 调用它。

```bash
git clone https://github.com/upbeyond/OpenBot-Harness-Benchmark-Engine.git
cd OpenBot-Harness-Benchmark-Engine
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev,inspect]"
.venv/bin/pytest
.venv/bin/benchmark --help
```

`inspect` extra 钉在 `inspect-ai==0.3.277`。没装这个版本时，`benchmark run`
在启动 harness 之前就会拒绝。默认 CI 不安装这个 extra，也不需要它。

记下这次安装用的 OHBE git commit（`git rev-parse HEAD`）和
`python -c "import ohbe; print(ohbe.__version__)"`。

## 2. GPU 机上的 LiteLLM

GPU 机上的代理镜像是 `ghcr.io/berriai/litellm:v1.104.2`。上游只指向本机的
vLLM 和 Ollama，例如 vLLM `http://127.0.0.1:8000/v1`、Ollama
`http://127.0.0.1:11434`。样例配置是
`docker/litellm/config.gpu-farm.sample.yaml`。里面没有云厂商 API key。

`model_name` 必须和冒烟清单里的 model id 一致：

| OHBE `model_id` | LiteLLM `model_name` | 上游 |
| --- | --- | --- |
| `vllm/REPLACE_ME-instruct` | `vllm/REPLACE_ME-instruct` | `hosted_vllm/REPLACE_ME-instruct` |
| `ollama/REPLACE_ME` | `ollama/REPLACE_ME` | `ollama/REPLACE_ME` |

占位 id 写在 `models/pools/pool-gpu-farm.example.yaml`。冒烟实验用
`model_pool_file` 引用这份模板，不要在实验文件里再抄一套 id。换成 GPU 机
实际提供的 id 时，三处一起改：模型池 YAML、LiteLLM `model_name`、离线价格表
里的同一行。不要改成 `openai/` 或其他云厂商前缀。

`docker/litellm/compose.yaml` 和 `docker/litellm/config.sample.yaml` 是仓库里的
本地夹具代理，不是这台租用 GPU 机，也不属于这次联调。

只让 LiteLLM 进程访问本机 vLLM / Ollama 端口。基准进程不要调用
`litellm.completion`。

## 3. 把 `LITELLM_BASE_URL` 指到 GPU 机

在私有 OpenBot 主机上：

```bash
export LITELLM_BASE_URL=http://gpu-litellm:4000/v1
```

把 `gpu-litellm` 换成这台 GPU 机上 LiteLLM 实际的主机名或 IP，端口换成实际
监听端口。要求：

- 协议是 `http`。不要把密钥、query 或 fragment 写进 URL。
- 路径是空或者 `/v1`。
- 主机和端口必须与 `experiments/live-pilot-smoke.yaml` 的 `egress` 块一致。
  冒烟文件写的是 `gpu-litellm` 和 `4000`。改了真实地址，就要同时改这份清单。
- 主机不能是 `api.openai.com`、`api.anthropic.com`、`huggingface.co`，也不能是
  策略里其他被拒绝的厂商主机。

OpenBot 画像里的 `baseUrl` 是这个 URL 的 origin（冒烟文件对应
`http://gpu-litellm:4000`），不是带 `/v1` 的地址。凭证只写成
`${LITELLM_API_KEY}`，不写明文。

`LITELLM_API_KEY` 是 GPU 机 LiteLLM 的 virtual key。联调时在 shell 里导出。
不要写进实验 YAML、trace 或报告。

## 4. 出站白名单

策略版本是 `ohbe-egress-0.1`。Harness 进程只允许访问上面那个网关主机和端口。
冒烟文件里的白名单是 `gpu-litellm:4000`，网络名是 `ohbe_egress`。

直接连 OpenAI、Anthropic、HuggingFace 会在发包前失败。`docker.network: none`
盖不住调用模型的宿主进程，也会挡住 GPU 网关，不要用它当出站控制。

私有服务器必须能访问 GPU 机的 LiteLLM 端口。GPU 机上的 vLLM 和 Ollama 端口
不要对基准进程开放。

发布闸门仍然是：

```bash
.venv/bin/benchmark check-egress
```

没有 Docker daemon 时这个命令退出码是 2。它打的是仓库里的网关 mock，不是
GPU 机，也不是 live spend。

## 5. 钉死 `OPENBOT_BIN`

```bash
export OPENBOT_COMMIT=<40 位小写 git SHA>
export OPENBOT_BIN=/path/to/openbot
export OPENBOT_STATE_DIR=/tmp/ohbe-openbot-state
```

- `OPENBOT_COMMIT` 必须是正在运行的那个 OpenBot 修订的 40 位小写 SHA。
  `openbot-commit-unavailable`、分支名、`latest` 都会在启动前被拒绝。
- `OPENBOT_BIN` 必须是那个 commit 构建出来的可执行文件。本仓库不克隆
  OpenBot。
- `OPENBOT_STATE_DIR` 用隔离目录，不要用用户家目录。
- `OPENBOT_IMAGE` 可选。要么 tag 里带这 40 位 SHA，要么是
  `name@sha256:<64 位小写十六进制>`。`latest` 和
  `openbot-image-unavailable` 会被拒绝。OHBE 只记录这个引用，不会去拉镜像。

Harness 子进程会丢掉 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 和 HuggingFace
token。画像里也不要出现这些名字。

## 5.1 离线预检（`benchmark doctor`）

跑第 6 节之前先做这一步。预检只读当前 shell 的环境变量和仓库里的 YAML，
不访问网络，也不调用模型。

1. 模型池用仓库里的示例，或复制后再改 id：

```bash
cp models/pools/pool-gpu-farm.example.yaml models/pools/pool-gpu-farm.yaml
```

示例里的 `REPLACE_ME` 要换成 LiteLLM 上的真实 model id。网关地址只来自
`LITELLM_BASE_URL`，密钥只来自 `LITELLM_API_KEY`。文件里不要写 URL，也不要
写 key。`constraints.fail_closed` 为 true 时，`api.openai.com`、
`api.anthropic.com` 以及它们的子域名会被拒绝。

2. 在当前 shell 设置变量。`OPENBOT_BIN` 可以先不设。

```bash
export LITELLM_BASE_URL=http://gpu-litellm:4000/v1
export LITELLM_API_KEY=...
# export OPENBOT_BIN=/path/to/openbot
```

主机和端口必须与 `experiments/live-pilot-smoke.yaml` 的 `egress` 一致。
OpenAI、Anthropic、HuggingFace 以及其他被拒绝的厂商主机会失败。

3. 运行预检。两条命令等价：

```bash
.venv/bin/benchmark doctor \
  --pool models/pools/pool-gpu-farm.example.yaml \
  --experiment experiments/live-pilot-smoke.yaml
```

```bash
scripts/private-host-check.sh
```

`scripts/private-host-check.sh` 会调用 `python -m ohbe doctor`。可以给它传
和 `benchmark doctor` 一样的 `--pool`、`--experiment`、`--checklist`。

退出码 0：标准输出里有 LiteLLM-only 的 OpenBot profile（`baseUrl` 是网关
origin，`apiKey` 是 `${LITELLM_API_KEY}`），以及下面第 6 节的下一步命令。
退出码 1：环境、模型池或实验清单没有通过。退出码 2：文件读不到。
预检不会执行那些冒烟命令，也不会把 `LITELLM_API_KEY` 打出来。

## 5.2 打开工作台

`benchmark doctor` 退出码是 0 之后，可以在这台私有 OpenBot 主机上打开工作台。
工作台读的是仓库里的夹具报告和 `/v1`。它不执行第 6 节的冒烟，也不访问 GPU 机上的
LiteLLM。页面默认打开夹具 `fixture-pilot`。若 `results/live/<experiment_id>/report.json`
（或 `benchmark report` 写出的 `results/live/<experiment_id>/report/report.json`）已经在磁盘上，
报告列表会把它标成 live，可以打开。这只是读已有文件，不解锁 live spend，也不表示端到端已经连通。

这组容器的默认值：

- 页面只绑在 `127.0.0.1:8080`。API 只绑在 `127.0.0.1:8765`，给本机 nginx 用。
  浏览器走 8080，不要改成 `0.0.0.0`。
- 两个容器使用宿主机网络，不走 Docker 网桥。有些机器的 iptables `FORWARD`
  策略是 `DROP`，同网桥的包到不了 API。工作台不改 iptables。
- macOS / Windows 的 Docker Desktop 不能用这份宿主机网络：host network 到不了
  Mac 的 loopback。改用桥接文件 `docker/workbench/compose.desktop.yaml`
  （`make workbench-desktop-up`）。端口仍只发布在 `127.0.0.1:8080` 和
  `127.0.0.1:8765`。`LITELLM_BASE_URL` 与 `LITELLM_API_KEY` 只从当前 shell 代入，
  文件里不写密钥，也不写 `OHBE_ALLOW_LIVE_SPEND=1`。
- `OHBE_ALLOW_LIVE_SPEND` 是空字符串。只有精确的 `1` 才会打开这面旗，compose 不写 `1`。
- 不要把 `LITELLM_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 写进
  compose、镜像或 `docker/workbench/.env`。`LITELLM_*` 只从当前 shell 代入，未设置时为空。
- 进程能用宿主机的网络栈，但这个栈不跑 `benchmark run`，API 也不发起模型调用。

在仓库根目录：

```bash
make workbench-up
```

等价命令：

```bash
docker compose -f docker/workbench/compose.yaml up --build -d
```

在这台机器上打开：

```text
http://127.0.0.1:8080
```

从自己的笔记本看这台私有机时，用 SSH 转发。监听地址保持 `127.0.0.1`。

```bash
ssh -L 8080:127.0.0.1:8080 <user>@<private-host>
```

笔记本上打开同一个地址。`<user>` 和 `<private-host>` 换成这台机器的账号和主机名。

核对进程，并确认 live spend 仍是锁定：

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/v1/status
curl -fsS http://127.0.0.1:8080/v1/doctor
curl -fsS http://127.0.0.1:8080/v1/results
```

`/v1/status` 里应有 `"live_spend":"locked"` 和 `"spend_execution":"disabled"`。
`/v1/results` 的列表里仍有 `result_id` `fixture-pilot`，其 `spend` 是 `not_live`。已落盘的 live 报告另记 `source_kind: live`。

| 路径 | 看什么 |
| --- | --- |
| `/` | 总览：引擎版本、spend 锁、`/v1` 路由表。联调摘要在这一页，不和评测并列 |
| `/pools` | 准备 · 模型池。已准入列表为空时不预填。只通过添加本地或添加云端添加。扫描在添加本地里，用来发现已部署模型 |
| `/harnesses` | 准备 · Harness 库。已登记列表为空时不预填。只通过预装或扫描添加。live 顺序保持 `HARNESS_ORDER` |
| `/rounds` | 评测/方案：对比槽与套件确认。确认后「去监测」。本页不下发、不 Tick。夹具目录在准备页 `/experiments` |
| `/results` | 一轮结束之后的报告。从评测方案的轮次结束处打开。默认是夹具 `fixture-pilot`。磁盘上已有的 live 报告可以在同一页打开，不解锁花费 |
| `/doctor` | 联调：从总览进入。进程内检查（夹具报告、出口拒绝、spend 锁）。它不是 `benchmark doctor`，也不打印第 6 节的命令 |

### 用工作台表单改模型池和 Harness 画像

`/pools` 是**模型池**。主区域只列出已经准入的目录条目，没有条目时为空。添加只有两个动作：添加本地、添加云端。扫描收在添加本地里，用来发现已经部署的模型，不是第三个顶层动作。两者都先选本地或远程服务器；远程必须先连通。添加云端写入 `placement: gateway`。扫描只收下通过版本门的模型，放置方式是 `local`。模型身份仍只允许 vllm / ollama，请求只走 LiteLLM。没有单独的入库页。

`/rounds` 是**评测/方案**：起草模式、对比槽、生成并确认套件，然后「去监测」。观察壳与下发在 `/monitor`。缺失不是零。一轮封存后从监测页打开 `/results`。准备页 `/pools`、`/harnesses`、`/experiments`（夹具目录，只读）不跑评测。`/experiments` 不再重定向到评测页。

`/harnesses` 是 **Harness 库**。主区域只列出已登记安装，没有安装时为空。预装从目录里选。pip 条目会在后台解析固定版本：目录里已有的规格（例如 `inspect-ai==0.3.277`），或最新的具体发行版及其 `files.pythonhosted.org` 文件地址。画像里的 `version` 是这个具体版本，不会写入 `latest`。五个对照条目的本地点击会按各自能复现的路径安装，不会写入 `latest`。OpenClaw npm `2026.10.1` 和 DeepSeek Harness `@deepseek-ai/dsh@0.2.1-alpha.2` 走 `npm install --ignore-scripts`，包脚本不运行，版本和可执行文件对齐后才登记画像。WorkBuddy `5.7.7` 在 macOS 或 Windows 上下载对应安装包并登记为未启动，Linux 返回 `os_unsupported`，不下载。千问办公 `1.2.5` 仍没有可固定地址（下载页只有浮动的 `/release/latest/`，版本化路径 404，该地址不入库），点击返回 `installer_unpinned`。豆包工作 `2.31.10` 在 Windows 上下载 x64 或 arm64 安装包，不启动，并登记失败关闭的画像；其他系统返回 `os_unsupported`。OpenClaw 的可运行适配器是选入的嵌入式 agent，不启动 OpenClaw Gateway，也不把回合桥接到 DeepSeek Harness。WorkBuddy 的可运行适配器是桌面应用里捆绑的 CodeBuddy CLI（`codebuddy --print`），不是桌面 GUI，也不把 WorkBuddy 当作 DeepSeek Harness 的模型源；桌面安装包仍然不执行。豆包工作和千问办公有失败关闭的适配器：`run` 在启动进程或访问模型之前拒绝，因为没有只走 LiteLLM 的无头路径。本机已经装好且版本变量与固定版本一致时（`WORKBUDDY_VERSION` 与 `WORKBUDDY_BIN`、`OPENCLAW_VERSION` 与 `OPENCLAW_BIN`、`DSH_VERSION` 与 `DSH_BIN`、`QWEN_WORK_VERSION` 与 `QWEN_WORK_BIN`、`DOUBAO_WORK_VERSION` 与 `DOUBAO_WORK_BIN`），扫描或安装可以登记画像，不再去下载。千问办公和豆包工作的画像写明 `runnable: false`，二进制也不会被启动。远程需要主机，以及 SSH 密钥或密码。密钥来自请求字段或环境变量名，不写入池 YAML、目录 YAML 或 git。被拒绝的主机会在打开连接前失败。认证之后，远程软件清单和远程安装仍不执行（`inventory_unsupported`）。同一套固定版本是以后在远程安装的入口。这五个名字保持 plan。Live 顺序仍只有 `openbot` 和 `inspect_react`。保存文件不会扩大 live 顺序。

这些动作都不打开 live spend。即使进程里 `OHBE_ALLOW_LIVE_SPEND=1`，响应仍是 `spend: none`、`executed: false`，`spend_execution` 仍是 `disabled`。本地 Harness 目标不探测网络。添加本地在请求或 `OHBE_VLLM_BASE_URL` 给出 vLLM 地址时会 GET `/v1/models`，这时 `network` 为 `probed`；没有地址则不打开连接。令牌只来自请求或 `OHBE_VLLM_API_KEY`，不进池文件。这不解锁 live spend。模型池的远程目标只有 TCP 探测之后 `network` 才是 `probed`。Harness 库的远程目标只有 SSH 认证之后才是 `probed`。被拒绝的主机保持 `not_contacted`。

停掉：

```bash
make workbench-down
```

Docker Desktop 停掉用 `make workbench-desktop-down`。

### 本机开发者注意（Mac）

这两条是本机运维惯例，不改产品行为，也不解锁 live spend。

**Git `insteadOf`（SSH 被改 HTTPS）**

部分 Mac 的全局 git 配置里有：

```text
url.https://github.com/.insteadof git@github.com:
```

效果是把 `git@github.com:` 重写成 `https://github.com/`，fetch/push 走 443，在部分网络下不稳定。
**不要**为了本仓库去改用户的 `~/.gitconfig`。先只读确认：

```bash
git config --global --get-regexp 'url\..*insteadof'
```

单次取消该 rewrite（不写盘）：

```bash
git -c url.https://github.com/.insteadof= fetch origin
git -c url.https://github.com/.insteadof= push origin HEAD
```

若还命中 `url.https://.insteadof git://`，一般无害；GitHub SSH 问题主要来自上面那条。

**Docker Hub（IPv6 / oauth）**

Docker Desktop 上构建工作台时，Hub 拉取偶发 IPv6 或 oauth 失败。`make workbench-desktop-up` 已固定：

- Docker context：`desktop-linux`（可用 `DOCKER_DESKTOP_CONTEXT` 覆盖）
- `docker compose up --build --pull never`：本地已有基础镜像时不再向 Hub 重拉

首次或本地没有基础镜像时，先在网络正常时拉一次（例如 `python:3.12-slim-bookworm`、`node:22-alpine`、`nginxinc/nginx-unprivileged:1.27-alpine`），再跑 `make workbench-desktop-up`。

没有 Docker 时，在这台机器上分开起 API 和开发页面。同样只绑本机，同样不要设置
`OHBE_ALLOW_LIVE_SPEND`：

```bash
.venv/bin/pip install -e ".[api]"
.venv/bin/uvicorn ohbe.api.app:app --host 127.0.0.1 --port 8000
```

另一个 shell：

```bash
cd workbench
npm ci
npm run dev
```

开发页面是 `http://127.0.0.1:5173`。Vite 把 `/v1` 转到 `127.0.0.1:8000`。

`make doctor` 仍是第 5.1 节的离线预检。工作台在跑也不会让它发起模型调用。
第 6 节要等操作者自己执行。

## 6. 冒烟步骤

先不要跑。确认第 5.1 节的 `benchmark doctor` 已经退出码 0。doctor 会打印
下面这一段，但不会执行它。实验文件里没有 token 计数、价格或成功率。
`max_cost` 保持不写。占位 GPU 价还没换成这台机器的记录值时，算出来的金额
不能当成预算。

密钥齐了、并且 `LITELLM_BASE_URL` 已经指到 GPU 机之后，由操作者手动执行：

<!-- ohbe:next-smoke -->
```bash
# 预检通过后由操作者手动执行。benchmark doctor 不会运行下面的命令。
export LITELLM_API_KEY=...   # 只在 shell 中，不要写入仓库
export LITELLM_IMAGE_DIGEST=sha256:<实际拉到的 64 位摘要>   # 可选，不要猜
.venv/bin/benchmark run \
  --experiment experiments/live-pilot-smoke.yaml \
  --out results/live
.venv/bin/benchmark report \
  --input results/live/live-pilot-smoke/report-input.json \
  --out results/live/live-pilot-smoke/report
```
<!-- /ohbe:next-smoke -->

退出码：

| 码 | 含义 |
| --- | --- |
| 0 | 发布闸门全部通过 |
| 1 | 某个 harness 格子失败（此时不发布指标），或者矩阵跑完但闸门未过（审计和 report input 仍会写下） |
| 2 | 清单或前置条件被拒绝，没有发起模型调用 |

`results/live/` 里除了 README 都在 `.gitignore` 中。不要把 trace、
`report-input.json` 或渲染报告提交进仓库。

`benchmark audit-pilot` 仍然只打夹具，不读 `results/live/`。

## 7. 价格：什么时候能算出来

离线表是 `pricing/ohbe-offline-2026-10-09.yaml`。

- `openai/gpt-test` 和 `openai/gpt-test-mini` 是 `tokens`。`provider_cost` 和
  `normalized_cost` 都是每百万 token 的标价。
- `vllm/REPLACE_ME-instruct` 和 `ollama/REPLACE_ME` 是
  `normalized_gpu_seconds`，GPU 类型 `rented-placeholder`，
  `gpu_cost_per_hour` 为 `"3.60"`。CostEngine 用 Decimal 除以 3600，得到
  每 GPU 秒 `0.001` 美元。这个 3.60 是为了让商是有限小数而放的占位，不是
  这台机器的租金，也不是厂商报价。

调用要同时满足下面两条，CostEngine 才会给出金额：

1. token usage 是 `present`（计数可以是明确的 0，不能是缺省）。
2. 事件上有 `normalized_gpu_seconds`：非负十进制字符串；整数秒也可以是
   JSON 整数。JSON 浮点会被拒绝。

这时 `provider_cost` 是计算出来的 `0`（本地权重没有厂商 token 账单），
`normalized_cost` 是秒数乘以每秒价格。执行总成本是 `normalized_cost`。
事件上的 `cost_amount` 和代理的 `x-litellm-response-cost` 都不会抄进这两个
字段。

缺 usage，或者有 token 但没有 `normalized_gpu_seconds` 时，两个字段都是
`invalid`，金额是 null。null 不是 0。OHBE 不会用 token 数或延迟去估 GPU 秒。

换成真实租金时，改 `gpus` 里的 `gpu_type` 和 `gpu_cost_per_hour`（仍然写
十进制字符串，不要写 YAML 浮点），让模型行的 `gpu_type` 指向它，然后重新计算
文件字节的 sha256，把它和实验引用钉在一起：

```bash
python -c "from ohbe.cost import load_pricing_table; t=load_pricing_table('pricing/ohbe-offline-2026-10-09.yaml'); print(t.pricing_id, t.version, t.content_hash)"
```

## 8. 可复现性要记下的内容

一次联调至少留下这些，和 `results/live/<experiment_id>/manifest.json` 放在
一起。manifest 里没有 API key，也没有观测到的花费数字。

| 记下 | 从哪里来 |
| --- | --- |
| OHBE 版本和本仓库 git commit | `ohbe.__version__`，`git rev-parse HEAD` |
| OpenBot 40 位 SHA | `OPENBOT_COMMIT` |
| OpenBot 镜像引用（若用了） | `OPENBOT_IMAGE`，必须是 SHA tag 或 `sha256:` digest |
| LiteLLM 镜像 | `ghcr.io/berriai/litellm:v1.104.2`；拉到了就再记 `LITELLM_IMAGE_DIGEST` |
| 价格表 | 路径、`pricing_id`、`version`、`content_hash` |
| GPU | 实际记录的 `gpu_type` 和 `gpu_cost_per_hour`（占位的 3.60 要标明是占位） |
| 实验 | `experiment_id`、`seed`、`repetitions`、`timeout_seconds`、`max_total_tokens`；`max_cost` 若未设置就记 null |
| 出站 | 策略版本、网关 `host:port`、`LITELLM_BASE_URL` 的 origin（不要带 key） |
| 模型 | `model_id`、`litellm_model`、vLLM / Ollama 的模型修订（若知道） |
| 报告 | `report.json` 里的 `input_sha256`；`routing_optimality` 和 `model_independence` 应为 null |

不要记录：`LITELLM_API_KEY`、`LITELLM_MASTER_KEY`、任何云厂商 key、trace 里的
用户家目录。

`routing_optimality` 和 `model_independence` 保持 null。工具面一致性和
routing regret 都不在这次联调的结论里。
