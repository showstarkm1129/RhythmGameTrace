from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import os, webbrowser
os.chdir(Path(__file__).resolve().parent)
try:
    server=ThreadingHTTPServer(('127.0.0.1',8765),SimpleHTTPRequestHandler)
except OSError:
    server=ThreadingHTTPServer(('127.0.0.1',0),SimpleHTTPRequestHandler)
url=f'http://127.0.0.1:{server.server_port}/arcaea_touch_video_sync.html'
print('Arcaea Touch Studio: '+url,flush=True)
print('Press Ctrl+C to close the server.',flush=True)
webbrowser.open(url)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
