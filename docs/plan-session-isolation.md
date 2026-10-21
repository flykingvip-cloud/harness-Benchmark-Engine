# 方案会话隔离（PlanSession）

每个方案是一套独立的比较系统。在评测页切换方案（候选方案列表中的一行，或「添加方案」）之后，列表以下的模式、对比槽、套件、观察和报告都换成这一方案自己的内容。

本文只定义目标。它不改变工作台行为，不改变 HTTP 行为，也不解锁 live spend。

核对基线：`main` `465481ac`（`评测页：候选方案可添加，配置表单默认收起`，#32）。该提交只改了 `workbench/src/pages/Rounds.tsx` 的候选方案列表和默认收起的配置表单。比较槽、模式单选、夹具适配块和路径条的数据归属与 #32 之前相同。

相关现况说明：

- 槽位与 `pool-admitted`：E2（`7e18fa1`）、E3（`0c67874`），以及 [evaluation-framework.md](evaluation-framework.md) 的工作台表单一节。
- 生命周期：`DraftPlan → ConfirmedSuite → Running → RoundReport → ForkPlan`。
- 模式：`paired_harness` 是默认评测；`cross_model` 是固定 harness 下的模型选择，不是模型竞技场排行榜。

## 1. 问题、目标、非目标

### 1.1 问题

Michael 的要求：切换不同方案页签时，下方全部内容按该方案重配。方案之间不能共用一套比较现场。

在 `465481ac` 上，服务端已经按 `plan_id` 保存了方案文档、套件、作业、事件和报告，确认套件时也会把当时的槽位抄进 `RoundStore.comparison_by_plan`。页面却把「正在看哪一个方案」和「页面上正在比较什么」拆开了。四处缺口如下。

**对比槽是进程里的一份全局列表，不按 `plan_id` 键控。**

`IntakeService.slots` 是一条 `list[str]`。`GET` / `POST /v1/rounds/comparison-slots` 读写这一条。`POST /v1/rounds/plans` 和 `POST /v1/rounds/plans/{plan_id}/confirm` 都读它：请求里的 `model_ids` 必须与它一致，否则 `409 slot_mismatch`。确认从草稿变为已确认时，把这份 id 抄到 `comparison_by_plan[plan_id]`；再次确认不改写这份抄件。抄件存在，但槽位板仍绑在全局路由上。

工作台 `ComparisonSlots` 始终 `GET` / `POST` 这条全局路由，并且放在 `#plan-editor` 外面。未选方案、正在「添加方案」、或已打开某个方案时，看到的是同一块板。`openPlan` 只在套件已确认时把 `comparison_model_ids` 放进 `confirmedSlots` 当作冻结显示；草稿方案的板仍然是全局列表。在方案 A 上改槽并保存，会改掉方案 B 接下来生成或确认时读到的列表。

确认前，`RoundPlanDetail.comparison_model_ids` 仍是 `[]`。已绑定的 id 在 `pool_model_ids` 上（`pool-admitted`）。`tests/test_catalog_hydrate.py` 固定了这条契约：创建和草稿套件的 `comparison_model_ids` 为空，确认之后才等于当时的全局槽；之后把全局槽清空再确认，已冻结的抄件保持不变。

**模式单选是页面级状态，位于候选列表之上。**

`RoundsPage` 用 `useState<RoundMode>("paired_harness")`，单选组（`role="radiogroup"`）画在 `data-testid="candidate-plans"` 之上。`openPlan` 会把 `plan.mode` 写进这同一个 state，所以单选会跟着变，但它仍是整页控件。`addPlan` 清掉详情、套件、作业和报告，不清 `mode`。拨动单选会改草稿名称，并决定下一次「生成」使用的模式；已经显示在下面的套件、观察窗和报告不会跟着换成另一个模式的内容。

**`openPlan` 会装入一个方案的套件、作业和报告，夹具模型适配块始终是整页的。**

`openPlan` 按该 `plan_id` 请求方案详情、套件、作业和报告，写入页面级 state。夹具适配另走 `useLoad(routes.modelFit)`，即 `GET /v1/analysis/model-fit`。`RoundStore.model_fit` 固定返回种子报告 `fixture-cross-model-report`（`label: fixture`，`routing_optimality` 与 `model_independence` 均为 `null`）。页面在活动报告下面无条件渲染「夹具上的模型适配」（`data-testid="fixture-fit-cards"` 等）。因此打开没有报告的 `fixture-paired`，或打开一次干跑封存的 `not_measured` 报告时，下面仍然是 `fixture-cross-model` 的前沿表。

