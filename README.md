# ARC Research Chat

One self-contained HTML interface plus a local Python browser/Jupyter helper. The helper opens a separate Chromium window for VT login/MFA; no VT passwords are entered into the chatbot. VPN must be enabled using your normal client.

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

## Connect and work

1. Enable VT VPN if needed. Click **Open ARC / sign in**, and complete VT credentials and MFA in the Chromium window.
2. Click **Prepare Jupyter**. The helper attempts to open Jupyter and select Falcon and the supplied account (`will_taggart_mcll` is prefilled; replace it with your authorized allocation). Review resources and walltime and click **Launch reviewed job** in the chat (or **Launch** in OOD). If site labels differ, fill in the form manually. Provisioning is deliberately user-submitted; the helper does not guess resource settings.
3. Once the job is ready, click **Connect ready session** in the chat (or **Connect to Jupyter** in OOD), then **Attach to Jupyter** in the chat. The helper captures the opened tab and attaches automatically. For a manually opened tab, click Attach automatically; if several distinct servers are open, choose a session button. This creates a new persistent Python kernel and a uniquely named `ARC-chat-….ipynb` notebook in the Jupyter root. Choose an installed kernel name if `python3` is unavailable.
4. Enter your personal ARC or OpenAI API key and a model ID that supports function calling. ARC defaults to `https://llm-api.arc.vt.edu/api/v1` and `gpt-oss-120b`. OpenAI uses `https://api.openai.com/v1`; enter a model available to your account.
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
- If installation was interrupted, rerun `start.command`. If Chromium cannot launch, run `.venv/bin/python -m playwright install chromium`.

## Tests

```sh
.venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
```

Unit tests cover proxy URL derivation, binary framing, parent-message correlation, completion ordering, model/tool roundtrips with a mock API, tool rejection, and localhost/token/origin enforcement. A passing local integration test also verified browser-cookie authentication through a Jupyter URL prefix, real kernel execution, persistent variables, interactive stdin, errors, HTML output, valid notebook persistence, uploads, browser UI execution, and shutdown. Live ARC login and model requests require validation by an authorized user.

Sources: [ARC OOD](https://www.docs.arc.vt.edu/resources/ood.html), [ARC model API](https://docs.arc.vt.edu/ai/011_llm_api_arc_vt_edu.html), [Jupyter REST](https://jupyter-server.readthedocs.io/en/latest/developers/rest-api.html), [Jupyter messages](https://jupyter-client.readthedocs.io/en/stable/messaging.html), [Playwright authentication](https://playwright.dev/python/docs/auth), [OpenAI chat API](https://developers.openai.com/api/reference/resources/chat).

Optional integration test (local only): install `jupyter-server ipykernel nbformat` into the virtual environment, then run `.venv/bin/python integration_test.py`. It uses ports 8765 and 8877 and temporary files.

### Navigation timeout on opening ARC

The helper now waits only for navigation to begin, rather than for every page resource to load. A timeout preserves the visible browser tab and provides recovery guidance. If the VT login page is visible, complete login and MFA there. If the tab is blank or unreachable, connect VT VPN and reload it; test OOD in your usual browser as well. Clicking Open ARC again brings the existing login tab forward without discarding authentication. After updating helper.py, restart the helper to load the fix.

Connection selection prefers the Notebook interface and falls back to Lab when Notebook is absent. Multiple matching jobs require selecting the desired job in OOD manually. If already running an older helper, click the Notebook connection directly in OOD and then Attach in the chat; no restart is needed for that workaround.

Automatic attachment tracks the tab opened by Connect ready session (including popups and same-tab navigation). Notebook and Lab tabs on the same server are deduplicated. Repeated attachment reuses the existing live kernel. The URL field has been removed from the interface.

The helper continuously reads kernel WebSocket messages between code runs to maintain ping/pong. If the connection closes while idle, new execution reconnects to the same existing kernel without clearing variables or history. Busy kernels are preserved and require waiting or interruption; code is never automatically replayed after a mid-execution disconnect.

Recovery from an OOD 502/503/504: check My Interactive Sessions for a reachable running Jupyter job. Connect ready session can switch from a stale server to the newly opened server without deleting the old kernel or files. Disconnect old session clears only local connection references when needed. Switching servers creates a fresh Python workspace; saved files remain on their original filesystem, but in-memory variables are not transferred. Updating the files does not hot-reload a running helper: quit the helper and reopen the app to use build 2026.09.17.3.
