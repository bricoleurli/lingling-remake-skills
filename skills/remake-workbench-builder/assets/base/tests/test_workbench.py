import base64,copy,hashlib,io,json,sys,tempfile,threading,time,unittest,urllib.error,urllib.request,zipfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine
from engine import Store,Problem,save,read,expand,prompt,validate_cards
from manage import add
from server import Server
FIX=Path(__file__).parent/'fixtures'
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
class Provider(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        body=self.rfile.read(int(self.headers['Content-Length']));s=self.server;s.calls.append((self.path,body))
        if self.path.endswith('/chat/completions'):
            data=json.loads(body);p=data['messages'][0]['content'];isplan='只返回合法JSON数组' in p
            value=read(FIX/('pages.json' if isplan else 'copy.json'))
            if s.invalid:value={'bad':True}
            result={'choices':[{'message':{'content':json.dumps(value,ensure_ascii=False)}}]}
        elif self.path.endswith('/images/edits'):
            if s.fail_image==sum(path.endswith('/images/edits') for path,_ in s.calls):self.send_response(503);self.end_headers();return
            result={'data':[{'b64_json':base64.b64encode(PNG).decode()}]}
        else:self.send_response(404);self.end_headers();return
        b=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
class WorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir();self.app=self.root/'app';self.app.mkdir()
        self.flow=read(FIX/'flow.json');save(self.source/'flow.json',self.flow);(self.source/'presets').mkdir();(self.source/'presets/character.png').write_bytes(PNG)
        self.key=add(self.app,self.source);self.store=Store(self.root/'data',self.app)
        self.provider=ThreadingHTTPServer(('127.0.0.1',0),Provider);self.provider.calls=[];self.provider.fail_image=None;self.provider.invalid=False
        threading.Thread(target=self.provider.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{self.provider.server_port}/v1'
        self.store.set_settings({'text':{'base_url':url,'model':'fake-text','api_key':'secret-test-key'},'image':{'base_url':url,'model':'fake-image','api_key':'secret-test-key','size':'768x1024'}})
    def tearDown(self):self.provider.shutdown();self.provider.server_close();self.temp.cleanup()
    def wait(self,jid):
        until=time.monotonic()+5
        while time.monotonic()<until:
            j=self.store.job(jid)
            if j['status'] not in engine.BUSY:return j
            time.sleep(.01)
        self.fail('job timeout')
    def plan(self):return self.wait(self.store.start(self.key,{'topic':'男生烧钱的5大行为'})['id'])
    def complete(self):
        j=self.plan();self.store.action(j['id'],'confirm');return self.wait(j['id'])
    def test_confirm_boundary_and_reference_transport(self):
        j=self.plan();self.assertEqual(j['status'],'awaiting_confirmation');self.assertEqual(len(j['pages']),7);self.assertEqual(len(self.provider.calls),2)
        self.store.action(j['id'],'confirm');j=self.wait(j['id']);self.assertEqual(j['status'],'completed');self.assertEqual(len(self.provider.calls),9)
        payloads=[b for path,b in self.provider.calls if path.endswith('/images/edits')]
        self.assertTrue(all(PNG in b and b'name="image[]"' in b and '【图1】'.encode() in b for b in payloads))
        actual=(self.store.jobpath(j['id'])/'evidence/image-01.txt').read_text();expected=expand(prompt(self.flow['steps'][2]).replace('{{img:preset_1}}','【图1】'),{'item':j['pages'][0]['content']});self.assertEqual(actual,expected)
    def test_fail_and_resume_only_unfinished_pages(self):
        self.provider.fail_image=3;j=self.plan();self.store.action(j['id'],'confirm');j=self.wait(j['id']);self.assertEqual(j['status'],'failed');self.assertEqual(sum(bool(p['file']) for p in j['pages']),2)
        old=j['pages'][0]['file'];self.provider.fail_image=None;self.store.action(j['id'],'retry');j=self.wait(j['id']);self.assertEqual(j['status'],'completed');self.assertEqual(j['pages'][0]['file'],old);self.assertEqual(len(self.provider.calls),10)
    def test_redraw_retains_old_version_and_same_prompt(self):
        j=self.complete();old=j['pages'][1]['file'];old_payload=self.provider.calls[3][1];self.store.action(j['id'],'redraw',1);j=self.wait(j['id']);self.assertEqual(j['pages'][1]['versions'][0],old);self.assertEqual(len(j['pages'][1]['versions']),2);self.assertTrue((self.store.jobpath(j['id'])/old).exists());self.assertEqual(len(self.provider.calls),10)
        p=(self.store.jobpath(j['id'])/'evidence/image-02.txt').read_bytes();self.assertIn(p,old_payload);self.assertIn(p,self.provider.calls[-1][1])
    def test_failed_redraw_retry_targets_requested_page(self):
        j=self.complete();old=j['pages'][2]['file'];self.provider.fail_image=8
        self.store.action(j['id'],'redraw',2);j=self.wait(j['id']);self.assertEqual(j['status'],'failed');self.assertEqual(j['pages'][2]['file'],old)
        self.provider.fail_image=None;self.store.action(j['id'],'retry');j=self.wait(j['id']);self.assertEqual(j['status'],'completed');self.assertEqual(len(j['pages'][2]['versions']),2);self.assertEqual(len(self.provider.calls),11)
    def test_export_no_secret_and_order(self):
        j=self.complete();z=zipfile.ZipFile(io.BytesIO(self.store.export(j['id'])));self.assertEqual([x for x in z.namelist() if x.endswith('.png')],[f'{i:02d}.png' for i in range(1,8)]);self.assertTrue(all(b'secret-test-key' not in z.read(n) for n in z.namelist()));self.assertNotIn('api_key',json.dumps(self.store.settings()))
    def test_invalid_model_output_stops_before_images(self):
        self.provider.invalid=True;j=self.plan();self.assertEqual(j['status'],'failed');self.assertEqual(len(self.provider.calls),3);self.assertEqual(j['pages'],[])
    def test_validator_rejects_text_loss(self):
        p=read(FIX/'pages.json');p[1]['display_text'].pop()
        with self.assertRaises(ValueError):validate_cards(p,self.flow)
    def test_unknown_features_not_silently_ignored(self):
        f=copy.deepcopy(self.flow);f['steps'][1]['runtime_unknown']='x';f['steps'][1]['when']='has:foo'
        with self.assertRaises(Problem):engine.check_flow(f)
        f=copy.deepcopy(self.flow);f['handoff']['step_contracts']['txt_plan']['system_prompt']='Do something else'
        with self.assertRaises(Problem):engine.check_flow(f)
    def test_two_products_and_version_isolation(self):
        original=self.key;f=copy.deepcopy(self.flow);f['id']='second_product';save(self.source/'flow.json',f);second=add(self.app,self.source);self.assertEqual(len(self.store.catalog()),2)
        f=copy.deepcopy(self.flow);f['steps'][0]['prompt'].append('新的玩法规则');save(self.source/'flow.json',f);updated=add(self.app,self.source);self.assertNotEqual(updated,original);self.assertEqual(self.store.product(original)[0],self.flow);self.assertEqual(len(self.store.catalog()),2)
    def test_tampered_product_refused(self):
        p=self.app/'products'/self.key/'flow.json';f=read(p);f['steps'][0]['prompt'].append('tampered');save(p,f)
        with self.assertRaises(Problem):self.store.product(self.key)
    def test_restart_preserves_checkpoint(self):
        j=self.plan();j['status']='generating';j['stage']='images';self.store.put(j);restored=Store(self.store.root,self.app);self.assertEqual(restored.job(j['id'])['status'],'interrupted');self.assertEqual(len(restored.job(j['id'])['pages']),7)
    def test_stop_keeps_completed_page(self):
        j=self.plan()
        def generate(*args):
            self.store.action(j['id'],'stop');return PNG
        with patch('engine.image_call',side_effect=generate):self.store.action(j['id'],'confirm');j=self.wait(j['id'])
        self.assertEqual(j['status'],'stopped');self.assertEqual(sum(bool(p['file']) for p in j['pages']),1)
    def test_duplicate_actions_rejected(self):
        j=self.plan();j['status']='generating';self.store.put(j)
        with self.assertRaises(Problem):self.store.action(j['id'],'confirm')
    def test_missing_connection_no_job_created(self):
        (self.store.root/'settings.json').unlink()
        with self.assertRaises(Problem):self.store.start(self.key,{'topic':'new topic'})
        self.assertEqual(self.store.jobs(),[])
    def test_placeholder_ratio_rejected(self):
        cfg=self.store.settings(True);cfg['image']['size']='1024x1536'
        with self.assertRaises(Problem):self.store.set_settings(cfg)
    def test_http_source_token_and_media_boundaries(self):
        server=Server(('127.0.0.1',0),self.store);threading.Thread(target=server.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{server.server_port}'
        try:
            for headers in [{'Host':'evil.example'},{'Origin':'https://evil.example'}]:
                with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(urllib.request.Request(url+'/api/settings',headers=headers))
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(urllib.request.Request(url+'/api/jobs',data=b'{}',headers={'Content-Type':'application/json'}))
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(url+'/media/0123456789abcdef/../../settings.json')
            with urllib.request.urlopen(url+'/api/settings') as r:self.assertNotIn(b'secret-test-key',r.read())
        finally:server.shutdown();server.server_close()
if __name__=='__main__':unittest.main()