**路径条和观察区不严格属于 `activePlanId`。**

页面没有 `activePlanId`。选中行只是 `editor === "edit" && detail.plan.plan_id` 与该行相等。路径下标是：

```text
report ? 3 : job ? 2 : （名单为空且没有 detail）? 0 : 1
```

它看的是页面上最后留下的 `report` / `job` / `detail`。观察区 `#observation` 始终挂载，窗格来自页面级 `events`。`openPlan` 在请求返回之前不清除上一个方案的作业、事件和报告，切换过程中仍显示上一套比较。`addPlan` 会清这些字段，但槽位板和夹具适配块仍留在原处。

已经按方案分开、隔离时必须保留的部分：

| 已有归属 | 位置 |
| --- | --- |
| `RoundPlan.mode`、harness、池引用 | `RoundStore.plans` |
| 套件、作业、报告 | `suite_by_plan`、`job_by_plan`、`report_by_plan` |
| 观察事件 | `events[job_id]` |
| 确认后的槽位抄件 | `comparison_by_plan`（仅在草稿首次确认时写入） |
| 池种类与绑定 id | `pool_kind`、`pool_model_ids` |
| 分叉 | 新的草稿 `plan_id`；复制池绑定；不复制套件、作业、报告和 `comparison_by_plan` |

种子方案：

| `plan_id` | 模式 | 套件 / 作业 / 报告 |
| --- | --- | --- |
| `fixture-paired` | `paired_harness` | 无 |
| `fixture-cross-model` | `cross_model` | `fixture-cross-model-suite` / `fixture-cross-model-job` / `fixture-cross-model-report` |

### 1.2 目标

1. 选定一个方案之后，列表以下只渲染该方案的 `PlanSession`。
2. 「添加方案」打开一份空白起草会话，列表以下不再留下上一个方案的槽位、套件、观察或报告。
3. 未选定方案时，不把全局槽位或夹具适配演示当成当前比较。
4. P0 只在工作台把现有接口绑定到活动方案。P1 再把槽位的权威从 `IntakeService.slots` 移到方案本身。
5. 确认仍然冻结该方案自己的槽位抄件和 `suite_hash`。改任务仍然走分叉。

### 1.3 非目标

这些约束在 P0 和 P1 都保持：

- **保留 Dry-run 标签。** `POST .../start` 与 `POST .../tick` 的响应继续带 `label: "Dry-run"`、`fixture_pool_id: "pool-pilot"`。干跑作业的 `model_ids` 仍是夹具池 `pool-pilot` 的模型（今天是 `openai/gpt-test` 与 `openai/gpt-test-mini`），即使该方案的槽位抄件是准入目录里的另一组 id。观察区继续标明 Dry-run 和 `pool-pilot`。隔离不做的事情，是把准入槽位写进干跑作业，冒充已经按这些模型跑过。
- **花费继续双锁。** `live: true` 且环境变量 `OHBE_ALLOW_LIVE_SPEND` 不是 `1` 时，仍是 `403 live_spend_locked`。两个条件都满足时，方案路由仍是 `executed: false`、`spend: none`、`network: not_contacted`。本文不解锁 spend，实现隔离的 PR 也不解锁。
- **不编造 Oracle。** `routing_optimality` 与 `model_independence` 保持 `null`。帕累托标记仍是该 harness、该冻结套件下的适应信号，不是路由最优，也不是排行榜。
- **不改两种模式的定义。** `paired_harness` 仍是共享模型池上的 harness 对照。`cross_model` 仍是固定一个 harness、变化模型。
- **不把 `PlanSession` 落盘，也不要求进程重启后还在。** 方案存储今天就是进程内的。P0 / P1 维持这一点。
- **不改总览 `/` 和报告 `/results`。** 这两页可以继续读取 `GET /v1/analysis/model-fit`，并保持夹具标签。它们不是方案页签。
- **配置表单默认收起保持不变。** #32 的「添加方案」和候选行选中行为保留。隔离改变的是收起之后列表以下还剩什么，以及展开之后读哪一份会话。

