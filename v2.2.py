#!/usr/bin/env python3
import sys, os, json, asyncio, argparse, platform, struct, contextlib, signal

IS_WIN = platform.system() == "Windows"
if IS_WIN:
    import msvcrt
else:
    import tty, termios, fcntl, select
try:
    import websockets
except ImportError:
    print("pip install websockets")
    sys.exit(1)

def get_size():
    try:
        s = os.get_terminal_size()
        return s.lines, s.columns
    except Exception:
        return 24, 80

async def run_server(p, verbose=False):
    if platform.system() != "Linux":
        sys.exit("Server runs Linux only")
    print(f"[ush] server running on :{p}")
    async def handler(ws, _=None):
        master = slave = pid = None
        loop = asyncio.get_running_loop()
        q = asyncio.Queue()
        try:
            try:
                init = json.loads(await ws.recv())
                rows = int(init.get("rows", 24))
                cols = int(init.get("cols", 80))
            except Exception:
                return
            master, slave = os.openpty()
            fcntl.ioctl(slave, 21524, struct.pack("HHHH", rows, cols, 0, 0))
            pid = os.fork()
            if pid == 0:
                os.close(master)
                os.login_tty(slave)
                os.execvp("/bin/login", ["/bin/login"])

            def rd():
                try:
                    d = os.read(master, 16384)
                    if d:
                        q.put_nowait(d)
                    else:
                        with contextlib.suppress(Exception):
                            loop.remove_reader(master)
                        q.put_nowait(None)
                except Exception:
                    with contextlib.suppress(Exception):
                        loop.remove_reader(master)
                    q.put_nowait(None)
            loop.add_reader(master, rd)
            async def ws_r():
                try:
                    async for msg in ws:
                        if isinstance(msg, bytes):
                            with contextlib.suppress(Exception):
                                os.write(master, msg)
                        else:
                            try:
                                j = json.loads(msg)
                            except Exception:
                                continue
                            if j.get("op") == "resize":
                                with contextlib.suppress(Exception):
                                    fcntl.ioctl(slave, 21524, struct.pack("HHHH", int(j["rows"]), int(j["cols"]), 0, 0))
                                    os.kill(pid, signal.SIGWINCH)
                finally:
                    q.put_nowait(None)
            async def ws_w():
                while True:
                    d = await q.get()
                    if d is None:
                        break
                    with contextlib.suppress(Exception):
                        await ws.send(d)
            async def reap():
                await loop.run_in_executor(None, os.waitpid, pid, 0)
                with contextlib.suppress(Exception):
                    await ws.send(b"Connection closed.")
                with contextlib.suppress(Exception):
                    await ws.close()
                q.put_nowait(None)
            await asyncio.gather(ws_r(), ws_w(), reap())
        finally:
            if master is not None:
                with contextlib.suppress(Exception):
                    loop.remove_reader(master)
                with contextlib.suppress(Exception):
                    os.close(master)
            if slave is not None:
                with contextlib.suppress(Exception):
                    os.close(slave)
            if pid:
                with contextlib.suppress(Exception):
                    os.kill(pid, 9)
    async with websockets.serve(handler, "0.0.0.0", p):
        await asyncio.Future()

async def run_client(host, port, verbose=False):
    addr = f"ws://{host}:{port}"
    stop = asyncio.Event()
    send_q = asyncio.Queue()
    manual = {"v": False}
    async def stdout_loop(ws):
        try:
            async for msg in ws:
                if isinstance(msg, str):
                    msg = msg.encode("utf-8", "ignore")
                sys.stdout.buffer.write(msg)
                sys.stdout.buffer.flush()
        finally:
            stop.set()
    async def sender(ws):
        while not stop.is_set():
            try:
                d = await asyncio.wait_for(send_q.get(), 0.1)
            except asyncio.TimeoutError:
                continue
            with contextlib.suppress(Exception):
                await ws.send(d)
    async def resize(ws):
        last = get_size()
        with contextlib.suppress(Exception):
            await ws.send(json.dumps({"rows": last[0], "cols": last[1]}))
        while not stop.is_set():
            await asyncio.sleep(0.5)
            cur = get_size()
            if cur != last:
                last = cur
                with contextlib.suppress(Exception):
                    await ws.send(json.dumps({"op": "resize", "rows": cur[0], "cols": cur[1]}))
    async def stdin_win():
        while not stop.is_set():
            if not msvcrt.kbhit():
                await asyncio.sleep(0.01)
                continue
            ch = msvcrt.getwch()
            if ch == "\x1d":
                manual["v"] = True
                stop.set()
                return
            if ch in ("\x00", "\xe0"):
                nxt = msvcrt.getwch()
                seq = {
                    "H": b"\x1b[A",
                    "P": b"\x1b[B",
                    "K": b"\x1b[D",
                    "M": b"\x1b[C",
                    "G": b"\x1b[H",
                    "O": b"\x1b[F",
                    "R": b"\x1b[2~",
                    "S": b"\x1b[3~",
                    "I": b"\x1b[5~",
                    "Q": b"\x1b[6~",
                }.get(nxt)
                if seq:
                    await send_q.put(seq)
                continue
            if ch in ("\r", "\n"):
                await send_q.put(b"\r")
                continue
            await send_q.put(ch.encode("utf-8", "ignore"))
    async def stdin_unix():
        loop = asyncio.get_running_loop()
        old = termios.tcgetattr(0)
        tty.setraw(0)
        cur = termios.tcgetattr(0)
        cur[3] &= ~termios.ISIG
        termios.tcsetattr(0, termios.TCSADRAIN, cur)
        try:
            while not stop.is_set():
                dr, _, _ = await loop.run_in_executor(None, select.select, [0], [], [], 0.1)
                if not dr:
                    continue
                ch = os.read(0, 4096)
                if b"\x1d" in ch:
                    manual["v"] = True
                    stop.set()
                    return
                if ch:
                    await send_q.put(ch)
        finally:
            termios.tcsetattr(0, termios.TCSADRAIN, old)
    async def stdin_loop(): await (stdin_win() if IS_WIN else stdin_unix())
    try:
        async with websockets.connect(addr) as ws:
            tasks = [
                asyncio.create_task(stdout_loop(ws)),
                asyncio.create_task(sender(ws)),
                asyncio.create_task(resize(ws)),
                asyncio.create_task(stdin_loop()),
            ]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        print(f"Connection failed: {e}")
    finally:
        if manual["v"]:
            print("[ush] aborted by user.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ush v2.2")
    parser.add_argument("--server", "-s", action="store_true")
    parser.add_argument("-p", type=int, default=80)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("host", nargs="?")
    args = parser.parse_args()
    if args.server:
        try:
            asyncio.run(run_server(args.p, args.verbose))
        except KeyboardInterrupt:
            pass
    elif args.host:
        try:
            asyncio.run(run_client(args.host, args.p, args.verbose))
        except KeyboardInterrupt:
            pass
    else:
        parser.print_help()
