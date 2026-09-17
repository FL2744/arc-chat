"""Local socket test: python -m unittest test_reopening -v."""
import unittest
from unittest.mock import AsyncMock
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
import helper

class ReopeningTest(unittest.IsolatedAsyncioTestCase):
    async def test_two_tabs_share_session_and_pending_prompt(self):
        original=helper.bridge
        b=helper.bridge=helper.Bridge()
        b.kernel='preserved-kernel'; b.channel=AsyncMock()
        b.input_header={'msg_id':'prompt'};b.input_content={'prompt':'Name?','password':False}
        app=web.Application();app.router.add_get('/ws',helper.socket)
        try:
            async with TestServer(app) as server, ClientSession() as client:
                first=await client.ws_connect(server.make_url('/ws'))
                second=await client.ws_connect(server.make_url('/ws'))
                for ws in (first,second):
                    self.assertEqual((await ws.receive_json())['type'],'status')
                    self.assertEqual((await ws.receive_json())['type'],'busy')
                    self.assertEqual((await ws.receive_json())['prompt'],'Name?')
                self.assertEqual(len(b.clients),2)
                await second.send_json({'action':'input','value':'Ada'})
                for ws in (first,second): self.assertEqual((await ws.receive_json())['type'],'input_done')
                b.channel.send_json.assert_awaited_once()
                self.assertEqual(b.kernel,'preserved-kernel')
                await first.close();await second.close()
        finally: helper.bridge=original

if __name__=='__main__': unittest.main()
