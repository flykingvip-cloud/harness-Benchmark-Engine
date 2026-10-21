# 观察壳（Observation shell）

监测页 `/monitor` 的观察区是一个**实验观察者**。测试人员用它看同一已确认方案里多个 Harness（`paired_harness`）或多个模型（`cross_model`）的 Dry-run。评测页 `/rounds` 只起草并确认方案，确认后用「去监测」进入。它不是通用会话管理器：没有恢复会话，没有删除会话，也不把窗格写成可以离开本方案单独续聊的 Claude 会话。

The `/monitor` observation area is an **experiment observer**. Testers use it to watch a Dry-run across harnesses (`paired_harness`) or across models (`cross_model`) inside one confirmed plan. `/rounds` only drafts and confirms the plan, then links here. It is not a generic session manager: there is no resume, no delete, and a pane is not a chat that lives outside the plan.

## 布局 / Layout

活动 `PlanSession` 在监测页顶栏选定之后，观察区换成左右壳：

| 区域 | 内容 |
| --- | --- |
| 左：窗格目录 | `paired_harness` 每个 Harness 一行；`cross_model` 每个模型一行。点击切换当前窗格。 |
| 右上：参数记录 | 阶段、最早/最近时间、任务 / 模型 / Harness id。延迟和成本只在事件写明时填上，否则是占位。横幅保持 `spend:none` / `executed:false`。 |
| 右下：过程记录 | 把该窗格可见的 `ObservationEvent` 收成对话式回合：调度行与该窗格的步骤。服务端英文原文保留。 |
| 下方 | 五维对比和轮次报告仍在壳下面。封存之后也不拆掉。 |

Left: a directory. Right top: a parameter record. Right bottom: a process log. `DimensionCompare` and the round report stay below the shell, including after seal.

身份仍是 `pane_id`。配对桩事件的 `pane_id` 是 Harness id，两个窗格共享夹具池里的第一个模型。跨模型桩事件的 `pane_id` 是模型 id，Harness 不变。步骤还没显现时，目录按同一套 id 预告；Tick 揭开更多桩事件后，参数和时间更新，过程记录变长。

Identity stays `pane_id`. Paired stub panes are harness ids sharing the fixture pool's first model. Cross-model stub panes are model ids under one harness. Before step events, the directory previews those ids. As Tick reveals more stub events, the record and the log grow.

## 两种模式 / Modes

- **P0 `paired_harness`：** 目录是 Harness 窗格（受试与参照）。这是默认评测，不是把多个会话并排管理。
- **P1 `cross_model`：** 同一壳。目录是模型。还没有 Dry-run 作业时，行来自本方案的对比槽（槽位预览）。作业打开后，行换成夹具池上的 Dry-run 窗格。对比槽板仍是原来的 `ComparisonSlots`，不在这里再编辑。槽位 id 和窗格 id 可以不同，两边都标出来，避免把 Dry-run 读成已经按准入槽跑过。

`cross_model` reuses the same shell. Slot ids and dry-run pane ids can differ; both stay labeled. The slot board is still `ComparisonSlots`.

## 下发本轮 / Send this round

套件在评测页确认后，监测页的「下发本轮套件」调用现有的 `POST /v1/rounds/plans/{id}/start`（`live: false`）。服务端把**同一已确认套件**交给该方案的每个窗格，并打开 Dry-run 作业。「推进 Tick」仍是 `POST /v1/rounds/jobs/{id}/tick`。切到下一任务时，执行对话自动填入该任务正文并扇出到全部窗格。桩过程只回放焦点任务；后面的任务只留下未测量的桩摘要，不假装测完整个套件。

After confirm on the evaluation page, Monitor's 「下发本轮套件」 calls the existing start route with `live: false`. That fans the same confirmed suite out to every pane and opens the dry-run job. Tick is unchanged. Switching tasks fills the execution dialogue and fans that text out to every pane. The stub replays the focus task; later tasks keep an unmeasured stub summary and do not pretend the whole suite was measured.

## 不做什么 / Out of scope

- 不解锁 live spend。双锁不变。响应仍是 `executed: false`、`spend: none`、`network: not_contacted`，标签 Dry-run。
- 不扩大 `HARNESS_ORDER`。竞争 Harness 仍只登记在方案里。
- 不替换 `PlanSession` 隔离，不改 `pane_id`，不删五维对比和报告。
- 不把缺失的延迟或成本写成 0。
- 不提供恢复、删除或跨方案的会话库。
