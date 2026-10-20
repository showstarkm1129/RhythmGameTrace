"""Arcaea Touch Studio local server: serves the viewer and records iPad touch logs over USB.

Copied to viewer/launch_viewer.py by source/build_video_sync.py. Run with the Python that has pymobiledevice3:
    py launch_viewer.py [--port 8765] [--no-browser]

Recording = the README capture command, driven from the viewer's "記録" panel, via touch_oslog.py:
    pymobiledevice3 developer dvt oslog --format json, with a device-side filter
    -> "Digitizer token:" records of backboardd's TouchEvents marked "behavior: foreground"
The plain command streams every process's log and filters on the PC, which fell ~5x behind real time while touching.
backboardd logs every touch twice: under a raw token in device pixels, and under a "behavior: foreground" token in
screen points. The old command kept only the latter by its token value (0xD8DEEB04), but that value changes between
sessions, so the marker is used instead.
"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import argparse, collections, datetime, json, os, re, subprocess, sys, tempfile, threading, time, webbrowser

import ai_bridge

HERE = Path(__file__).resolve().parent
# In the repository, captures go next to the other data; in a standalone viewer folder, next to this script
CAPTURE_DIR = HERE.parent / 'data' / 'captures' if (HERE.parent / 'data').is_dir() else HERE / 'captures'
WORKER = HERE / 'touch_oslog.py'
READY_MARKER = 'TOUCH_OSLOG_READY'  # printed by touch_oslog.py once the device stream is open
SUB_RX = re.compile(r'path:\s*\d+;\s*\{[\d.]+,\s*[\d.]+\};\s*(.*?)touchID:\s*(0x[\da-fA-F]+)')
TOKEN_RX = re.compile(r'Digitizer token:\s*(0x[\da-fA-F]+)')
# touch_<date>_<time>[_<name>].jsonl: the timestamp keeps the files in recording order, the name is the user's
NAME_RX = re.compile(r'^touch_(\d{8}_\d{6})(?:_([^\\/:*?"<>|\x00-\x1f]{1,80}))?\.jsonl$')
TITLE_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# Only these keys are needed downstream (touchlog.py / the viewer); format_string and arguments repeat the message
KEEP_KEYS = ('timestamp', 'pid', 'subsystem', 'category', 'message')
CHILD_ENV = dict(os.environ, PYTHONWARNINGS='ignore', PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')


def clean_title(title):
    """A name the user typed, made safe for a Windows file name (or '' for none)."""
    title = TITLE_BAD.sub('_', str(title or '')).strip()
    return title.rstrip('. ')[:80].strip()


def capture_name(stamp, title):
    return f'touch_{stamp}' + (f'_{title}' if title else '') + '.jsonl'


def rename_capture(path, title):
    """Rename a finished capture to carry title; returns the new path. Raises ValueError with a message for the user."""
    m = NAME_RX.match(path.name)
    if not m or not path.is_file():
        raise ValueError('ファイルが見つかりません')
    new = path.with_name(capture_name(m.group(1), clean_title(title)))
    if new == path:
        return path
    if new.exists():
        raise ValueError(f'{new.name} は既にあります')
    path.rename(new)
    return new


def pmd3(*args):
    return [sys.executable, '-m', 'pymobiledevice3', *args]


def clean_stderr(line):
    line = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
    return '' if not line or 'Warning' in line or 'warnings.warn' in line else line


class Capture:
    """One recording at a time. States: idle -> connecting -> recording -> stopping -> idle (or error)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = 'idle'
        self.proc = None
        self.out = None
        self.file = None
        self.error = ''
        self.title = ''
        self.stderr_tail = collections.deque(maxlen=12)
        self._reset_stats()

    def _reset_stats(self):
        self.started_at = None
        self.live_at = None
        self.stop_requested_at = None
        self.lines = 0
        self.records = 0
        self.contacts = 0
        self.active = set()
        self.tokens = collections.Counter()
        self.skipped_raw = 0
        self.last_touch = None

    # ----- public API -----
    def status(self):
        with self.lock:
            now = time.time()
            return {
                'state': self.state, 'file': self.file.name if self.file else None, 'title': self.title, 'error': self.error,
                'elapsed': round(now - self.started_at, 1) if self.started_at and self.state != 'idle' else None,
                'live': self.live_at is not None, 'lines': self.lines, 'records': self.records,
                'contacts': self.contacts, 'active': len(self.active), 'tokens': dict(self.tokens),
                'skipped_raw': self.skipped_raw,
                'stderr': list(self.stderr_tail)[-4:],
            }

    def start(self, title=''):
        with self.lock:
            if self.state in ('connecting', 'recording', 'stopping'):
                return False, '既に記録中です'
            CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
            self._reset_stats()
            self.error = ''
            self.stderr_tail.clear()
            self.title = clean_title(title)
            self.file = CAPTURE_DIR / capture_name(datetime.datetime.now().strftime('%Y%m%d_%H%M%S'), self.title)
            self.out = open(self.file, 'w', encoding='utf-8', newline='\n')
            self.started_at = time.time()
            self.state = 'connecting'
        threading.Thread(target=self._run, daemon=True).start()
        return True, ''

    def set_title(self, title):
        """Name the recording in progress. The open file cannot be renamed on Windows, so it is renamed on close."""
        with self.lock:
            if self.state not in ('connecting', 'recording', 'stopping'):
                return False, '記録していません'
            self.title = clean_title(title)
        return True, ''

    def stop(self):
        with self.lock:
            if self.state not in ('connecting', 'recording'):
                return False, '記録していません'
            self.stop_requested_at = time.time()
            self.state = 'stopping'
        return True, ''

    def shutdown(self):
        self._kill()
        self._close()

    # ----- internals -----
    def _run(self):
        if not WORKER.is_file():
            self._fail(f'{WORKER.name} が見つかりません（viewer フォルダを作り直してね）')
            return
        proc = subprocess.Popen([sys.executable, str(WORKER)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=CHILD_ENV)
        with self.lock:
            self.proc = proc
        threading.Thread(target=self._read_out, args=(proc,), daemon=True).start()
        threading.Thread(target=self._read_err, args=(proc,), daemon=True).start()
        self._watch()

    def _mark_live(self):
        if self.live_at is None:
            self.live_at = time.time()
            if self.state == 'connecting':
                self.state = 'recording'

    def _read_out(self, proc):
        for raw in proc.stdout:
            line = raw.decode('utf-8', 'replace')
            with self.lock:
                if proc is not self.proc or self.out is None:
                    break
                self.lines += 1
                self._mark_live()
                # Cheap substring test first: the stream also carries unrelated backboardd logs
                if 'Digitizer token:' not in line:
                    continue
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                msg = j.get('message')
                if not isinstance(msg, str) or not msg.startswith('Digitizer token:'):
                    continue
                if 'behavior: foreground' not in msg.split('subevents', 1)[0]:
                    self.skipped_raw += 1
                    continue
                self.out.write(json.dumps({k: j[k] for k in KEEP_KEYS if k in j}, ensure_ascii=False) + '\n')
                self.records += 1
                self.last_touch = time.time()
                m = TOKEN_RX.match(msg)
                if m:
                    self.tokens[m.group(1)] += 1
                for txt, ident in SUB_RX.findall(msg):
                    if re.search(r'\bdown;', txt):
                        if ident not in self.active:
                            self.contacts += 1
                        self.active.add(ident)
                    elif re.search(r'\bup;', txt) or 'range (out)' in txt:
                        self.active.discard(ident)
                if self.records % 200 == 0:
                    self.out.flush()

    def _read_err(self, proc):
        for raw in proc.stderr:
            line = clean_stderr(raw.decode('utf-8', 'replace'))
            if not line:
                continue
            with self.lock:
                if READY_MARKER in line:
                    self._mark_live()
                else:
                    self.stderr_tail.append(line)

    def _watch(self):
        while True:
            time.sleep(0.2)
            with self.lock:
                state, proc = self.state, self.proc
                now = time.time()
                if state == 'stopping':
                    # Logs reach the PC with a delay, and stopping mid-contact loses its "up" event (README §2):
                    # keep reading at least 1.5 s, until no finger is down for 1 s, at most 6 s
                    waited = now - self.stop_requested_at
                    quiet = not self.active and (self.last_touch is None or now - self.last_touch >= 1.0)
                    if waited >= 6 or (waited >= 1.5 and quiet):
                        break
                if proc.poll() is not None:
                    if state != 'stopping':
                        tail = [l for l in self.stderr_tail if 'Trying again' not in l]
                        if any('No module named' in l and 'pymobiledevice3' in l for l in tail):
                            self.error = 'pymobiledevice3 が見つかりません（py -m pip install pymobiledevice3）'
                        elif self.live_at is None:
                            self.error = 'iPadに接続できません（USB接続・ロック解除・デベロッパモードを確認）: ' + (tail[-1] if tail else f'exit {proc.returncode}')
                        else:
                            self.error = 'ログの取得が止まりました（iPadが外れた可能性）: ' + (tail[-1] if tail else f'exit {proc.returncode}')
                    break
        failed = bool(self.error)
        self._kill()
        self._close()
        with self.lock:
            self.state = 'error' if failed else 'idle'

    def _fail(self, message):
        self._kill()
        self._close()
        with self.lock:
            self.error = message
            self.state = 'error' if message else 'idle'

    def _kill(self):
        with self.lock:
            proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _close(self):
        with self.lock:
            if self.out:
                self.out.close()
                self.out = None
            # Drop a capture that never received a touch so failed attempts don't pile up
            if self.file and self.records == 0 and self.file.exists():
                try:
                    self.file.unlink()
                except OSError:
                    pass
                self.file = None
            elif self.file and self.file.exists():
                try:
                    self.file = rename_capture(self.file, self.title)
                except (ValueError, OSError) as e:
                    self.error = self.error or f'名前を付けられませんでした（{e}）。{self.file.name} のまま保存しています'


capture = Capture()
screenshot_lock = threading.Lock()


def device_screenshot():
    """PNG of the iPad's screen (developer mode, like the recording). Returns (png bytes, error message)."""
    if not screenshot_lock.acquire(blocking=False):
        return None, '前のスクリーンショットを撮っています'
    fd, tmp = tempfile.mkstemp(suffix='.png')
    os.close(fd)
    try:
        r = subprocess.run(pmd3('developer', 'dvt', 'screenshot', tmp), capture_output=True, env=CHILD_ENV, timeout=60)
        data = Path(tmp).read_bytes() if r.returncode == 0 else b''
        if not data:
            lines = [clean_stderr(l) for l in r.stderr.decode('utf-8', 'replace').splitlines()]
            return None, ([l for l in lines if l] or [f'exit {r.returncode}'])[-1]
        return data, ''
    except subprocess.TimeoutExpired:
        return None, '60秒で撮れませんでした'
    finally:
        screenshot_lock.release()
        try:
            os.unlink(tmp)
        except OSError:
            pass
device_cache = {'at': 0, 'value': None}


def device_info():
    if time.time() - device_cache['at'] < 5:
        return device_cache['value']
    try:
        r = subprocess.run(pmd3('usbmux', 'list'), capture_output=True, env=CHILD_ENV, timeout=30)
        devs = json.loads(r.stdout.decode('utf-8', 'replace')) if r.returncode == 0 else []
        usb = [d for d in devs if d.get('ConnectionType') == 'USB']
        value = {'connected': bool(usb), 'devices': [{k: d.get(k) for k in ('DeviceName', 'ProductType', 'ProductVersion', 'ConnectionType')} for d in usb]}
        if r.returncode != 0:
            value['error'] = (clean_stderr(r.stderr.decode('utf-8', 'replace').splitlines()[-1]) if r.stderr else '') or 'pymobiledevice3 を実行できません'
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        value = {'connected': False, 'devices': [], 'error': str(e)}
    device_cache.update(at=time.time(), value=value)
    return value


def list_captures():
    if not CAPTURE_DIR.is_dir():
        return []
    files = sorted((p for p in CAPTURE_DIR.glob('touch_*.jsonl') if NAME_RX.match(p.name)), reverse=True)
    return [{'name': p.name, 'size': p.stat().st_size} for p in files[:30]]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE), **kw)

    def log_message(self, fmt, *args):
        if '/api/' not in (args[0] if args else ''):
            super().log_message(fmt, *args)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api_allowed(self):
        # Only the viewer page may call the API: the custom header forces a CORS preflight for other sites,
        # and the Host check blocks DNS-rebinding to this local port
        host = (self.headers.get('Host') or '').split(':')[0]
        return self.headers.get('X-Touch-Studio') == '1' and host in ('127.0.0.1', 'localhost')

    def do_GET(self):
        url = urlparse(self.path)
        if not url.path.startswith('/api/'):
            return super().do_GET()
        if not self._api_allowed():
            return self._json({'error': 'forbidden'}, 403)
        if url.path == '/api/capture/status':
            return self._json(capture.status())
        if url.path == '/api/device':
            return self._json(device_info())
        if url.path == '/api/ai/config':
            return self._json(ai_bridge.public_config())
        if url.path == '/api/device/screenshot':
            png, err = device_screenshot()
            if not png:
                return self._json({'error': err}, 503)
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(png)))
            self.end_headers()
            self.wfile.write(png)
            return
        if url.path == '/api/captures':
            return self._json({'dir': str(CAPTURE_DIR), 'files': list_captures()})
        if url.path == '/api/captures/file':
            name = (parse_qs(url.query).get('name') or [''])[0]
            path = CAPTURE_DIR / name
            if not NAME_RX.match(name) or not path.is_file():
                return self._json({'error': 'not found'}, 404)
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json({'error': 'not found'}, 404)

    def _ai(self, action, body):
        try:
            if action == 'config':
                return self._json(ai_bridge.public_config(ai_bridge.update_config(body)))
            if action == 'detect':
                return self._json(ai_bridge.detect(body.get('kind'), body.get('folder') or None))
            if action == 'pick-folder':
                return self._json({'folder': ai_bridge.pick_folder(str(body.get('title') or 'フォルダを選ぶ'))})
            if action == 'models':
                return self._json({'models': ai_bridge.list_models(body.get('kind'))})
            if action == 'ask':
                system, prompt = body.get('system'), body.get('prompt')
                if not isinstance(system, str) or not isinstance(prompt, str) or not prompt.strip():
                    return self._json({'error': '質問が空です'}, 400)
                return self._json(ai_bridge.ask(system, prompt, body.get('provider')))
        except ai_bridge.AIError as e:
            return self._json({'error': str(e)}, 502)
        return self._json({'error': 'not found'}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        if not self._api_allowed():
            return self._json({'error': 'forbidden'}, 403)
        try:
            n = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
            if not isinstance(body, dict):
                body = {}
        except ValueError:
            return self._json({'error': 'bad request'}, 400)
        if url.path.startswith('/api/ai/'):
            return self._ai(url.path[8:], body)
        if url.path == '/api/captures/rename':
            name = str(body.get('name') or '')
            if not NAME_RX.match(name):
                return self._json({'error': 'ファイル名が正しくありません'}, 400)
            # The file being recorded is still open: name it through the capture instead
            if capture.status()['file'] == name and capture.status()['state'] in ('connecting', 'recording', 'stopping'):
                ok, msg = capture.set_title(body.get('title'))
                return self._json({'ok': ok, 'error': msg, 'name': name, 'pending': True}, 200 if ok else 409)
            try:
                new = rename_capture(CAPTURE_DIR / name, body.get('title'))
            except (ValueError, OSError) as e:
                return self._json({'ok': False, 'error': str(e)}, 409)
            return self._json({'ok': True, 'name': new.name})
        if url.path == '/api/capture/start':
            ok, msg = capture.start(body.get('title'))
        elif url.path == '/api/capture/title':
            ok, msg = capture.set_title(body.get('title'))
        elif url.path == '/api/capture/stop':
            ok, msg = capture.stop()
        else:
            return self._json({'error': 'not found'}, 404)
        self._json({'ok': ok, 'error': msg, **capture.status()}, 200 if ok else 409)


def main():
    ap = argparse.ArgumentParser(description='Arcaea Touch Studio viewer + USB touch recorder')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}/arcaea_touch_video_sync.html'
    print('Arcaea Touch Studio: ' + url, flush=True)
    print('Recordings are saved to: ' + str(CAPTURE_DIR), flush=True)
    print('Press Ctrl+C to close the server.', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        capture.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
