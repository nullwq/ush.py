#!/usr/bin/env python3
#Anyone whos reading this dont worry you can understand what is going on :) 
#This is Python not C :)
import sys,os,json,asyncio,argparse,platform,signal,struct
import tty, termios, fcntl, select

try: import websockets
except ImportError:
    #actually we dont really need this but uhh let it happen ig
    print("Please install 'websockets' library: pip install websockets")
    sys.exit(1)

async def run_server(p, verbose=False):
    if platform.system() != "Linux": sys.exit("Server runs Linux only")

    async def handler(ws, path):
        master , slave, pid = None, None, None # its easy to read like this THIS IS NOT C DONT PUT VARIABLES LIKE WE ARE IN C
        loop = asyncio.get_running_loop() # async loop yay!
        try:
            init = await ws.recv()
            if not isinstance(init, str): return
            data = json.loads(init)
            rows, cols = int(data.get("rows", 24)), int(data.get("cols", 80)) # just know that if you dont know what this does i dont know neither
            
            master, slave = os.openpty() 
            fcntl.ioctl(slave, 21524, struct.pack("HHHH", rows, cols, 0, 0)) #cursed 
            pid = os.fork() #meh not that cursed now
            if pid == 0:
                os.close(master)
                os.login_tty(slave)
                os.execvp("/bin/login", ["/bin/login"])
            
            q = asyncio.Queue()
            def on_read():
                try:
                    data = os.read(master, 16384) # curse on you 
                    if data: q.put_nowait(data)
                    else: loop.remove_reader(master); q.put_nowait(None)
                except Exception: 
                    try:
                         loop.remove_reader(master)
                    except Exception: 
                        pass # it wasnt a big deal let it happen
                    q.put_nowait(None)
            
            loop.add_reader(master, on_read)
            
            async def ws_read():
                try:
                    async for msg in ws:
                        if isinstance(msg, bytes): os.write(master, msg)
                        else:
                            data = json.loads(msg)
                            if data.get("op") == "resize":
                                fcntl.ioctl(slave, 21524, struct.pack("HHHH", int(data["rows"]), int(data["cols"]), 0, 0)) #huh
                                os.kill(pid, 28)
                except Exception: 
                    pass #let it hapeeeeennnn
                finally: q.put_nowait(None)
            
            async def ws_write():
                try:
                    while True:
                        data = await q.get()
                        if data is None: break
                        await ws.send(data)
                except websockets.ConnectionClosed:
                    pass #let it happen let it hapeeennn

            await asyncio.gather(ws_read(), ws_write())
        except Exception as e:
            if verbose: print(f"[ERROR][Handler] error: {e}") # now DONT let that happen
        finally:
            if master is not None: 
                try: loop.remove_reader(master)
                except Exception: 
                    pass #let it happen again yay
                os.close(master)
            if slave is not None: os.close(slave)
            if pid:
                try: os.kill(pid, 9); os.waitpid(pid, 0) # evil serial killer >:D
                except Exception: 
                    pass

    print(f"[INFO][websocket] server running on :{p}")
    async with websockets.serve(handler, "0.0.0.0", p):
        await asyncio.Future()

async def run_client(host, port, verbose=False): 
    if host.startswith("http://"): host = host.replace("http://", "ws://", 1) #ey cf gfys
    elif host.startswith("https://"): host = host.replace("https://", "wss://", 1) #muheheheehe
    
    if host.startswith("ws://") or host.startswith("wss://"):
        addr = host
    else:
        scheme = "wss" if port == 443 else "ws" #ts is bullshit i know but im too lazy to make some fucktion to check if its an encrypted port
        addr = f"{scheme}://{host}:{port}"

    old_settings = None
    ws = None
    loop = None
    try:
        for attempt in range(2):
            try:
                ws = await websockets.connect(addr)
                break
            except Exception as e:
                status = getattr(e, "status_code", None)
                if attempt == 0 and addr.startswith("ws://") and (status in (301, 302, 307, 308, 400, 426, 502) or isinstance(e, (ConnectionRefusedError, ConnectionResetError, EOFError))):
                    if verbose: print(f"[INFO] Connection to {addr} failed ({e}), retrying with wss://...")
                    addr = addr.replace("ws://", "wss://", 1) #respectfully what the fuck
                    if addr.endswith(":80"): addr = addr[:-3] + ":443"
                else:
                    raise e # i normally hate raise i love my custom exception TUI but im too lazy

        try:
            old_settings = termios.tcgetattr(0)
            tty.setraw(0)
            cur = termios.tcgetattr(0)
            cur[3] &= ~termios.ISIG
            termios.tcsetattr(0, termios.TCSADRAIN, cur)
            
            size = os.get_terminal_size()
            await ws.send(json.dumps({"rows": size.lines, "cols": size.columns}))
            
            loop = asyncio.get_running_loop()
            stop = asyncio.Event()

            def handle_resize():
                size = os.get_terminal_size()
                asyncio.run_coroutine_threadsafe(ws.send(json.dumps({"op":"resize","rows":size.lines,"cols":size.columns})), loop)

            loop.add_signal_handler(signal.SIGWINCH, handle_resize)# ahhh just like joe biden says "what"

            async def stdin_loop():
                while not stop.is_set():
                    dr, _, _ = await loop.run_in_executor(None, select.select, [0], [], [], 0.1)
                    if dr:
                        ch = os.read(0, 4096)
                        if b"\x1d" in ch: stop.set(); break
                        await ws.send(ch)

            async def stdout_loop():
                try:
                    async for msg in ws:
                        sys.stdout.buffer.write(msg)
                        sys.stdout.buffer.flush()
                except websockets.ConnectionClosed:
                    pass
                finally: stop.set()

            await asyncio.gather(stdin_loop(), stdout_loop())
        finally:
            if loop:
                try: loop.remove_signal_handler(signal.SIGWINCH)
                except Exception: pass
            if ws: await ws.close()
    except Exception as e:
        if verbose: import traceback; traceback.print_exc()
        else: print(f"[ERROR][Connection]: {e}")
    finally:
        if old_settings: termios.tcsetattr(0, termios.TCSADRAIN, old_settings)
        print("\n[INFO][Connection]: Connection closed.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ush.py v2.2 (not the bad one xD)")
    parser.add_argument("--server", "-s", action="store_true")
    parser.add_argument("-p", type=int, default=80)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("host", nargs="?")
    args = parser.parse_args()
    
    if args.server:
        try: asyncio.run(run_server(args.p, args.verbose))
        except KeyboardInterrupt: 
            pass
    elif args.host:
        try: asyncio.run(run_client(args.host, args.p, args.verbose))
        except KeyboardInterrupt: 
            pass
    else:
        parser.print_help()
