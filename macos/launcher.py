"""Standard-library bootstrap for the macOS app; no Terminal window required."""
import fcntl, json, os, pathlib, subprocess, sys, time, urllib.request, shutil

resources = pathlib.Path(__file__).resolve().parent
support = pathlib.Path.home() / 'Library/Application Support/ARC Chat'
support.mkdir(parents=True, exist_ok=True, mode=0o700)
state = support / 'session.json'

def dialog(text):
    subprocess.run(['/usr/bin/osascript', '-e', 'on run argv\n display dialog (item 1 of argv) with title "ARC Chat" buttons {"OK"} default button "OK"\nend run', text])

def reopen():
    try:
        info = json.loads(state.read_text())
        url = info['url']
        if not url.startswith('http://127.0.0.1:8765/#'): return False
        with urllib.request.urlopen('http://127.0.0.1:8765/', timeout=2) as response:
            if b'ARC' not in response.read(4096): return False
        subprocess.run(['/usr/bin/open',url], check=True)
        return True
    except Exception: return False

lock = open(support / 'launcher.lock', 'w')
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    for attempt in range(5):
        if reopen(): break
        time.sleep(1)
    else: dialog('The ARC Chat helper is running, but its page is unavailable. Keep any existing chat tab open. See the startup log in Library/Application Support/ARC Chat/launcher.log.')
    sys.exit(0)

with open(support / 'launcher.log', 'a') as log:
    try:
        # Reuse the installed development runtime on this Mac when available.
        config = json.loads((resources / 'runtime.json').read_text())
        installed = pathlib.Path(config['existing_runtime'])
        python = installed / '.venv/bin/python'
        browsers = installed / '.browsers'
        if not python.exists() or not browsers.exists():
            python = support / 'runtime/bin/python'
            browsers = support / 'browsers'
            marker = support / 'setup-complete'
            if not marker.exists():
                dialog('First-time setup will download the Python dependencies and Chromium. This can take several minutes. The chat will open automatically when ready.')
                if not python.exists():
                    subprocess.run([sys.executable,'-m','venv',str(support/'runtime')],check=True,stdout=log,stderr=log)
                subprocess.run([str(python),'-m','pip','install','-r',str(resources/'requirements.txt')],check=True,stdout=log,stderr=log)
                env = os.environ | {'PLAYWRIGHT_BROWSERS_PATH':str(browsers)}
                subprocess.run([str(python),'-m','playwright','install','chromium'],env=env,check=True,stdout=log,stderr=log)
                marker.touch()
        env = os.environ | {'PLAYWRIGHT_BROWSERS_PATH':str(browsers), 'ARC_CHAT_STATE':str(state), 'PYTHONUNBUFFERED':'1'}
        # An older Terminal-launched helper may own this port; do not kill it.
        try:
            urllib.request.urlopen('http://127.0.0.1:8765/',timeout=1).close()
        except Exception: pass
        else:
            dialog('Port 8765 is already in use, possibly by ARC Chat in Terminal. Keep using that chat, or stop its helper with Control-C before opening this app.')
            sys.exit(0)
        state.unlink(missing_ok=True)
        # Keep serving files from a stable path even if the .app is moved.
        app_files=support/'app'
        app_files.mkdir(exist_ok=True)
        for name in ('helper.py','arc-chat.html'):
            shutil.copy2(resources/name,app_files/name)
        result = subprocess.run([str(python),str(app_files/'helper.py')],cwd=support,env=env,stdout=log,stderr=log)
        if result.returncode:
            dialog('ARC Chat could not start or stopped unexpectedly. Open the startup log at '+str(support/'launcher.log'))
    except Exception as exc:
        print(repr(exc),file=log,flush=True)
        dialog('ARC Chat setup failed. Check your internet connection and open the app again. Details: '+str(support/'launcher.log'))
    finally:
        state.unlink(missing_ok=True)