## 2. 目标模型：PlanSession

页面持有：

```text
activePlanId: string | null
sessions:     map of plan_id → PlanSession
composer:     PlanSession | null    # 仅「添加方案」、尚未 POST /plans 时
```

`activePlanId === null` 且 `composer === null`：只显示准备区和候选列表。  
`composer !== null`：列表以下是这份尚未落库的起草会话。  
`activePlanId` 指向某个 `plan_id`：列表以下只读 `sessions[activePlanId]`。

```text
PlanSession
  planId            # 起草中为空
  mode              # paired_harness | cross_model
  slotsSnapshot     # 有序 model_id；顺序与 pool-admitted 一致
  slotsStatus       # draft | frozen
  suite             # TaskSuite | null
  job               # DispatchJob | null
  observation       # 该 job 当前阶段可见的事件
  reportRef         # report_id | null，打开报告步时再装入 RoundReport
  lifecycle         # draft_plan | confirmed_suite | running | round_report | fork_plan
  origin            # fixture | session
  poolKind          # admitted | fixture
  poolModelIds      # 生成时绑定的 id
```

字段来源：

| 字段 | 今天读什么 | 目标 |
| --- | --- | --- |
| `mode` | `RoundPlan.mode` 已按方案保存；控件是页面 state | 会话字段。方案文档一旦创建，模式不可在原文档上改；换模式是新建方案 |
| `slotsSnapshot` | 全局 `IntakeService.slots`；确认后另有 `comparison_by_plan` | 草稿：该方案自己的列表。已确认：冻结抄件 |
| `suite` | `suite_by_plan` | 只随活动方案装入 |
| `job` | `job_by_plan` | 只随活动方案装入 |
| `observation` | `events[job_id]`，画在页面级 state 上 | 只随 `session.job` 装入 |
| `reportRef` | `report_by_plan` | 只随活动方案装入。`fixture-cross-model-report` 只属于 `fixture-cross-model` |

不变量：

- 列表以下的渲染只读活动会话或空白起草会话。切换 `activePlanId` 不 `POST` 槽位，不确认，不 tick。
- `slotsStatus === frozen` 当且仅当该方案套件为 `confirmed`。冻结后改槽返回冲突；改任务继续走现有的分叉（`409 suite_already_confirmed`）。
- 干跑观察窗里的模型 id 可以与 `slotsSnapshot` 不同。前者是 `pool-pilot`，后者是该方案的准入槽位。两列都要出现，并带上各自的标签，避免把 Dry-run 窗格读成槽位板。
- 分叉产生新的 `planId`。子方案是草稿：按值复制父方案的池绑定和 `slotsSnapshot`，不复制套件、作业、报告。子方案随后可以改自己的草稿槽，父方案的冻结抄件保持不变。

## 3. 交互

准备区（模型池、Harness 库、夹具目录）留在列表之上，各方案共用。它不是比较系统。

### 3.1 选定与空白

| 操作 | 列表以下 |
| --- | --- |
| 初次进入，未选行，也未点「添加方案」 | 不渲染槽位板、套件、观察、报告、夹具适配块。路径条停在「方案」（列表本身），不高亮「观察」或「报告」 |
| 「添加方案」 | `activePlanId = null`，打开 `composer`。模式单选、名称和轴在这份起草里。槽位从空列表开始，不继承全局槽，也不继承上一方案。没有套件、作业、报告 |
| 点击候选行 | `activePlanId` 设为该 `plan_id`，关闭 `composer`。在绘制新 id 之前先卸下上一会话的视图（上一份留在 `sessions` 里，不留在画面上）。请求返回后只填这一份 |

选中行保持 `aria-current` 与 `data-testid="candidate-plan"`。活动会话容器使用 `data-testid="plan-session"` 和 `data-plan-id`。

### 3.2 模式单选放进方案编辑器

单选组从候选列表上方移入 `#plan-editor`。列表的「模式」列仍显示每一行自己的 `plan.mode`。页面不再有一个作用于所有方案的模式。

- 起草中（`composer`，或已创建但用户明确在新建）：可以改模式。
- 方案文档已经存在：单选显示该 `plan.mode`，并且不可改。要另一模式就再点「添加方案」。
- 打开 `fixture-paired` 时单选为 `paired_harness`。打开 `fixture-cross-model` 时单选为 `cross_model`。

