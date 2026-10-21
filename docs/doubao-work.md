# 豆包工作 adapter

豆包工作 is a desktop client. The same product is also reached from the
Doubao app and from Feishu. Login is a phone number or a Feishu account.
OHBE does not install the client and does not drive the GUI.

`DoubaoWorkAdapter.run` implements the `HarnessAdapter` method and then
fails closed. It does not start a process, open a socket, or place a model
call. It is not a peer of OpenBot, Inspect `react()`, DeepSeek Harness,
OpenClaw, or WorkBuddy. `HARNESS_ORDER` stays `openbot`, then
`inspect_react`.

## Why this is not runnable

No headless command is documented that takes one task and sends the model
call only to an OpenAI-compatible gateway. The published path is the desktop
app. A guessed CLI, a browser automation, or the Doubao chat installer would
not be this product and would not be LiteLLM-only.

## Pin

Checked on 2026-10-11. `microsoft/winget-pkgs` lists `ByteDance.DoubaoWork`
at `2.25.17`, `2.31.8`, and `2.31.10`. `2.31.10` is the newest. The manifest
names the publisher CDN. Both URLs returned 200 to a `HEAD` request. They
are catalog pins. A Windows click downloads the matching file and does
not launch it. Other systems return `os_unsupported` and download nothing.

| Field | Value |
| --- | --- |
| catalog version | `2.31.10` |
| Windows x64 | `https://lf-flow-web-cdn.doubao.com/obj/flow-doubao/doubao_pc/2.31.10/DoubaoWork_installer_2.31.10.exe` |
| Windows arm64 | `https://lf-flow-web-cdn.doubao.com/obj/flow-doubao/doubao_pc/2.31.10/DoubaoWork_Arm64_installer_2.31.10.exe` |
| macOS | no versioned artifact URL was published on `doubao.com/work` or in that manifest |
| adapter | closed, `no-litellm-only-headless` |

Earlier catalog rows had no pinable URL. That was true then. The Windows
pins above replace the unpublished placeholder. A third-party mirror is
still not a pin. macOS stays unpinned rather than inventing a DMG URL.

`latest` is not a harness version. On Windows a click without
`DOUBAO_WORK_VERSION` and `DOUBAO_WORK_BIN` downloads the pinned installer,
does not launch it, and admits the closed profile (`desktop_registered`,
`runnable: false`). Other systems return `os_unsupported`. When the version
and bin already match, the profile is admitted and still says
`runnable: false`. The binary is not executed. The profile YAML does not
store a URL, a key, or the installer address. `live_run` stays `rejected`.
