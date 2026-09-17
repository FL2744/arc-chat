import json, ssl, struct, unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from aiohttp import WSMsgType, web
from helper import BrowserTimeout, BrowserError, Bridge, base_url, decode_packet, message, guard, TOKEN, tls_context
from config import get_profile

class ProtocolTests(unittest.TestCase):
    def test_tls_requires_valid_certificate_and_hostname(self):
        context = tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_proxy_base(self):
        self.assertEqual(base_url('https://ood.arc.vt.edu/node/gpu/123/tree/a?token=x'),'https://ood.arc.vt.edu/node/gpu/123/')
        self.assertEqual(base_url('http://127.0.0.1:8888/lab'),'http://127.0.0.1:8888/')
        with self.assertRaises(ValueError): base_url('http://remote.example/lab')
    def test_binary(self):
        raw=json.dumps({'content':{'text':'hello'}}).encode()
        self.assertEqual(decode_packet(struct.pack('!II',1,8)+raw)['content']['text'],'hello')
        self.assertEqual(decode_packet(struct.pack('!III',2,12,12+len(raw))+raw+b'buffer')['content']['text'],'hello')

class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_detach_does_not_delete_unreachable_kernel(self):
        b=Bridge();b.kernel='old';b.session='old-session';b.channel=AsyncMock();b.api=AsyncMock()
        await b.detach()
        self.assertIsNone(b.kernel);self.assertIsNone(b.channel)
        b.api.assert_not_awaited()

    async def test_attach_can_switch_server_without_old_server_access(self):
        b=Bridge();b.kernel='old';b.base='https://ood.arc.vt.edu/node/old/123/'
        b.channel=AsyncMock();b.context=object();b.save=AsyncMock();b.open_channel=AsyncMock()
        b.api=AsyncMock(side_effect=[{'kernelspecs':{'python3':{}}},{'id':'new-session','kernel':{'id':'new-kernel'}}])
        await b.attach('https://ood.arc.vt.edu/node/new/456/tree','python3')
        self.assertEqual(b.kernel,'new-kernel')
        self.assertEqual(b.base,'https://ood.arc.vt.edu/node/new/456/')
        self.assertFalse(any(c.args[0]=='DELETE' for c in b.api.call_args_list))

    async def test_reconnect_preserves_kernel_and_history(self):
        b=Bridge(); b.kernel='keep-me'; b.history=[{'role':'user','content':'prior'}]
        b.api=AsyncMock(return_value={'execution_state':'idle'}); b.open_channel=AsyncMock()
        await b.reconnect()
        b.api.assert_awaited_once_with('GET','api/kernels/keep-me')
        b.open_channel.assert_awaited_once()
        self.assertEqual(b.kernel,'keep-me'); self.assertEqual(len(b.history),1)
        b.api=AsyncMock(return_value={'execution_state':'busy'}); b.open_channel.reset_mock()
        with self.assertRaisesRegex(ValueError,'No shutdown'): await b.reconnect()
        b.open_channel.assert_not_awaited()

    async def test_reader_drains_idle_messages_and_forwards_execution(self):
        b=Bridge()
        class Channel:
            def __aiter__(self): return self.events()
            async def events(self):
                yield 'idle message'
                b.executing=True
                yield 'execution message'
        b.channel=Channel()
        await b.read_channel()
        self.assertEqual(await b.events.get(),'execution message')
        self.assertIsNone(await b.events.get())
        self.assertTrue(b.events.empty())

    async def test_connection_interface_selection(self):
        class Locator:
            def __init__(self,items): self.items=items
            def or_(self,other): return Locator(self.items+other.items)
            async def count(self): return len(self.items)
            async def click(self): self.items[0]['clicked']=True
        class Page:
            url='https://ood.arc.vt.edu/pun/sys/dashboard/batch_connect/sessions'
            def __init__(self,labels): self.items=[dict(label=x,clicked=False) for x in labels]
            def get_by_role(self,role,name):
                return Locator([i for i in self.items if role=='link' and name.search(i['label'])])
        notebook='Connect to Jupyter (Notebook interface)'
        lab='Connect to Jupyter (Lab interface)'
        for labels,expected in [([notebook,lab],notebook),([lab],lab),(['Connect to Jupyter'],'Connect to Jupyter')]:
            b=Bridge(); b.capture_jupyter=AsyncMock(); page=Page(labels); b.context=SimpleNamespace(pages=[page])
            await b.browser_click('connect')
            self.assertEqual([i['label'] for i in page.items if i['clicked']],[expected])
        for labels in [[notebook,lab,notebook,lab],[]]:
            b=Bridge(); b.capture_jupyter=AsyncMock(); page=Page(labels); b.context=SimpleNamespace(pages=[page])
            with self.assertRaises(ValueError): await b.browser_click('connect')
            self.assertFalse(any(i['clicked'] for i in page.items))

    async def test_automatic_tab_discovery(self):
        a=SimpleNamespace(url='https://ood.arc.vt.edu/node/a/123/tree', title=AsyncMock(return_value='A'))
        same=SimpleNamespace(url='https://ood.arc.vt.edu/node/a/123/lab', title=AsyncMock(return_value='A lab'))
        other=SimpleNamespace(url='https://ood.arc.vt.edu/node/b/456/tree', title=AsyncMock(return_value='B'))
        b=Bridge(); b.context=SimpleNamespace(pages=[a,same]); b.emit=AsyncMock()
        self.assertEqual(base_url(await b.discover_jupyter()),base_url(a.url))
        b.context.pages.append(other); b.jupyter_page=other
        self.assertEqual(await b.discover_jupyter(),other.url)
        b.jupyter_page=None
        with self.assertRaisesRegex(ValueError,'Choose'): await b.discover_jupyter()
        self.assertEqual(len(b.emit.call_args.kwargs['items']),2)

    async def test_captures_new_popup_and_same_tab_navigation(self):
        old=SimpleNamespace(url='https://ood.arc.vt.edu/pun/sys/dashboard')
        new=SimpleNamespace(url='https://ood.arc.vt.edu/node/a/123/tree')
        b=Bridge(); b.context=SimpleNamespace(pages=[old,new])
        await b.capture_jupyter([(old,old.url)])
        self.assertIs(b.jupyter_page,new)
        before=[(old,old.url)]; old.url=new.url; b.context.pages=[old]
        await b.capture_jupyter(before)
        self.assertIs(b.jupyter_page,old)

    async def test_connect_attaches_and_repeat_attach_reuses_kernel(self):
        b=Bridge(); b.browser_click=AsyncMock(); b.discover_jupyter=AsyncMock(return_value='https://ood.arc.vt.edu/node/new/123/tree'); b.attach=AsyncMock(return_value='Connected')
        self.assertEqual(await b.dispatch('connect',{'kernel':'python3'}),'Connected')
        b.attach.assert_awaited_once_with('https://ood.arc.vt.edu/node/new/123/tree','python3')
        b=Bridge(); b.kernel='existing'; b.channel=SimpleNamespace(closed=False)
        self.assertIn('Already connected',await b.attach('','python3'))

    async def test_navigation_does_not_wait_for_full_load(self):
        b=Bridge(); page=SimpleNamespace(goto=AsyncMock(return_value=None))
        b.context=SimpleNamespace(new_page=AsyncMock(return_value=page))
        await b.browser_open()
        page.goto.assert_awaited_once_with('https://ood.arc.vt.edu/',wait_until='commit',timeout=30000)

    async def test_navigation_timeout_preserves_tab_and_guides_user(self):
        b=Bridge(); page=SimpleNamespace(goto=AsyncMock(side_effect=BrowserTimeout('timeout')))
        b.context=SimpleNamespace(new_page=AsyncMock(return_value=page))
        result=await b.browser_open()
        self.assertIn('VPN',result); self.assertIn('login/MFA',result)
        self.assertIs(b.ood_page,page)

    async def test_open_reuses_authentication_tab(self):
        b=Bridge(); b.context=SimpleNamespace(new_page=AsyncMock())
        b.ood_page=SimpleNamespace(is_closed=Mock(return_value=False),url='https://login.vt.edu/',bring_to_front=AsyncMock())
        result=await b.browser_open()
        b.context.new_page.assert_not_awaited()
        self.assertIn('Existing',result)

    async def test_network_error_includes_actionable_guidance(self):
        b=Bridge(); page=SimpleNamespace(goto=AsyncMock(side_effect=BrowserError('net::ERR_NAME_NOT_RESOLVED')))
        b.context=SimpleNamespace(new_page=AsyncMock(return_value=page))
        with self.assertRaisesRegex(RuntimeError,'VPN/network'):
            await b.browser_open()

    async def test_execute_correlates_and_waits_for_idle_and_reply(self):
        b=Bridge(); b.save=AsyncMock(); b.emit=AsyncMock()
        class Channel:
            closed=False
            async def send_json(self,request):
                self.events=[]
                for kind,content,parent in [
                    ('stream',{'name':'stdout','text':'unrelated'},'other'),
                    ('stream',{'name':'stdout','text':'ok'},request['header']['msg_id']),
                    ('status',{'execution_state':'idle'},request['header']['msg_id']),
                    ('execute_reply',{'status':'ok','execution_count':1},request['header']['msg_id'])]:
                    e=message(kind,content,parent={'msg_id':parent})
                    self.events.append(SimpleNamespace(type=WSMsgType.TEXT,data=json.dumps(e)))
            async def receive(self): return self.events.pop(0)
        b.channel=Channel()
        self.assertEqual(await b.execute('print("ok")'),'ok')
        self.assertEqual(b.cells[0]['execution_count'],1)
        self.assertEqual(len(b.cells[0]['outputs']),1)
        self.assertEqual(b.save.await_count,2)
    async def test_tool_rejection_and_edit_guard(self):
        b=Bridge(); b.pending={'id':'abc','code':'print(1)'}
        with self.assertRaises(ValueError): await b.dispatch('run',{'code':'print(2)'})
        await b.dispatch('reject',{})
        self.assertIsNone(b.pending)
        self.assertEqual(b.history[-1]['tool_call_id'],'abc')
    async def test_model_tool_roundtrip(self):
        b=Bridge(); b.kernel='test-kernel'
        b.profile=get_profile('default')
        class Response:
            status=200
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def json(self): return {'choices':[{'message':{'role':'assistant','content':'Compute.', 'tool_calls':[{'id':'call1','type':'function','function':{'name':'run_python','arguments':'{"code":"print(2)"}'}}]}}]}
        class HTTP:
            def post(self,url,**kwargs):
                self.body=kwargs['json']
                assert url=='https://example.org/v1/chat/completions'
                return Response()
        b.http=HTTP()
        await b.chat({'endpoint':'https://example.org/v1','key':'test-only','model':'mock','text':'Calculate'})
        self.assertEqual(b.pending['code'],'print(2)')
        b.execute=AsyncMock(return_value='2')
        await b.dispatch('run',{'code':'print(2)'})
        self.assertEqual(b.history[-1],{'role':'tool','tool_call_id':'call1','content':'2'})
        self.assertIsNone(b.pending)

    async def test_local_auth(self):
        handler=AsyncMock(return_value=web.Response())
        good=SimpleNamespace(host='127.0.0.1:8765',headers={'Origin':'http://127.0.0.1:8765'},path='/ws',query={'token':TOKEN})
        await guard(good,handler)
        for change in [{'host':'evil.example:8765'},{'headers':{'Origin':'https://evil.example'}},{'query':{'token':'wrong'}}]:
            bad=SimpleNamespace(**(vars(good)|change))
            with self.assertRaises(web.HTTPForbidden): await guard(bad,handler)

if __name__=='__main__': unittest.main()