### 3.3 列表以下按会话显示或收起

活动会话存在时，按该会话的生命周期显示：

| 块 | 显示条件 |
| --- | --- |
| 配置表单 | 起草，或正在编辑该方案。字段来自该会话 |
| 槽位板 | 始终属于该会话。`slotsStatus === draft` 时可改；`frozen` 时只读，id 为该方案的抄件 |
| 套件表 | `suite !== null` |
| 观察区与阶段条 | 区域可以留标题，内容来自 `session.job` 与 `session.observation`。没有作业时是空状态，文案带上该 `plan_id`，不沿用上一作业的阶段 |
| Dry-run 横幅 | 观察区里保持现有 `data-testid="dry-run-label"`。有作业时同时显示 `fixture_pool_id` |
| 报告 | `reportRef !== null` 时显示该报告的五维、发现和前沿 |
| 夹具适配块 | 仅当 `reportRef === "fixture-cross-model-report"` |

未选中任何方案时，上表全部不挂载。

路径条只反映活动会话：

| 会话状态 | 高亮 |
| --- | --- |
| 无活动会话 | 方案 |
| `draft_plan` 或已确认但还没有作业 | 方案 |
| 已有作业、还没有报告 | 观察 |
| 已有报告 | 报告 |

另一方案已经跑到报告，不能把当前方案的路径条推到「报告」。

### 3.4 夹具演示放在哪里

数据在种子方案 `fixture-cross-model` 的报告 `fixture-cross-model-report` 上。`GET /v1/rounds/reports/fixture-cross-model-report` 与今天的 `GET /v1/analysis/model-fit` 读的是同一份。响应保持 `label: "fixture"`、`spend: "not_live"`，`routing_optimality` 与 `model_independence` 为 `null`。美元数仍是夹具试点里已有的 `0.0009` 与 `0.00045`，不是新测量。

在 `/rounds` 上，这块演示只出现在 `fixture-cross-model` 的报告区，沿用现有的 `fixture-fit-cards`、`fixture-fit-findings`、`fixture-fit-frontier`。`fixture-paired`、会话方案的 `not_measured` 报告、以及空白起草，都不挂载它。

总览和报告页继续可以画这份夹具。那是页面自己的夹具图，不从方案会话里借状态。

### 3.5 槽位编辑

- 草稿：增删和「推荐」只改活动会话的 `slotsSnapshot`。
- 已确认：板冻结，显示该方案的抄件。
- `ComparisonSlots` 在全局槽为空时自动推荐并 `POST` 的效果，不能在切换方案或挂载板时触发。推荐只作为活动草稿上的按钮。
- 切换方案不发出槽位写请求。

## 4. P1 的 API

P1 采用**按方案保存的槽位**。确认是冻结点，这一点今天已经有一半：`comparison_by_plan` 在首次确认时写入，再次确认不改写。

只做「确认时抄一份全局列表」不够。两份草稿在确认前仍共用 `IntakeService.slots`，切换未确认的方案时槽位还是会串。Michael 的要求包括草稿和两行夹具方案，不限于已确认套件。因此权威列表从创建就挂在 `plan_id` 上，确认只负责冻结。

### 4.1 新路由

```text
GET /v1/rounds/plans/{plan_id}/slots
PUT /v1/rounds/plans/{plan_id}/slots
```

`PUT` 的请求体沿用 `ComparisonSlotsRequest`（`model_ids`）。响应：

| 字段 | 值 |
| --- | --- |
| `plan_id` | 路径中的方案 |
| `model_ids` | 该方案的有序抄件 |
| `status` | `draft` 或 `frozen` |
| `source` | `plan_slots` |
| `spend` | `none` |

校验与今天的 `replace_slots` 相同：目录内成员、无重复、长度不超过 16。非成员为 `404 not_in_catalog`。重复或过长为 `422 invalid_request`。

写入规则：

