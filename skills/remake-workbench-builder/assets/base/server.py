import argparse,json,mimetypes,secrets,threading,urllib.parse,webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from presentation import load as presentation
from engine import Store,Problem,require,safe,MAX
ROOT=Path(__file__).resolve().parent
class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,store):super().__init__(address,Handler);self.store=store;self.token=secrets.token_urlsafe(32)
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,data,status=200,kind='application/json; charset=utf-8',filename=None):
        if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode()
        if isinstance(data,str):data=data.encode()
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if filename:self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers();self.wfile.write(data)
    def guard(self,write=False):
        port=self.server.server_address[1];hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
        require(self.headers.get('Host') in hosts,'仅允许本机访问');origin=self.headers.get('Origin');require(not origin or origin in {'http://'+h for h in hosts},'请求来源不允许')
        if write:require(secrets.compare_digest(self.headers.get('X-Workbench-Token',''),self.server.token),'请刷新页面后重试')
    def do_GET(self):
        try:
            self.guard();s=self.server.store;path=urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            if path=='/':return self.send((ROOT/'static/index.html').read_text().replace('__TOKEN__',self.server.token),kind='text/html; charset=utf-8')
            if path in ['/app.js','/style.css']:
                p=ROOT/'static'/path[1:];return self.send(p.read_bytes(),kind='text/javascript; charset=utf-8' if p.suffix=='.js' else 'text/css; charset=utf-8')
            if path=='/api/catalog':return self.send(s.catalog())
            if path.startswith('/api/products/'):
                parts=path.split('/');require(len(parts)==5,'玩法地址无效');f,folder=s.product(parts[3]+'/'+parts[4])
                return self.send(dict({k:f[k] for k in ['id','name','inputs','presets','steps','outputs'] if k in f},presentation=presentation(f,folder)))
            if path=='/api/settings':return self.send(s.settings())
            if path=='/api/jobs':return self.send(s.jobs())
            if path.startswith('/api/jobs/'):
                parts=path.split('/');j=s.job(parts[3])
                if len(parts)==4:return self.send(j)
                if len(parts)==5 and parts[4]=='plan':
                    return self.send({'pages':[{'index':i,'prompt':(s.jobpath(j['id'])/'evidence'/f'image-{i:02d}.txt').read_text(encoding='utf-8'),'image_inputs':j.get('image_inputs',1)} for i in range(1,len(j['pages'])+1)]})
                if len(parts)==5 and parts[4]=='export':return self.send(s.export(j['id']),kind='application/zip',filename='creation.zip')
            if path.startswith('/media/'):
                _,_,jid,name=path.split('/');j=s.job(jid)
                require(name in [v for p in j['pages'] for v in p.get('versions',[])],'图片不存在');p=safe(s.jobpath(jid),name)
                return self.send(p.read_bytes(),kind=mimetypes.guess_type(name)[0] or 'application/octet-stream')
            if path.startswith('/product/'):
                _,_,pid,version,*parts=path.split('/');f,folder=s.product(pid+'/'+version);name='/'.join(parts)
                require(name in [p['file'] for p in f['presets']],'资源不存在');p=safe(folder,name);return self.send(p.read_bytes(),kind=mimetypes.guess_type(name)[0])
            self.send({'error':'页面不存在'},404)
        except (Problem,ValueError,KeyError,TypeError,OSError) as e:self.send({'error':str(e) if isinstance(e,Problem) else '资源不存在或数据格式无效'},400)
    def do_POST(self):
        try:
            self.guard(True);n=int(self.headers.get('Content-Length',0));require(0<n<1024*1024,'请求过大或为空');b=json.loads(self.rfile.read(n));require(isinstance(b,dict),'数据无效')
            s=self.server.store;path=urllib.parse.urlsplit(self.path).path
            if path=='/api/settings':result=s.set_settings(b)
            elif path=='/api/jobs':result=s.start(b['product'],b.get('values',{}))
            elif path.startswith('/api/jobs/'):
                parts=path.split('/');require(len(parts)==5,'操作无效');result=s.action(parts[3],parts[4],b.get('index'))
            else:return self.send({'error':'操作不存在'},404)
            self.send(result)
        except Problem as e:self.send({'error':str(e)},400)
        except (ValueError,KeyError,TypeError,OSError):self.send({'error':'输入无效或资源不存在'},400)
        except Exception:self.send({'error':'操作失败，请检查设置与输入'},500)
def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8766);p.add_argument('--data-dir',default=str(ROOT/'data'));p.add_argument('--no-browser',action='store_true');a=p.parse_args()
    try:server=Server(('127.0.0.1',a.port),None)
    except OSError:print('端口被占用，请使用已有窗口或 --port 指定其他端口。');return 1
    server.store=Store(a.data_dir,ROOT)
    url=f'http://127.0.0.1:{server.server_address[1]}';print('工作台：'+url+'\n关闭此窗口会停止服务；内容已保存在 '+str(server.store.root),flush=True)
    if not a.no_browser:threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=='__main__':raise SystemExit(main())
