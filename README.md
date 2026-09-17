# ARC Research Chat

One self-contained HTML interface plus a local Python browser/Jupyter helper. The helper opens a separate Chromium window for VT login/MFA; no VT passwords are entered into the chatbot. VPN must be enabled using your normal client.

## Downloads

[Windows bootstrap](https://raw.githubusercontent.com/FL2744/arc-chat/release-assets-v0.3.0/ARC-Chat-Windows.zip) | [macOS bootstrap](https://raw.githubusercontent.com/FL2744/arc-chat/release-assets-v0.3.0/ARC-Chat-macOS.zip) | [Linux bootstrap](https://raw.githubusercontent.com/FL2744/arc-chat/release-assets-v0.3.0/ARC-Chat-Linux.zip) | [Source archive](https://raw.githubusercontent.com/FL2744/arc-chat/release-assets-v0.3.0/ARC-Chat-source.zip) | [Release notes](https://github.com/FL2744/arc-chat/releases/tag/v0.3.0)

The links above are published through ordinary Git rather than the GitHub Release asset API, which makes the classroom-sized downloads independently recoverable from release-service outages. SHA-256 files are stored beside each download on the `release-assets-v0.3.0` branch.

**Windows:** unzip **ARC-Chat-Windows.zip**, then double-click **ARC Chat.cmd**. Python 3.10+ is required; first launch creates the local runtime and downloads the compatible Chromium browser.

**macOS:** unzip **ARC-Chat-macOS.zip**, move **ARC Chat.app** to Applications, and open it. First launch creates the local runtime and downloads dependencies/Chromium. Preview builds are not Apple-notarized, so macOS may require explicit approval to open them.

**Linux:** run `./arc-chat`; Python 3.10+ and internet access are required for first-time dependency and Chromium setup.

Self-contained Windows/macOS packages are still built and smoke-tested by CI. They are intentionally kept as CI artifacts rather than normal Git objects because the bundled browser makes them larger than GitHub's 100 MB Git object limit.

VT VPN is still required when normal ARC access requires it.

## Start

To build the macOS wrapper, run `python3 macos/build.py`, then double-click **dist/ARC Chat.app**. No Terminal command is needed. Stop any existing Terminal-launched helper first with Control-C. Use **Quit helper** in the chat to stop the background helper. See `macos/README.md` for app setup details.

Requires Python 3.10+ and internet access for first-time dependency installation.

On macOS/Linux, open a terminal in this folder and run:

```sh
./start.command
```

On Windows (or for manual setup):

```sh
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python helper.py
```

The helper opens the HTML interface at a local address with a random access token. Keep the terminal open. No web hosting, Node.js, API SDK, or ARC-side software installation is required. Do not expose port 8765 to the network.

Student Mode uses the selected course profile and keeps infrastructure details out of the normal workflow. Set `ARC_COURSE_ALLOCATION` to the instructor-provided course allocation before using **Start course workspace**. If that variable is not present, switch to Advanced Mode and select an authorized allocation manually in OOD. The repository contains no personal or public default allocation.

## Connect and work

1. Set `ARC_COURSE_ALLOCATION` when using Student Mode, enable VT VPN if needed, and click **Start course workspace**. Complete VT credentials and MFA in the visible Chromium window.
2. Review resources and walltime in OOD, then click **Launch** there or **Launch reviewed job** in Advanced Mode. If site labels differ, fill in the form manually. Provisioning is deliberately user-submitted; the helper does not guess resource settings.
3. Once the job is ready, click **Connect ready session** in the chat (or **Connect to Jupyter** in OOD), then **Attach to Jupyter** in the chat. The helper captures the opened tab and attaches automatically. For a manually opened tab, click Attach automatically; if several distinct servers are open, choose a session button. This creates a new persistent Python kernel and a uniquely named `ARC-chat-….ipynb` notebook in the Jupyter root. Choose an installed kernel name if `python3` is unavailable.
4. Enter your personal ARC API key. Student Mode uses the Virginia Tech ARC endpoint and the course profile's model policy. OpenAI and custom OpenAI-compatible endpoints remain available in Advanced Mode; all custom endpoints must use HTTPS and may not target local/private addresses. Advanced Mode can also use a reviewed ARC vLLM job through a localhost SSH tunnel; its generated API key stays inside the helper.
5. Send a request, review proposed Python, and click **Run Python**. Outputs appear as they arrive. Click **Continue with outputs** to let the model inspect the results and propose the next step. Each proposed execution requires a click; there is no unattended execution loop.
6. Reply to Python `input()` and `getpass()` prompts in the input box. Input answers are omitted from chatbot history, but the Python program itself can print or save them. Never put API keys directly into chat or source cells; use `getpass.getpass()`.
7. Use **Files & results** to upload individual local files and download outputs. Uploaded files receive unique names; use the reported path. Upload complete project folders through Jupyter to preserve filenames and relative imports. Refresh files to see generated `.xlsx` and `.html` outputs. Export dialogue separately if needed.
8. **Shut down chat kernel** when finished. Also stop the OOD job under **My Interactive Sessions** to release its allocation. Closing the HTML does not stop a running cell or the allocation.

## Advanced Slurm and managed vLLM preview

Advanced Mode contains an experimental ARC job/service layer. It is intentionally separate from the normal classroom path and does not run arbitrary compute work on a login node.

- **Slurm jobs:** enter your VT PID and authorized allocation, review the generated `sbatch` script, then explicitly submit it. Status, active-job listing, recent stdout, and cancellation use OpenSSH in `BatchMode=yes` against the documented Falcon login hosts. Password/Duo prompts are never collected by ARC Chat.
- **Managed vLLM:** review a Slurm job for a model already available under ARC's `/common/data/models/` tree, submit it, wait until Slurm reports a running compute node, then explicitly start the SSH tunnel. Model traffic goes only to the resulting loopback endpoint. ARC Chat refuses to use the managed provider until a tracked tunnel process is actually alive.
- **Artifacts:** uploads and generated outputs can be represented as provenance records for future pipeline/workflow features. This registry is metadata only; it does not grant additional filesystem access.

These Advanced controls have synthetic/unit coverage but still require a live ARC acceptance test with an authorized account before they should be considered a supported classroom workflow. Student Mode does not expose these controls.

## Run an existing notebook

Upload your project folder through Jupyter first. Ask the chatbot to inspect `my-project/analysis.ipynb`, report dependencies, and propose executing its cells. For a trusted notebook, you can also run this directly in the Python panel:

```python
import os, json
os.chdir("my-project")  # run once, relative to the kernel's initial directory
with open("analysis.ipynb", encoding="utf-8") as f:
    notebook = json.load(f)
for cell in notebook["cells"]:
    if cell["cell_type"] == "code":
        result = get_ipython().run_cell("".join(cell["source"]))
        if result.error_before_exec or result.error_in_exec:
            break
```

This runs notebook source within the chat kernel and supports interactive prompts. The original notebook is not overwritten. The wrapper cell and outputs are saved in the chat notebook. Project dependencies, source files, and personal API credentials must be supplied separately.

## Boundaries and recovery

- The live VT launch selectors, authenticated OOD proxy, and Advanced SSH/Slurm/vLLM path have not been verified in this environment. Login, allocation selection, queueing, compute-node access, scheduler commands, and managed-model startup require an ARC user acceptance test. Visible/manual fallbacks are retained where applicable.
- Launching the instance is a browser-assisted step, not an unattended provisioning API. The app does not bypass MFA or configure VPN.
- The model API is called from the local helper, not the ARC compute node. Python runs remotely on ARC. VPN/network reachability must permit both services.
- Local keys and browser cookies are held in process memory, without saved browser profiles or localStorage. User-executed Python and its outputs are saved in the remote notebook and IPython history. Programs can disclose secrets in their output; inspect outputs before sending them to a provider.
- Chat history is bounded before each model request with conservative context accounting, and oversized tool text is clipped rather than allowing unbounded prompt growth. Python execution currently returns at most the last 24,000 characters of textual output to chat; full supported outputs remain in the notebook. Image outputs are displayed but are not sent as model vision inputs.
- HTML previews are sandboxed with scripts/network disabled. Download trusted interactive maps to run them locally. Widgets and binary widget protocols are not supported; updated displays are retained as additional snapshots.
- No automatic retry of Python occurs after a dropped connection. The local command protocol uses request IDs and a bounded replay cache so reconnecting the UI can safely resend the same outstanding action without submitting it twice. On helper restart, non-secret recovery metadata can opportunistically reattach the same still-running Jupyter session/notebook; stale recovery data is ignored rather than replaying code. Chat/model history is not reconstructed from disk, so save/export important dialogue before closing.
- Large files should use Jupyter's native file interface. This helper supports individual uploads up to 20 MB in the UI.
- If installation was interrupted, rerun `start.command` (or `start.ps1` on Windows). If Chromium cannot launch, run `.venv/bin/python -m playwright install chromium` on macOS/Linux or `.venv\Scripts\python.exe -m playwright install chromium` on Windows.

## Tests

```sh
.venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
```

Unit/stress tests cover proxy URL derivation, binary framing, parent-message correlation, completion ordering, model/tool roundtrips, endpoint and upload hardening, stale/multiple UI tabs, request replay/idempotency, recovery metadata, context bounding, artifact contracts, Slurm command generation, managed-vLLM lifecycle contracts, and localhost/token/origin enforcement. A passing local integration test also verifies browser-cookie authentication through a Jupyter URL prefix, real kernel execution, persistent variables, interactive stdin, errors, HTML output, valid notebook persistence, uploads, browser UI execution, and shutdown. Portable-package smoke tests verify that the frozen app serves its UI and contains the headful Chromium runtime. Live ARC login/model/job/service requests still require validation by an authorized user.

Sources: [ARC OOD](https://www.docs.arc.vt.edu/resources/ood.html), [ARC model API](https://docs.arc.vt.edu/ai/011_llm_api_arc_vt_edu.html), [Jupyter REST](https://jupyter-server.readthedocs.io/en/latest/developers/rest-api.html), [Jupyter messages](https://jupyter-client.readthedocs.io/en/stable/messaging.html), [Playwright authentication](https://playwright.dev/python/docs/auth), [OpenAI chat API](https://developers.openai.com/api/reference/resources/chat).

Optional integration test (local only): install `jupyter-server ipykernel nbformat` into the virtual environment, then run `.venv/bin/python integration_test.py`. It uses ports 8765 and 8877 and temporary files.

### Course profiles and diagnostics

The built-in `fl2744` profile reads its allocation from `ARC_COURSE_ALLOCATION`. For another course or a managed deployment, point `ARC_CHAT_PROFILE_FILE` at a JSON file with a `profiles` list; each profile may define `id`, `name`, `workspace_backend`, `cluster`, `allocation`, `resource_profile`, `model_provider`, `model_policy`, and `advanced_mode`. Allocation values may reference an environment variable such as `${ARC_COURSE_ALLOCATION}`. Do not put API keys, passwords, browser cookies, or notebook content in profile files. See `profiles.example.json`.

Use **Run diagnostics** to generate an in-app health report. It contains runtime, browser, workspace, and model-configuration status but excludes credentials, cookies, chat content, and notebook contents. Advanced Mode also provides **Run full workspace checks**, which explicitly tests OOD visibility, model reachability, Jupyter reachability, a temporary remote write/delete, and a visible kernel smoke-test cell.

Every push and pull request targeting `main` runs the test matrix on Ubuntu, Windows, and macOS across supported Python versions, plus local-Jupyter integration and packaging checks. Version tags (`v*`) trigger the preview release workflow, which builds self-contained Windows/macOS packages, the lightweight macOS bootstrap, Linux/source archives, smoke-tests the portable apps, and publishes SHA-256 checksums as a prerelease.

### Navigation timeout on opening ARC

The helper now waits only for navigation to begin, rather than for every page resource to load. A timeout preserves the visible browser tab and provides recovery guidance. If the VT login page is visible, complete login and MFA there. If the tab is blank or unreachable, connect VT VPN and reload it; test OOD in your usual browser as well. Clicking Open ARC again brings the existing login tab forward without discarding authentication. After updating helper.py, restart the helper to load the fix.

Connection selection prefers the Notebook interface and falls back to Lab when Notebook is absent. Multiple matching jobs require selecting the desired job in OOD manually. If already running an older helper, click the Notebook connection directly in OOD and then Attach in the chat; no restart is needed for that workaround.

Automatic attachment tracks the tab opened by Connect ready session (including popups and same-tab navigation). Notebook and Lab tabs on the same server are deduplicated. Repeated attachment reuses the existing live kernel. The URL field has been removed from the interface.

The helper continuously reads kernel WebSocket messages between code runs to maintain ping/pong. If the connection closes while idle, new execution reconnects to the same existing kernel without clearing variables or history. Busy kernels are preserved and require waiting or interruption; code is never automatically replayed after a mid-execution disconnect.

Recovery from an OOD 502/503/504: check My Interactive Sessions for a reachable running Jupyter job. Connect ready session can switch from a stale server to the newly opened server without deleting the old kernel or files. Disconnect old session clears only local connection references when needed. Switching to a different server creates a fresh Python workspace; saved files remain on their original filesystem, but in-memory variables are not transferred. A helper restart can reattach a still-running previously recorded session when it is safely discoverable, without executing notebook cells. Updating the files does not hot-reload a running helper: quit the helper and reopen the app to use build 2026.09.17.10.

## Certificate verification on macOS

The helper uses `truststore` to verify HTTPS with the native system trust store (macOS Keychain). Certificate and hostname checks remain enabled. This avoids relying on a missing python.org OpenSSL certificate bundle. Existing app installations refresh dependencies when bundled requirements change. Restart the helper after upgrading; closing a browser tab alone does not restart it. If verification still fails, check your system trust configuration and any institutional VPN/proxy certificates with IT; do not disable TLS verification.
