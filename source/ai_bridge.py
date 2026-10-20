"""AI helpers for the viewer: the Claude Code and Codex CLIs, the Anthropic API and OpenAI-compatible APIs
(OpenAI, LM Studio, Ollama). Used by launch_viewer.py; copied to viewer/ by source/build_video_sync.py.

Settings live in ~/.arcaea_touch_studio/ai.json. API keys stay on this PC: the page only learns whether one is set.
The CLIs run with the user's own login, in an empty temporary folder, with the whole request on stdin (no prompt text
on the command line, where a .cmd launcher would let cmd.exe interpret it).
"""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

CONFIG = Path.home() / '.arcaea_touch_studio' / 'ai.json'
WINDOWS = os.name == 'nt'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
CLI_KINDS = ('claude-code', 'codex')
API_KINDS = ('anthropic', 'openai', 'lmstudio', 'ollama')
DEFAULTS = {
    'provider': 'claude-code',
    'claude-code': {'folder': '', 'exe': '', 'model': ''},
    'codex': {'folder': '', 'exe': '', 'model': ''},
    'anthropic': {'model': 'claude-sonnet-5-5', 'key': ''},
    'openai': {'base': 'https://api.openai.com/v1', 'model': '', 'key': ''},
    'lmstudio': {'base': 'http://localhost:1234/v1', 'model': ''},
    'ollama': {'base': 'http://localhost:11434/v1', 'model': ''},
}
# File names to look for, best first. A native .exe beats an npm .cmd launcher; non-Windows uses the bare name
EXE_NAMES = {
    'claude-code': ['claude.exe', 'claude.cmd', 'claude.bat'] if WINDOWS else ['claude'],
    'codex': ['codex.exe', 'codex.cmd', 'codex.bat'] if WINDOWS else ['codex'],
}
SKIP_DIRS = {'.git', '__pycache__', 'cache', 'Cache', 'logs', 'tmp', 'temp'}


class AIError(Exception):
    """A failure to show the user as is."""


# ---------- settings ----------
def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        saved = json.loads(CONFIG.read_text('utf-8'))
    except (OSError, ValueError):
        saved = {}
    if saved.get('provider') in DEFAULTS and saved.get('provider') != 'provider':
        cfg['provider'] = saved['provider']
    for kind in (*CLI_KINDS, *API_KINDS):
        if isinstance(saved.get(kind), dict):
            for k in cfg[kind]:
                if isinstance(saved[kind].get(k), str):
                    cfg[kind][k] = saved[kind][k]
    return cfg


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), 'utf-8')
    os.replace(tmp, CONFIG)


def env_key(kind):
    return os.environ.get({'anthropic': 'ANTHROPIC_API_KEY', 'openai': 'OPENAI_API_KEY'}.get(kind, ''), '')


def public_config(cfg=None):
    """The settings without the keys: only whether one is set (saved here or in the environment) and its last 4."""
    cfg = json.loads(json.dumps(cfg or load_config()))
    for kind in ('anthropic', 'openai'):
        key = cfg[kind].pop('key', '') or env_key(kind)
        cfg[kind]['hasKey'] = bool(key)
        cfg[kind]['keyHint'] = key[-4:] if key else ''
        cfg[kind]['keyFromEnv'] = bool(key) and not load_config()[kind]['key']
    cfg['configPath'] = str(CONFIG)
    return cfg


def update_config(update):
    """Merge what the page sent. A key is replaced only when a new one is given, or cleared with clearKey."""
    cfg = load_config()
    if update.get('provider') in (*CLI_KINDS, *API_KINDS):
        cfg['provider'] = update['provider']
    for kind in (*CLI_KINDS, *API_KINDS):
        part = update.get(kind)
        if not isinstance(part, dict):
            continue
        for k in cfg[kind]:
            v = part.get(k)
            if k == 'key':
                if isinstance(v, str) and v.strip():
                    cfg[kind]['key'] = v.strip()
                elif part.get('clearKey'):
                    cfg[kind]['key'] = ''
            elif isinstance(v, str):
                cfg[kind][k] = v.strip()
    save_config(cfg)
    return cfg


# ---------- finding the CLIs from a folder ----------
def find_exes(kind, folder, max_depth=5, max_entries=60000):
    """Executables for kind under folder, shallowest and best-named first (e.g. an npm folder's codex.cmd before
    the codex.exe deep in its node_modules)."""
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise AIError(f'フォルダが見つかりません: {folder}')
    names = EXE_NAMES[kind]
    found, seen, queue = [], 0, [(root, 0)]
    while queue:
        d, depth = queue.pop(0)
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            seen += 1
            if seen > max_entries:
                queue = []
                break
            try:
                if e.is_file() and e.name.lower() in names and (WINDOWS or os.access(e.path, os.X_OK)):
                    found.append((depth, names.index(e.name.lower()), e.path))
                elif e.is_dir(follow_symlinks=False) and depth < max_depth and e.name not in SKIP_DIRS:
                    queue.append((Path(e.path), depth + 1))
            except OSError:
                pass
    return [p for _, _, p in sorted(found)]