- `PUT` 只替换这一方案的草稿抄件。
- 套件已确认：`409 slots_frozen`。
- `pool_kind === "fixture"` 且列表非空：`409 fixture_pool`，与今天确认夹具池方案时的规则相同。夹具方案的抄件保持空列表。
- 准入方案在生成时绑定的 `pool_model_ids` 与抄件保持一致。生成之后若 `PUT` 改变了 id，下一次确认前必须重新生成，否则确认仍是 `409 slot_mismatch`（「生成之后槽位变了」）。P1 可以把这条检查放在 `PUT` 上：已绑定 `pool-admitted` 的方案，`PUT` 的 id 必须等于 `pool_model_ids`，否则 `409 slot_mismatch`，并提示重新生成。这样草稿板不会显示一份确认时必然被拒绝的列表。

### 4.2 生成、确认、分叉

`POST /v1/rounds/plans` 不再读取 `IntakeService.slots`。

- 请求体里的 `model_ids` 成为新方案的初始 `slotsSnapshot`。非空且都是目录成员时，绑定 `pool-admitted`，规则与今天相同。
- 省略 `model_ids` 或传空列表：走夹具池路径（仅当没有要求 `pool-admitted`）。抄件为空。对 `pool-admitted` 仍是 `409 slots_required`。
- 创建响应里的 `comparison_model_ids` 从这时起等于这份抄件，并增加 `slots_status: "draft"`。冻结信号仍是套件状态，不是「列表非空」。

`POST .../confirm` 冻结方案上已有的抄件，不读全局槽。

- 省略 `model_ids`：冻结已存储的抄件。
- 带上 `model_ids`：必须与已存储抄件相同，否则 `409 slot_mismatch`。
- 首次确认把 `slots_status` 设为 `frozen`。再次确认不改写抄件，也不改 `suite_hash`。今天「清空全局槽后再确认，抄件不变」的行为，改成「全局路由不再参与；再次确认仍不改写」。

`POST .../fork`：子方案得到父方案 `slotsSnapshot` 的按值副本，`slots_status` 为 `draft`，池绑定与今天一样复制。不复制套件、作业、报告。

`GET /v1/rounds/plans/{plan_id}` 与套件视图里的 `comparison_model_ids` 表示该方案的抄件（草稿或已冻结）。新增 `slots_status`。P0 及以前，这个列表在确认前为空；P1 起，创建成功后即为抄件。`tests/test_catalog_hydrate.py` 里「创建后 `comparison_model_ids == []`」的断言要在 P1 的实现 PR 里改成等于初始抄件，并断言 `slots_status == "draft"`。确认后列表不变、`slots_status == "frozen"`、清空操作不能改写抄件，这些断言保留。

### 4.3 全局路由的兼容窗口

P1 保留 `GET /v1/rounds/comparison-slots`，返回进程里残留的全局列表，`source` 仍是 `round_comparison_slots`。它不作为任何方案页签的数据源。

`POST /v1/rounds/comparison-slots` 在 P1 改为 `409 slots_unscoped`，提示改用 `PUT /v1/rounds/plans/{plan_id}/slots`。实现 PR 同时改 `tests/test_catalog_hydrate.py`、`tests/test_model_intake.py`，以及 [sdk-surface.md](sdk-surface.md) 里 `OhbeClient.rounds.comparisonSlots.replace()` 的说明。目录校验（重复、非成员、长度）改由方案路由覆盖。

花费字段保持 `spend: none`。这些路由不打开套接字，不调用模型。

## 5. 从当前全局槽位迁移

存储是进程内的。重启后全局槽已经是空的（`test_catalog_hydrate` 在新进程里断言 `model_ids == []`）。没有文件要迁移。

P1 进程启动、种入夹具方案之后：

| 方案 | 槽位抄件 |
| --- | --- |
| `fixture-paired`、`fixture-cross-model` | 空列表，`pool_kind: fixture`。不把 `IntakeService.slots` 抄上去。演示用的模型 id 留在夹具作业和夹具报告上 |
| 已确认的会话方案 | `comparison_by_plan` 中已有的元组，`frozen`。全局列表与它不同时，以抄件为准 |
| 草稿且 `pool_kind: admitted` | `pool_model_ids`，`draft`。全局列表已经漂移时，以生成时绑定的 id 为准 |
| 草稿且 `pool_kind: fixture` | 空列表 |

孤儿的全局列表（与每个方案的抄件都不同）只留在废弃的 `GET` 里，不画进任何方案。

