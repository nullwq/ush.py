#!/usr/bin/env python3
import sys,os,json,time,threading,argparse,platform,ipaddress,signal,ctypes
from queue import Queue,Empty
from http.client import HTTPConnection
M_BUF,IS_WIN=1024*1024,platform.system()=="Windows"
if IS_WIN: import msvcrt
else: import tty,termios,fcntl,struct,select
def http_post(h,p,path,headers=None,data=b""):
    c=HTTPConnection(h,p,timeout=5);hds=headers or {}
    if data:hds["Content-Length"]=str(len(data))
    c.request("POST",path,body=data,headers=hds);r=c.getresponse();d=r.read();c.close();return r.status,d
_win_send_q=None
_win_handler=None
def _install_win_handler(q):
    global _win_send_q,_win_handler
    _win_send_q=q
    if _win_handler is not None: return
    HandlerType=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_uint)
    def _h(t):
        if _win_send_q is None: return False
        if t==0 or t==1:
            try:_win_send_q.put(b'\x03');return True
            except: return False
        return False
    _win_handler=HandlerType(_h)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_win_handler,True)
def run_c(h,p,verbose=False):
    send_q=Queue();out_q=Queue();stop=[0]
    if IS_WIN:
        try:
            _install_win_handler(send_q)
        except Exception: pass
    try:
        if IS_WIN: r,c=os.get_terminal_size().lines,os.get_terminal_size().columns
        else:
            try: r,c=struct.unpack("HH",fcntl.ioctl(0,21523,b'\x00'*4))
            except: r,c=24,80
        sid=json.loads(http_post(h,p,"/auth",data=json.dumps({"rows":r,"cols":c}).encode())[1])["sid"]
    except Exception as e:
        if verbose:
            import traceback;traceback.print_exc()
        else: print("Fail")
        return
    def tx():
        while not stop[0]:
            data=b""
            try:
                while True: data+=send_q.get_nowait()
            except Empty: pass
            if data:
                try:http_post(h,p,"/push",headers={"X-Session":sid},data=data)
                except Exception:
                    if verbose:
                        import traceback;traceback.print_exc()
            else: time.sleep(0.01)
    def rx():
        while not stop[0]:
            try:
                st,rsp=http_post(h,p,"/pull",headers={"X-Session":sid})
                if st==200: out_q.put(rsp)
                elif st==410: stop[0]=1
            except Exception:
                if verbose:
                    import traceback;traceback.print_exc()
            time.sleep(0.003)
    def output_thread():
        while not stop[0]:
            try:
                d=out_q.get(timeout=0.1);sys.stdout.write(d.decode(errors="ignore"));sys.stdout.flush()
            except Empty: continue
    def poll_size():
        if IS_WIN: old=os.get_terminal_size()
        else:
            try: old=struct.unpack("HH",fcntl.ioctl(0,21523,b'\x00'*4))
            except: old=(24,80)
        while not stop[0]:
            time.sleep(0.5)
            if IS_WIN: new=os.get_terminal_size()
            else:
                try: new=struct.unpack("HH",fcntl.ioctl(0,21523,b'\x00'*4))
                except: new=(24,80)
            if new!=old:
                old=new
                try:http_post(h,p,"/resize",headers={"X-Session":sid},data=json.dumps({"rows":getattr(new,"lines",new[0]),"cols":getattr(new,"columns",new[1])}).encode())
                except Exception:
                    if verbose:
                        import traceback;traceback.print_exc()
    def input_thread():
        if IS_WIN:
            try:
                while not stop[0]:
                    if msvcrt.kbhit():
                        ch=msvcrt.getwch()
                        if ch=='\x1d': stop[0]=1;break
                        try: send_q.put(ch.encode())
                        except: send_q.put(ch.encode('utf-8','ignore'))
                    else: time.sleep(0.001)
            except Exception: stop[0]=1
        else:
            old=termios.tcgetattr(0)
            tty.setraw(0)
            cur=termios.tcgetattr(0)
            cur[3]=cur[3] & ~termios.ISIG
            termios.tcsetattr(0,termios.TCSADRAIN,cur)
            try:
                while not stop[0]:
                    if select.select([0],[],[],0.001)[0]:
                        ch=os.read(0,4096)
                        if b"\x1d" in ch: stop[0]=1;break
                        send_q.put(ch)
            finally:
                termios.tcsetattr(0,termios.TCSADRAIN,old)
    threading.Thread(target=tx,daemon=True).start();threading.Thread(target=rx,daemon=True).start()
    threading.Thread(target=output_thread,daemon=True).start();threading.Thread(target=poll_size,daemon=True).start()
    threading.Thread(target=input_thread,daemon=True).start()
    while not stop[0]:
        try: time.sleep(0.1)
        except Exception: pass
    print("\nConnection closed.")
sess={}
if __name__=="__main__":
    p=argparse.ArgumentParser(description="ush.py v2.0");p.add_argument("--server","-s",action="store_true");p.add_argument("-p",type=int,default=8080);p.add_argument("-d",action="store_true");p.add_argument("-v","--verbose",action="store_true");p.add_argument("host",nargs="?");a=p.parse_args()
    if a.host and "-p" not in sys.argv:
        try: ipaddress.ip_address(a.host)
        except: a.p=80
    if a.server:
        if platform.system()!="Linux": sys.exit("Server runs Linux only")
        from http.server import BaseHTTPRequestHandler,HTTPServer
        class H(BaseHTTPRequestHandler):
            def _S(self,d=b"",c=200): self.send_response(c);self.end_headers();self.wfile.write(d)
            def do_POST(self):
                L,sid,path=int(self.headers.get("Content-Length",0)),self.headers.get("X-Session"),self.path
                body=self.rfile.read(L)
                if path=="/auth":
                    try:
                        d=json.loads(body);r,c=int(d.get("rows",24)),int(d.get("cols",80))
                        m,sl=os.openpty();fcntl.ioctl(sl,21524,struct.pack("HHHH",r,c,0,0))
                        pid=os.fork()
                        if pid==0: os.close(m); os.login_tty(sl); os.execvp("/bin/login",["/bin/login"])
                        sid=os.urandom(32).hex(); sess[sid]={"fd":m,"slave_fd":sl,"pid":pid,"q":Queue(50)}
                        def rd_loop(fd,mid):
                            while sess.get(mid):
                                try:
                                    if select.select([fd],[],[],0.5)[0]:
                                        data=os.read(fd,8192)
                                        if data: sess[mid]["q"].put(data)
                                except: break
                            sess.pop(mid,None)
                        threading.Thread(target=rd_loop,args=(m,sid),daemon=True).start()
                        self._S(json.dumps({"sid":sid}).encode())
                    except: self._S(b"fail",401)
                elif not (ss:=sess.get(sid)): self._S(b"",410)
                elif path=="/pull":
                    out=b"";q=ss["q"]
                    while not q.empty() and len(out)<M_BUF: out+=q.get()
                    self._S(out)
                elif path=="/push": os.write(ss["fd"],body); self._S(b"ok")
                elif path=="/resize": d=json.loads(body); fcntl.ioctl(ss["slave_fd"],21524,struct.pack("HHHH",int(d["rows"]),int(d["cols"]),0,0)); os.kill(ss["pid"],28); self._S(b"ok")
        if a.d:
            if os.fork()>0: sys.exit(0)
            os.setsid()
            if os.fork()>0: sys.exit(0)
        print(f"[ush] server running on :{a.p}"); HTTPServer(("0.0.0.0",a.p),H).serve_forever()
    elif a.host: run_c(a.host,a.p,a.verbose)
    else: p.print_help()
