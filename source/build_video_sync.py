from pathlib import Path
import argparse,json,sys,zipfile
ROOT=Path(__file__).resolve().parent.parent
SOURCE=ROOT/'source';BUILD=ROOT/'build';VIEWER=ROOT/'viewer'
sys.path.insert(0,str(SOURCE))
import touchlog

ap=argparse.ArgumentParser(description='Embed a touch log into source/viewer_template.html -> viewer/* and build/arcaea_touch_video_sync.zip')
ap.add_argument('jsonl',nargs='?',type=Path,default=ROOT/'data'/'touch_compact.jsonl',help='oslog JSONL capture to embed (default: data/touch_compact.jsonl)')
ap.add_argument('--title',default='Cataclysm Cry (INS)',help='display name of the embedded data')
args=ap.parse_args()
log=touchlog.load(args.jsonl)
embedded={'name':args.title,'source':args.jsonl.name,'duration':log['duration'],'strokes':log['strokes'],
          'info':{'records':log['records'],'skipped':log['skipped'],'token':log['token']}}
# '<\/' keeps a name containing "</script>" from closing the script block
html=(SOURCE/'viewer_template.html').read_text('utf-8').replace('__TOUCH_DATA__',json.dumps(embedded,ensure_ascii=False,separators=(',',':')).replace('</','<\\/'))
out=VIEWER/'arcaea_touch_video_sync.html';out.write_text(html,encoding='utf8',newline='\n')
# Windows local launcher: start HTTP server first, then open browser (avoids a startup race)
launch_script='''from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
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
'''
(VIEWER/'launch_viewer.py').write_text(launch_script,encoding='utf-8',newline='\n')
launcher='''@echo off
setlocal
cd /d "%~dp0"
py launch_viewer.py
if errorlevel 1 (
  echo The py launcher failed. Trying python...
  python launch_viewer.py
)
if errorlevel 1 pause
'''
(VIEWER/'start_viewer.bat').write_text(launcher,encoding='ascii',newline='\n')
readme='''Arcaea Touch Studio — 使い方

1. start_viewer.bat をダブルクリック。
2. ブラウザが開いたら、譜面動画ファイル（MP4など）を画面にドラッグ＆ドロップするか、右側の「ローカル動画」で選択。
   YouTubeを使う場合は「YouTube」タブでURLを貼り付けて「読込」。
   別のプレイのタッチログ（.jsonl）も、画面へのドロップか「0. タッチデータ」→「JSONLを開く」で読み込める。
3. 動画とタッチの対応する瞬間を探して、それぞれ「基準点を設定」→「基準点を一致させる」（どちらが先でもOK）。
   タッチ側はタイムライン（表示幅を10秒/2秒にすると1本ずつ見える）や「開始 ⏭」で探すと早い。
4. ±1ms / 10ms / 100ms などで微調整。
5. オフセットや表示調整はブラウザに自動保存され、次に開いたときに復元される。
   ローカル動画だけは自動で開けないので、前回と同じファイルを選び直す。
   別のPCやブラウザに持っていく場合は「設定を保存」でJSONに書き出す。

操作: Space 再生/一時停止、←/→ 100ms移動、Shift+←/→ 約8.33ms移動、[ / ] オフセット±1ms、F 全画面、M ミュート。

YouTubeはネット接続が必要。YouTube側で埋め込み禁止の動画は再生できません。
YouTube APIの制約で、APIキーなしの検索結果をアプリ内に直接表示することはできません。
ローカル動画はファイル選択だけで使用でき、外部アップロードされません。
重要: 動画とタッチログはもともと別の記録なので、曲開始位置を自動判定してはいません。
'''
(VIEWER/'使い方.txt').write_text(readme,encoding='utf-8-sig',newline='\n')
BUILD.mkdir(exist_ok=True)
zipout=BUILD/'arcaea_touch_video_sync.zip'
with zipfile.ZipFile(zipout,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for name in ['arcaea_touch_video_sync.html','start_viewer.bat','launch_viewer.py','使い方.txt']:
  z.write(VIEWER/name,arcname=name)
print({x.name:x.stat().st_size for x in [out,zipout,VIEWER/'start_viewer.bat']})
