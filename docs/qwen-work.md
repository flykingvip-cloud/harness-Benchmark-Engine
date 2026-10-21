# 千问办公 adapter

千问办公 `1.2.5` is a desktop app. The public surface is that GUI, plus the
web app. OHBE does not install it and does not drive it.

`QwenWorkAdapter.run` implements the `HarnessAdapter` method and then fails
closed. It does not start a process, open a socket, or place a model call.
It is not a peer of OpenBot, Inspect `react()`, DeepSeek Harness, OpenClaw,
or WorkBuddy. `HARNESS_ORDER` stays `openbot`, then `inspect_react`.

## Why this is not runnable

Checked on 2026-10-11:

- Desktop notes on `docs.qwenwork.cn` still name `1.2.5` as the newest
  heading, dated 2026-10-09.
- The download page publishes only
  `https://assets.qwenwork.cn/release/latest/` plus an unversioned filename
  such as `qwenworkcn-darwin-arm64.dmg`. That URL returns 200. It is a
  floating latest, so it is not stored.
- `https://assets.qwenwork.cn/release/1.2.5/` and `/release/v1.2.5/` return
  404 for the same filenames.
- HarmonyOS AppGallery shows a different build string (`1.0.5-2`). That is
  not the desktop pin.

A third-party integration says some installers contain `qoderclicn` and that
the binary accepts `--print`. Official Qoder CN docs for that CLI require
`QODERCN_PERSONAL_ACCESS_TOKEN` and add custom models through the interactive
`/model` wizard. Those docs say not to write BYOK into `settings.json`. The
provider list comes from the signed-in account. That is not a file this
adapter can point only at the OHBE LiteLLM gateway. Launching the CLI would
not be the same fairness contract as OpenBot, DeepSeek Harness, OpenClaw, or
the WorkBuddy bundled print path.

## Pin

| Field | Value |
| --- | --- |
| desktop notes | `1.2.5` |
| catalog version | `1.2.5` |
| artifact URL | not stored |
| adapter | closed, `no-litellm-only-headless` |

`latest` is not a harness version. A library click without
`QWEN_WORK_VERSION=1.2.5` and an executable `QWEN_WORK_BIN` returns
`installer_unpinned` and does not download `/release/latest/`. When both
match, the profile is admitted and still
says `runnable: false`. The binary is not executed. The profile YAML does
not store a URL or a key. `live_run` stays `rejected`.