P0 在 API 落地之前，用客户端映射做同一次迁移：

- 每个方案：`GET` 详情。套件已确认则 `slotsSnapshot = comparison_model_ids` 且冻结。草稿且 `pool_kind === "admitted"` 则 `slotsSnapshot = pool_model_ids`。两者不同时不采用全局 `GET` 的结果。
- 「添加方案」的 `composer` 从空列表开始。全局 `GET` 不写入 `composer`，也不写入已有方案。
- 活动草稿上的编辑留在 `sessions` / `composer`。切换方案不 `POST`。
- 调用今天的生成或确认之前，先把**这一份**抄件 `POST` 到全局路由，再带上相同的 `model_ids` 调用生成或确认，以满足现有的 `409 slot_mismatch`。这是单页里的临时桥。刷新会丢掉尚未生成的起草编辑；已生成方案以 `pool_model_ids` 或已确认抄件为准。P1 删掉这次全局 `POST`。

P0 必须挡住的串线：用户先生成方案 A（槽位 `[a]`），再生成方案 B（槽位 `[b]`），然后回到 A 并确认。确认 A 之前要把全局列表写成 `[a]`。画面在整个过程中显示 A 自己的 `[a]`，不因为全局列表已经变成 `[b]` 就重画 A。

## 6. 分期与「OHBE 合入验收」

### 6.1 P0：只绑定界面

不改路由契约，不改花费锁，不改 Dry-run 标签。

- 客户端 `PlanSession` 映射、`activePlanId` 和起草 `composer`。
- 模式单选移入方案编辑器；已存在的方案显示自身模式且不可改。
- 路径条、槽位板、套件、观察、报告、夹具适配块按第 3 节挂到活动会话。
- 选定方案时先卸下上一会话的视图，再请求。
- 槽位编辑留在该会话里；仅在该方案生成或确认的前一刻 `POST` 全局槽。
- 去掉「板一挂载且全局槽为空就自动 `POST` 推荐」的效果。推荐按钮只改活动草稿。
- 工作台测试覆盖：从 `fixture-cross-model` 切到 `fixture-paired` 后，夹具适配节点不在文档中；从带报告的方案切到无报告的方案后，观察阶段条不保留上一作业。

### 6.2 P1：方案槽位 API

按第 4 节实现。P0 的「确认前 `POST` 全局槽」删除。全局 `POST` 变为 `409 slots_unscoped`。`comparison_model_ids` 在创建时即为抄件，并带 `slots_status`。分叉按值复制抄件。

P1 的测试同时保留：`live: true` 且未设置 `OHBE_ALLOW_LIVE_SPEND=1` 时 `403 live_spend_locked`；start 的 `label` 为 `Dry-run`、`fixture_pool_id` 为 `pool-pilot`、`executed` 为 false、`spend` 为 `none`；模型适配响应里 `routing_optimality` 与 `model_independence` 为 `null`。

### 6.3 验收清单

实现 PR 用下面的清单做「OHBE 合入验收」。P0 可以先交前半；标了 P1 的条目在 API PR 上验收。本文档 PR 不实现这些条目。

**切换**

- [ ] P0 打开 `fixture-paired`：模式为 `paired_harness`；无套件、无作业、无报告；`fixture-fit-cards` 不在文档中；观察区没有 `fixture-cross-model-job` 的阶段。
- [ ] P0 打开 `fixture-cross-model`：模式为 `cross_model`；套件 `fixture-cross-model-suite`；作业 `fixture-cross-model-job`；报告 `fixture-cross-model-report`；夹具适配块只出现在这一方案下；`routing_optimality` 与 `model_independence` 为 `null`。
- [ ] P0 从 `fixture-cross-model` 点「添加方案」：列表以下是空白起草；上一份夹具报告、窗格和槽位列都不在画面上；路径条不高亮「报告」。
- [ ] P0 再点回 `fixture-cross-model`：报告与夹具适配块回来；起草里如果改过模式，不改这一行的 `cross_model`。
- [ ] P0 生成方案 A（槽位一组 id）和方案 B（另一组 id）后往返：槽位列、套件任务和观察窗格各自跟着 `data-plan-id` 变，不留下另一方案的列。
- [ ] P0 切换过程中，请求未返回时不继续显示上一方案的作业 id 或报告 id。