def common_folders(kind):
    """Where the installers usually put kind: PATH, the npm global folder, ~/.local/bin, the Claude Code app folder."""
    home, out = Path.home(), []
    for name in EXE_NAMES[kind]:
        hit = shutil.which(name.rsplit('.', 1)[0] if WINDOWS else name)
        if hit:
            out.append(str(Path(hit).parent))
    if WINDOWS and os.environ.get('APPDATA'):
        out.append(str(Path(os.environ['APPDATA']) / 'npm'))
    out += [str(home / '.local' / 'bin'), str(home / '.claude' / 'local'), str(home / '.npm-global' / 'bin'), '/usr/local/bin', '/opt/homebrew/bin']
    return [f for i, f in enumerate(out) if f not in out[:i] and Path(f).is_dir()]


def exe_version(path):
    try:
        r = subprocess.run([path, '--version'], capture_output=True, timeout=40, creationflags=NO_WINDOW)
        text = (r.stdout or r.stderr).decode('utf-8', 'replace').strip().splitlines()
        return text[0][:120] if text and r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def detect(kind, folder=None):
    """Search folder (or, without one, the usual places) and return the candidates with their versions."""
    if kind not in CLI_KINDS:
        raise AIError('CLIの種類が正しくありません')
    folders = [folder] if folder else common_folders(kind)
    exes = []
    for f in folders:
        for p in find_exes(kind, f):
            if p not in exes:
                exes.append(p)
    return {'folders': folders, 'candidates': [{'path': p, 'version': exe_version(p)} for p in exes[:6]]}


