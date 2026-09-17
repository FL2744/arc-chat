# ARC Research Chat

One self-contained HTML interface plus a local Python browser/Jupyter helper. The helper opens a separate Chromium window for VT login/MFA; no VT passwords are entered into the chatbot. VPN must be enabled using your normal client.

## Downloads

[Windows portable](https://github.com/FL2744/arc-chat/releases/download/v0.2.4/ARC-Chat-Windows-Portable.zip) · [macOS portable](https://github.com/FL2744/arc-chat/releases/download/v0.2.4/ARC-Chat-macOS-Portable.zip) · [macOS lightweight bootstrap](https://github.com/FL2744/arc-chat/releases/download/v0.2.4/ARC-Chat-macOS.zip) · [Linux bootstrap](https://github.com/FL2744/arc-chat/releases/download/v0.2.4/ARC-Chat-Linux.zip) · [Release notes](https://github.com/FL2744/arc-chat/releases/tag/v0.2.4)

**Windows:** unzip the whole portable archive and double-click **ARC-Chat.exe** inside the extracted `ARC-Chat` folder. The portable build bundles Python, ARC Chat's dependencies, and the compatible headful Chromium runtime used for visible VT login/MFA. It does not require a separate Python installation or a first-run browser download.

**macOS:** the portable archive is the self-contained classroom build. Unzip it, move **ARC-Chat.app** to Applications, and open it. The smaller **ARC-Chat-macOS.zip** remains available as a bootstrap fallback, but it requires Python 3.10+ and downloads dependencies/Chromium on first launch. Preview builds are not Apple-notarized, so macOS may require explicit approval to open them.

**Linux:** the current release remains an advanced/bootstrap path. Run `./arc-chat`; Python 3.10+ and internet access are required for first-time dependency and Chromium setup.

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
4. Enter your personal ARC API key. Student Mode uses the Virginia Tech ARC endpoint and the course profile's model policy. OpenAI and custom OpenAI-compatible endpoints remain available in Advanced Mode; all endpoints must use HTTPS and may not target local/private addresses.
5. Send a request, review proposed Python, and click **Run Python**. Outputs appear as they arrive. Click **Continue with outputs** to let the model inspect the results and propose the next step. Each proposed execution requires a click; there is no unattended execution loop.
6. Reply to Python `input()` and `getpass()` prompts in the input box. Input answers are omitted from chatbot history, but the Python program itself can print or save them. Never put API keys directly into chat or source cells; use `getpass.getpass()`.
7. Use **Files & results** to upload individual local files and download outputs. Uploaded files receive unique names; use the reported path. Upload complete project folders through Jupyter to preserve filenames and relative imports. Refresh files to see generated `.xlsx` and `.html` outputs. Export dialogue separately if needed.
8. **Shut down chat kernel** when finished. Also stop the OOD job under **My Interactive Sessions** to release its allocation. Closing the HTML does not stop a running cell or the allocation.

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

- The live VT launch selectors and authenticated OOD proxy have not been verified in this environment. Login, allocation selection, queueing, and compute-node access require an ARC user acceptance test. A visible manual fallback is included.
- Launching the instance is a browser-assisted step, not an unattended provisioning API. The app does not bypass MFA or configure VPN.
- The model API is called from the local helper, not the ARC compute node. Python runs remotely on ARC. VPN/network reachability must permit both services.
- Local keys and browser cookies are held in process memory, without saved browser profiles or localStorage. User-executed Python and its outputs are saved in the remote notebook and IPython history. Programs can disclose secrets in their output; inspect outputs before sending them to a provider.
- Chat and the last 24,000 characters of each cell's textual results enter model context. Full text and supported rich outputs are saved in the notebook. Image outputs are displayed but are not sent as model vision inputs. Long chats may exceed model context limits.
- HTML previews are sandboxed with scripts/network disabled. Download trusted interactive maps to run them locally. Widgets and binary widget protocols are not supported; updated displays are retained as additional snapshots.
- No automatic retry of Python after a dropped connection. Check the kernel in Jupyter before restarting the helper. A restarted helper creates a new kernel; it does not resume old chat state. Save/export work before closing.
- Large files should use Jupyter's native file interface. This helper supports individual uploads up to 20 MB in the UI.
- If installation was interrupted, rerun `start.command` (or `start.ps1` on Windows). If Chromium cannot launch, run `.venv/bin/python -m playwright install chromium` on macOS/Linux or `.venv\Scripts\python.exe -m playwright install chromium` on Windows.

## Tests

```sh
.venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
```

Unit tests cover proxy URL derivation, binary framing, parent-message correlation, completion ordering, model/tool roundtrips with a mock API, tool rejection, and localhost/token/origin enforcement. A passing local integration test also verified browser-cookie authentication through a Jupyter URL prefix, real kernel execution, persistent variables, interactive stdin, errors, HTML output, valid notebook persistence, uploads, browser UI execution, and shutdown. Live ARC login and model requests require validation by an authorized user.

Sources: [ARC OOD](https://www.docs.arc.vt.edu/resources/ood.html), [ARC model API](https://docs.arc.vt.edu/ai/011_llm_api_arc_vt_edu.html), [Jupyter REST](https://jupyter-server.readthedocs.io/en/latest/developers/rest-api.html), [Jupyter messages](https://jupyter-client.readthedocs.io/en/stable/messaging.html), [Playwright authentication](https://playwright.dev/python/docs/auth), [OpenAI chat API](https://developers.openai.com/api/reference/resources/chat).

Optional integration test (local only): install `jupyter-server ipykernel nbformat` into the virtual environment, then run `.venv/bin/python integration_test.py`. It uses ports 8765 and 8877 and temporary files.

### Course profiles and diagnostics

The built-in `fl2744` profile reads its allocation from `ARC_COURSE_ALLOCATION`. For another course or a managed deployment, point `ARC_CHAT_PROFILE_FILE` at a JSON file with a `profiles` list; each profile may define `id`, `name`, `workspace_backend`, `cluster`, `allocation`, `resource_profile`, `model_provider`, `model_policy`, and `advanced_mode`. Allocation values may reference an environment variable such as `${ARC_COURSE_ALLOCATION}`. Do not put API keys, passwords, browser cookies, or notebook content in profile files. See `profiles.example.json`.

Use **Run diagnostics** to generate an in-app health report. It contains runtime, browser, workspace, and model-configuration status but excludes credentials, cookies, chat content, and notebook contents. Advanced Mode also provides **Run full workspace checks**, which explicitly tests OOD visibility, model reachability, Jupyter reachability, a temporary remote write/delete, and a visible kernel smoke-test cell.

Every push and pull request targeting `main` runs the test matrix on Ubuntu, Windows, and macOS. Version tags (`v*`) trigger the preview release workflow, which builds the macOS app archive, source archive, and SHA-256 checksums as a prerelease.

### Navigation timeout on opening ARC

The helper now waits only for navigation to begin, rather than for every page resource to load. A timeout preserves the visible browser tab and provides recovery guidance. If the VT login page is visible, complete login and MFA there. If the tab is blank or unreachable, connect VT VPN and reload it; test OOD in your usual browser as well. Clicking Open ARC again brings the existing login tab forward without discarding authentication. After updating helper.py, restart the helper to load the fix.

Connection selection prefers the Notebook interface and falls back to Lab when Notebook is absent. Multiple matching jobs require selecting the desired job in OOD manually. If already running an older helper, click the Notebook connection directly in OOD and then Attach in the chat; no restart is needed for that workaround.

Automatic attachment tracks the tab opened by Connect ready session (including popups and same-tab navigation). Notebook and Lab tabs on the same server are deduplicated. Repeated attachment reuses the existing live kernel. The URL field has been removed from the interface.

The helper continuously reads kernel WebSocket messages between code runs to maintain ping/pong. If the connection closes while idle, new execution reconnects to the same existing kernel without clearing variables or history. Busy kernels are preserved and require waiting or interruption; code is never automatically replayed after a mid-execution disconnect.

Recovery from an OOD 502/503/504: check My Interactive Sessions for a reachable running Jupyter job. Connect ready session can switch from a stale server to the newly opened server without deleting the old kernel or files. Disconnect old session clears only local connection references when needed. Switching servers creates a fresh Python workspace; saved files remain on their original filesystem, but in-memory variables are not transferred. Updating the files does not hot-reload a running helper: quit the helper and reopen the app to use build 2026.09.17.9.

## Certificate verification on macOS

The helper uses `truststore` to verify HTTPS with the native system trust store (macOS Keychain). Certificate and hostname checks remain enabled. This avoids relying on a missing python.org OpenSSL certificate bundle. Existing app installations refresh dependencies when bundled requirements change. Restart the helper after upgrading; closing a browser tab alone does not restart it. If verification still fails, check your system trust configuration and any institutional VPN/proxy certificates with IT; do not disable TLS verification.
