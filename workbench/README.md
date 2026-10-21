# OHBE workbench

Independent React + Vite client for the OHBE HTTP surface. The engine remains
the Python package `ohbe`. This app only calls `/v1`.

The header toggle **中文 | English** switches every visible label. See For testers below.

```bash
pip install -e ".[api]"
uvicorn ohbe.api.app:app --host 127.0.0.1 --port 8000
```

`python -m ohbe.api` starts that same app. `GET /health` is the liveness probe.

In another shell:

```bash
cd workbench
npm install
npm run dev
```

Vite serves `http://127.0.0.1:5173` and proxies `/v1` to
`http://127.0.0.1:8000`. Set `VITE_OHBE_API_BASE` when the API is not behind
that proxy.

The tester path is 总览 / Overview, then 准备 / Prepare, then 评测/方案 /
Evaluation. Prepare holds 模型池 / Model Pool and Harness 库 / Harness
Library. Evaluation is one page: the fixture catalog, comparison slots, and
the observation shell (a pane directory, a parameter record, and a process
log for the selected harness or model). It watches one experiment. It is
not a session manager. See `docs/observation-shell.md`. `/experiments` opens that page. The post-run report
(报告 / Report) is linked from the end of a round, not from the main nav.
That page lists the fixture pilot and, when `results/live/<id>/report.json`
is already on disk, the live report. Opening it does not spend.
联调 / Doctor is a summary on Overview; the full checklist stays at
`/doctor`. 模型池 / Model Pool lists admitted models and starts
empty. Its only add actions are 添加本地 / Add local and 添加云端 / Add cloud.
扫描 / Scan sits inside Add local and only discovers models that are already
deployed. Ollama uses `ollama list`. A vLLM origin from the form or
`OHBE_VLLM_BASE_URL` is read with `GET /v1/models`. The bearer is not stored
and the scan does not unlock live spend. Harness 库 / Harness Library lists registered installs and starts
empty. Its actions are 预装 and 扫描. Install pins a concrete package version and does not store `latest`. Remote install needs a host and an SSH key or password; those secrets are not written to YAML. After auth, remote inventory is not executed yet. OpenClaw has an opt-in embedded-agent adapter and stays out of live order. That path is not the OpenClaw Gateway and not a DeepSeek Harness bridge. 千问办公 and 豆包工作 are closed: the preinstall card says they are not runnable, and a saved profile stays `runnable: false`. Live order stays
`HARNESS_ORDER`. Dry-run validation does not open a socket. Round start is
also a dry-run: it does not spend. `cross_model` on that page compares
models under one fixed harness for adaptation decisions. It is not a model
leaderboard. A cloud LiteLLM URL is refused. See `docs/sdk-surface.md` for
the route-to-SDK map and `docs/evaluation-framework.md` for the two modes.

## Private host

On the private OpenBot server, from the repository root:

```bash
make workbench-up
```

That builds this UI into nginx and starts `uvicorn ohbe.api.app:app` beside
it. Open `http://127.0.0.1:8080`. The API listens on `127.0.0.1:8765` for
nginx on the same machine. Both bind loopback. Live spend stays locked:
the compose file sets `OHBE_ALLOW_LIVE_SPEND` to an empty string and does
not pass API keys. `make workbench-down` stops the stack.
On macOS or Windows Docker Desktop, host networking does not reach the Mac
loopback. Use `make workbench-desktop-up`
(`docker/workbench/compose.desktop.yaml`) and
`make workbench-desktop-down`. That bridge still publishes only
`127.0.0.1:8080` and `127.0.0.1:8765`, and live spend stays locked.
`make doctor` is the offline preflight and does not talk to this UI.

Copy-paste steps, in Chinese, are in `docs/private-deploy-checklist.md`
section 5.2.

## For testers

The header control **中文 | English** switches navigation, buttons, status text, empty states, and errors. The choice is stored in `localStorage` under `ohbe.workbench.locale`.

- With no saved choice, a browser language starting with `zh` opens in Chinese. Any other language opens in English.
- Choosing either label saves that choice and overrides the browser language until you change it again.

Requests and responses stay English. Experiment ids, pool ids, hashes, error codes, and server `message` strings are not translated. Switching language does not call the API and does not enable live spend.

```bash
npm test
npm run build
```