def pick_folder(title):
    """The OS folder dialog, in a child Python so Tk owns its own main thread. Returns '' when cancelled."""
    code = ('import sys,tkinter as tk,tkinter.filedialog as fd\n'
            'r=tk.Tk();r.withdraw();r.attributes("-topmost",True)\n'
            'print(fd.askdirectory(title=sys.argv[1],mustexist=True) or "")')
    try:
        r = subprocess.run([sys.executable, '-c', code, title], capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        raise AIError('フォルダの選択が時間切れになりました')
    if r.returncode != 0:
        raise AIError('フォルダを選ぶ画面を開けません（tkinter がない？）。パスを直接入力してね')
    return r.stdout.decode('utf-8', 'replace').strip()


# ---------- asking ----------
def _cli_exe(cfg, kind):
    exe = cfg[kind]['exe']
    if exe and Path(exe).is_file():
        return exe
    if cfg[kind]['folder']:
        hits = find_exes(kind, cfg[kind]['folder'])
        if hits:
            return hits[0]
    name = 'Claude Code' if kind == 'claude-code' else 'Codex'
    raise AIError(f'{name} の実行ファイルが見つかりません。「AIの設定」でインストール先のフォルダを選んでね')


def _safe_model(m):
    m = (m or '').strip()
    if m and not all(c.isalnum() or c in '.-_:/[]' for c in m):
        raise AIError('モデル名に使えない文字があります')
    return m


def _run_cli(cmd, text, cwd, timeout):
    try:
        r = subprocess.run(cmd, input=text.encode('utf-8'), capture_output=True, cwd=cwd, timeout=timeout, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise AIError(f'{timeout}秒以内に返事がありませんでした')
    except OSError as e:
        raise AIError(f'起動できません: {e}')
    return r.returncode, r.stdout.decode('utf-8', 'replace'), r.stderr.decode('utf-8', 'replace')


def _err_tail(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return lines[-1][:300] if lines else ''


def ask_claude_code(cfg, system, prompt, timeout):
    exe, model = _cli_exe(cfg, 'claude-code'), _safe_model(cfg['claude-code']['model'])
    cmd = [exe, '-p', '--output-format', 'json', '--no-session-persistence'] + (['--model', model] if model else [])
    with tempfile.TemporaryDirectory() as tmp:
        code, out, err = _run_cli(cmd, f'{system}\n\n{prompt}', tmp, timeout)
    try:
        j = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise AIError('Claude Code の返事を読めません: ' + (_err_tail(err) or _err_tail(out) or f'exit {code}'))
    if j.get('is_error') or code != 0:
        raise AIError('Claude Code: ' + str(j.get('result') or _err_tail(err) or f'exit {code}')[:300])
    return j.get('result', ''), model or 'Claude Code の既定'


def ask_codex(cfg, system, prompt, timeout):
    exe, model = _cli_exe(cfg, 'codex'), _safe_model(cfg['codex']['model'])
    with tempfile.TemporaryDirectory() as tmp:
        last = Path(tmp) / 'last.txt'
        cmd = [exe, 'exec', '--skip-git-repo-check', '--ephemeral', '--sandbox', 'read-only', '--color', 'never',
               '-C', tmp, '-o', str(last)] + (['-m', model] if model else []) + ['-']
        code, out, err = _run_cli(cmd, f'{system}\n\n{prompt}', tmp, timeout)
        text = last.read_text('utf-8', 'replace') if last.is_file() else ''
    if code != 0 or not text.strip():
        raise AIError('Codex: ' + (_err_tail(err) or _err_tail(out) or f'exit {code}'))
    return text, model or 'Codex の既定'


def _http(url, body=None, headers=None, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8') if body is not None else None,
                                 headers={'Content-Type': 'application/json', **(headers or {})}, method='POST' if body is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')
        try:
            j = json.loads(detail)
            detail = (j.get('error') or {}).get('message') if isinstance(j.get('error'), dict) else j.get('error') or detail
        except ValueError:
            pass
        raise AIError(f'HTTP {e.code}: {str(detail)[:300]}')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AIError(f'{url} に接続できません: {getattr(e, "reason", e)}')
    except ValueError:
        raise AIError('返事がJSONではありません')


def _api_key(cfg, kind):
    key = cfg[kind].get('key') or env_key(kind)
    if not key:
        raise AIError('APIキーが設定されていません（「AIの設定」で入力）')
    return key


def ask_anthropic(cfg, system, prompt, timeout):
    model = _safe_model(cfg['anthropic']['model']) or DEFAULTS['anthropic']['model']
    j = _http('https://api.anthropic.com/v1/messages',
              {'model': model, 'max_tokens': 8000, 'system': system, 'messages': [{'role': 'user', 'content': prompt}]},
              {'x-api-key': _api_key(cfg, 'anthropic'), 'anthropic-version': '2023-06-01'}, timeout)
    return ''.join(b.get('text', '') for b in j.get('content', []) if b.get('type') == 'text'), model


def _openai_base(cfg, kind):
    base = (cfg[kind].get('base') or DEFAULTS[kind]['base']).rstrip('/')
    if not base.startswith(('http://', 'https://')):
        raise AIError('URLは http:// か https:// で始めてね')
    return base


def _openai_headers(cfg, kind):
    if kind != 'openai':
        return {}
    key = cfg['openai'].get('key') or env_key('openai')
    # A self-hosted OpenAI-compatible server may not need a key
    return {'Authorization': f'Bearer {key}'} if key else {}


LOCAL_HINT = {'lmstudio': '（LM Studio の Developer タブでサーバーを起動するか、lms server start）',
              'ollama': '（Ollama を起動してね）'}


def list_models(kind, cfg=None):
    try:
        return _list_models(kind, cfg or load_config())
    except AIError as e:
        raise AIError(str(e) + (LOCAL_HINT.get(kind, '') if '接続できません' in str(e) else ''))


def _list_models(kind, cfg):
    if kind == 'anthropic':
        j = _http('https://api.anthropic.com/v1/models?limit=100', None,
                  {'x-api-key': _api_key(cfg, 'anthropic'), 'anthropic-version': '2023-06-01'}, 30)
    elif kind in API_KINDS:
        j = _http(_openai_base(cfg, kind) + '/models', None, _openai_headers(cfg, kind), 30)
    else:
        raise AIError('この方式ではモデル一覧を取れません（空欄なら既定のモデル）')
    return sorted({m.get('id') for m in j.get('data', []) if m.get('id')})


def ask_openai(cfg, kind, system, prompt, timeout):
    model = _safe_model(cfg[kind]['model'])
    if not model:
        # Local servers: use whatever model is loaded first
        if kind == 'openai':
            raise AIError('モデル名を入れてね（「一覧」で取得できる）')
        models = list_models(kind, cfg)
        if not models:
            raise AIError('モデルが読み込まれていません（LM Studio / Ollama でモデルを読み込んでね）')
        model = models[0]
    try:
        j = _http(_openai_base(cfg, kind) + '/chat/completions',
                  {'model': model, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}]},
                  _openai_headers(cfg, kind), timeout)
    except AIError as e:
        raise AIError(str(e) + (LOCAL_HINT.get(kind, '') if '接続できません' in str(e) else ''))
    try:
        return j['choices'][0]['message']['content'] or '', model
    except (KeyError, IndexError, TypeError):
        raise AIError('返事の形式が想定と違います')


def ask(system, prompt, provider=None, timeout=420):
    cfg = load_config()
    kind = provider if provider in (*CLI_KINDS, *API_KINDS) else cfg['provider']
    t0 = time.time()
    if kind == 'claude-code':
        text, model = ask_claude_code(cfg, system, prompt, timeout)
    elif kind == 'codex':
        text, model = ask_codex(cfg, system, prompt, timeout)
    elif kind == 'anthropic':
        text, model = ask_anthropic(cfg, system, prompt, timeout)
    else:
        text, model = ask_openai(cfg, kind, system, prompt, timeout)
    return {'text': text, 'provider': kind, 'model': model, 'seconds': round(time.time() - t0, 1)}
