# 监测页（Monitor）

评测页 `/rounds` 起草并确认方案。监测页 `/monitor` 只加载**已确认、运行中或已封存**的方案，下发 Dry-run，并盯每个窗格的执行对话。`paired_harness` 与 `cross_model` 共用同一壳：左目录、右上参数记录、右下执行对话。

The evaluation page drafts and confirms a plan. Monitor loads a confirmed, running, or sealed plan, sends a dry-run, and watches each pane. Both modes share that shell.

## 已拍板的默认

线框 v2 留下的问题按这些默认落地：

| 问题 | 默认 |
| --- | --- |
| 导航与路由 | 侧栏「监测」，路由 `/monitor` |
| 评测页上的观察壳 | 整壳移走。套件确认后主按钮是「去监测」 |
| 自动切换 | 当前任务的每个窗格都有桩结果，或失败被标成跳过之后，才切到下一任务并扇出。跳过不挡住其他窗格。缺事件保持等待，不算 0 |
| 结果块 | 只写本任务桩摘要。焦点任务说明桩回放不是全套件测量；其余任务写明未测量。五维对比留在报告页 |

Pause is local. It stops Tick and auto-advance. It does not call a new route and does not change the spend lock.

## 两种模式

- `paired_harness`：目录一行一个 Harness。扇出到受试和参照窗格。
- `cross_model`：同一壳。目录一行一个模型，Harness 固定。作业打开前，行可以来自对比槽预览；作业打开后换成夹具池窗格。槽位 id 和窗格 id 都标出来。对比槽仍在评测页编辑。

## 诚实性

`POST /v1/rounds/plans/{id}/start` 与 `POST /v1/rounds/jobs/{id}/tick` 仍传 `live: false`。横幅保持 `spend:none` / `executed:false`。延迟和成本只在事件写明时填上。服务端桩只回放 `focus_task_id`。

线框原文：[`ohbe-monitor-wireframe.html`](ohbe-monitor-wireframe.html)。

## 自动切换节奏与跳过（P2）

| 控件 | 行为 |
| --- | --- |
| 自动切换任务 | 各窗格都有结果或跳过之后，按所选间隔（1s / 2s / 3s，默认 2s）进入下一任务并扇出 |
| 间隔选择 | 仅影响客户端倒计时；不改 API，不碰 spend 锁 |
| 暂停 | 停 Tick 与倒计时；解锁后也不自动改锁 |
| 跳过并继续 | 把当前任务仍 pending 的窗格标成手动跳过，不挡住其余窗格；不编造指标 |

结果块在执行对话里区分：成功桩、失败/手动跳过、未测量、等待占位。缺事件保持等待，**不算 0**。五维对比仍只在报告页。

解锁后的最小 live 步骤见 [`live-smoke-checklist.md`](live-smoke-checklist.md)。
