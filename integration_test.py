"""Optional local end-to-end test; requires jupyter-server, ipykernel, nbformat."""
import asyncio, os, subprocess, sys, tempfile
from pathlib import Path
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(Path(__file__).parent/'.browsers'))
from aiohttp import ClientSession, web
from playwright.async_api import async_playwright
import nbformat
import helper

async def main():
    # Windows can keep Jupyter/IPython log/SQLite handles alive for a fraction
    # of a second after the server/kernel exits. That must not turn a completed
    # integration run into a false negative during temporary-directory cleanup.
    with tempfile.TemporaryDirectory(prefix='arc-test-', ignore_cleanup_errors=(os.name=='nt')) as temp:
        env=os.environ|{'JUPYTER_RUNTIME_DIR':temp,'IPYTHONDIR':temp+'/ipython','JUPYTER_CONFIG_DIR':temp+'/config'}
        log=open(temp+'/server.log','w')
        proc=subprocess.Popen([sys.executable,'-m','jupyter_server','--no-browser','--ServerApp.port=8877',
            '--ServerApp.port_retries=0','--ServerApp.ip=127.0.0.1','--ServerApp.base_url=/test-proxy/',
            '--IdentityProvider.token=integration-test','--ServerApp.root_dir='+temp],env=env,stdout=log,stderr=log)
        b=helper.bridge
        try:
            async with ClientSession() as http:
                b.http=http
                for i in range(80):
                    try:
                        async with http.get('http://127.0.0.1:8877/test-proxy/api?token=integration-test') as r:
                            if r.status==200: break
                    except Exception: pass
                    await asyncio.sleep(.25)
                else: raise RuntimeError(Path(temp+'/server.log').read_text())
                async with async_playwright() as pw:
                    browser=await pw.chromium.launch(headless=True)
                    b.context=await browser.new_context()
                    page=await b.context.new_page()
                    await page.goto('http://127.0.0.1:8877/test-proxy/login?token=integration-test')
                    await b.attach('http://127.0.0.1:8877/test-proxy/tree','python3')
                    assert await b.execute('x = 20\nprint(x+2)')=='22\n'
                    assert await b.execute('print(x*2)')=='40\n'
                    original_emit=b.emit
                    async def emit(event_type,**d):
                        if event_type=='input':
                            await b.channel.send_json(helper.message('input_reply',{'value':'Ada'},'stdin',b.input_header))
                        await original_emit(event_type,**d)
                    b.emit=emit
                    assert await b.execute('name=input("Name? "); print("Hello",name)')=='Hello Ada\n'
                    assert 'division by zero' in await b.execute('1/0')
                    await b.execute('from IPython.display import display, HTML\ndisplay(HTML("<b>table</b>"))')
                    nb=await b.api('GET','api/contents/'+b.notebook_path)
                    nbformat.validate(nbformat.from_dict(nb['content']))
                    assert len(nb['content']['cells'])==5
                    await b.dispatch('upload',{'name':'example.txt','content':'aGVsbG8='})
                    # Actual UI -> helper WebSocket -> Jupyter execution.
                    app=web.Application(middlewares=[helper.guard]);app.router.add_get('/',helper.index);app.router.add_get('/ws',helper.socket)
                    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',8765).start()
                    ui=await b.context.new_page();errors=[];ui.on('pageerror',lambda e:errors.append(str(e)))
                    await ui.goto('http://127.0.0.1:8765/#'+helper.TOKEN)
                    await ui.wait_for_function("document.querySelector('#status').textContent.includes('Helper connected')")
                    await ui.locator('#code').fill('print("UI execution passed")')
                    await ui.locator('#run').click()
                    await ui.wait_for_function("document.querySelector('#status').textContent.includes('Finished and saved')")
                    assert any(t.startswith('Python') and 'UI execution passed' in t for t in await ui.locator('.bubble').all_text_contents())
                    assert not errors,errors
                    await ui.screenshot(path=str(Path(__file__).parent/'preview.png'),full_page=True)
                    await ui.close();await runner.cleanup()
                    await b.dispatch('shutdown',{})
                    await browser.close()
                    print('PASS: proxy authentication, kernel creation, persistent state, stdin, errors, HTML, notebook validation, upload, browser UI execution, shutdown.')
        finally:
            proc.terminate()
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired: proc.kill();proc.wait()
            log.close()

asyncio.run(main())