**冻结与模式**

- [ ] P0 已确认方案的槽位板只读，id 等于该方案详情里的 `comparison_model_ids`。
- [ ] P0 已存在方案的模式单选不可改；「添加方案」才能选另一个模式。
- [ ] P1 确认 A 之后，对 B 的 `PUT .../slots` 不改变 A 的 `GET .../slots`。
- [ ] P1 对已确认方案 `PUT .../slots` 得到 `409 slots_frozen`。再次确认不改 `suite_hash`，也不改抄件。
- [ ] P1 分叉得到新 `plan_id`、草稿抄件（按值复制）以及空的套件和报告。父方案的冻结抄件不变。

**路径、干跑、花费、Oracle**

- [ ] P0 路径条下标只跟活动方案的生命周期走。另一方案已有报告时，当前草稿仍停在「方案」。
- [ ] P0 / P1 `POST .../start` 仍为 `label: "Dry-run"`、`fixture_pool_id: "pool-pilot"`、`executed: false`、`spend: none`、`network: not_contacted`。作业 `model_ids` 仍是夹具池模型。观察区仍显示 Dry-run 横幅。
- [ ] P0 / P1 `live: true` 且未双锁放开时为 `403 live_spend_locked`。双锁环境变量为 `1` 时，方案路由仍然 `executed: false`、`spend: none`。
- [ ] P0 / P1 不新增、不填充 Oracle 字段。帕累托行不标成路由最优。

**API**

- [ ] P1 `POST /v1/rounds/plans` 不读全局槽。创建响应的 `comparison_model_ids` 等于请求中的初始抄件，`slots_status` 为 `draft`，`pool-admitted` 的 `pool_model_ids` 与抄件一致。
- [ ] P1 `POST /v1/rounds/comparison-slots` 为 `409 slots_unscoped`。`GET` 仍可返回残留列表，且方案页签不使用它来绘制。
- [ ] P1 夹具方案 `PUT` 非空槽位为 `409 fixture_pool`。

## 7. 风险

- **P0 的服务端仍是一条全局槽。** 刷新丢掉未生成的起草编辑。第二个客户端，或切换时误发的 `POST`，会再次串线。验收要包含「切换不发 `POST /v1/rounds/comparison-slots`」。P1 之前应在界面上说明：确认前的槽位编辑留在这个方案的页面状态里，刷新后以该方案已绑定的 `pool_model_ids` 或已确认抄件为准。
- **挂载时自动推荐会写全局槽。** 今天 `ComparisonSlots` 在目录有候选且全局槽为空时会自行 `POST` 推荐结果。P0 若保留这个效果，打开空槽方案就会改掉其他方案即将确认的列表。
- **`openPlan` 的异步窗口。** 若不在设置 `activePlanId` 的同一时刻卸下上一份作业和报告，路径条和观察区会在请求期间指向旧方案。
- **提前填充 `comparison_model_ids` 会被当成已冻结。** P1 创建后该字段就非空。冻结必须以 `slots_status` 或套件 `confirmed` 为准，不能以列表长度为准。P0 继续只用已确认套件上的抄件做只读板。
- **夹具演示从页脚拿掉之后，像是演示丢了。** 它只在选中 `fixture-cross-model` 时出现。验收清单把检查点放在这一行上，而不是放在页面底部。
- **干跑窗格与准入槽位 id 不一致。** 这是现有的 E3 行为。隔离之后两块会上下相邻，更容易被看成数据错误。观察区必须继续标 Dry-run 和 `pool-pilot`。不要为了让两列看起来一样而改作业的 `model_ids`。
- **分叉复制槽位是新行为。** 必须按值复制。和父方案共享同一个列表对象时，子方案的草稿编辑会改到父方案的冻结抄件。
- **废弃全局 `POST` 会碰到 SDK 与测试。** `OhbeClient.rounds.comparisonSlots.replace()`、`docs/sdk-surface.md`、`tests/test_catalog_hydrate.py`、`tests/test_model_intake.py` 都在 P1 实现 PR 里改。本文档 PR 不动它们。
- **不要用隔离顺手解开花费或补上 Oracle。** 路径条、槽位和报告怎么按方案拆开，都不改变双锁，也不把帕累托或费用差写成 `routing_optimality`。
