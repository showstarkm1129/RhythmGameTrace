import argparse,csv,datetime,json,sys
from html import escape
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/'data';BUILD=ROOT/'build'
sys.path.insert(0,str(Path(__file__).resolve().parent))
import touchlog

ap=argparse.ArgumentParser(description='JSONL -> data/<prefix>_touch_*.csv / *.png and build/<prefix>_touch_replay.html')
ap.add_argument('jsonl',nargs='?',type=Path,default=DATA/'touch_compact.jsonl',help='oslog JSONL capture (default: data/touch_compact.jsonl)')
ap.add_argument('--prefix',default='cataclysm',help='output file prefix (default: cataclysm)')
ap.add_argument('--title',default='Cataclysm Cry (INS)',help='chart / page title')
args=ap.parse_args()
BUILD.mkdir(exist_ok=True)
log=touchlog.load(args.jsonl)
zero=log['zero'];strokes=log['strokes'];lastms=log['duration'];all_events=log['events']

rows=[]
for ident,ss in log['per_id'].items():
    for t,x,y,phase,path in ss:
        rows.append((round(t/1000,3),str(zero+datetime.timedelta(milliseconds=t)),hex(ident),path,x/2,y/2,touchlog.PHASES[phase]))
rows.sort(key=lambda a:(a[0],a[2]))

with open(DATA/f'{args.prefix}_touch_samples.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f);w.writerow(['time_from_first_touch_s','log_timestamp','touch_id','path','x','y','state']);w.writerows(rows)
with open(DATA/f'{args.prefix}_touch_contacts.csv','w',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f);w.writerow(['touch_id','start_s','end_s','duration_ms','x_start','y_start','x_end','y_end','samples','has_release'])
    for i,st,en,points in strokes:
        w.writerow([hex(i),round(st/1000,3),round(en/1000,3),en-st,points[0][1]/2,points[0][2]/2,points[-1][1]/2,points[-1][2]/2,len(points),points[-1][3] in (3,4)])

# detailed temporal counts in 10s bins
taps=touchlog.onset_bins(strokes,lastms);bins=len(taps)
outputs=[DATA/f'{args.prefix}_touch_samples.csv',DATA/f'{args.prefix}_touch_contacts.csv']

# overview charts (matplotlib is optional: without it only the CSVs and the replay HTML are written)
try:
    import matplotlib
except ImportError:
    matplotlib=None
    print('matplotlib not installed: skipped PNG charts (py -m pip install matplotlib)')
if matplotlib:
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(13.8,3.4),layout='constrained')
    xs=[i*10 for i in range(bins)]
    ax.bar(xs,taps,align='edge',width=9.5)
    ax.set(xlim=(0,lastms/1000),xlabel='Seconds since first logged touch',ylabel='New touch IDs / 10 s',title=f'{args.title} - touch onset density')
    ax.grid(axis='y',alpha=.2)
    fig.savefig(DATA/f'{args.prefix}_touch_timeline.png',dpi=160)
    plt.close(fig)

    fig,ax=plt.subplots(figsize=(12.3,7.2),layout='constrained')
    starts=sorted(((st/1000,p[0][1]/2,p[0][2]/2) for _,st,en,p in strokes))
    sc=ax.scatter([p[1] for p in starts],[p[2] for p in starts],c=[p[0] for p in starts],s=6,alpha=.68,cmap='viridis')
    ax.set(xlim=(0,1194),ylim=(834,0),xlabel='X (logged logical units)',ylabel='Y (logged logical units)',title=f'{args.title} - {len(strokes):,} logged touch-down positions')
    fig.colorbar(sc,ax=ax,label='Seconds since first touch')
    ax.set_aspect('equal', adjustable='box')
    ax.grid(alpha=.12)
    fig.savefig(DATA/f'{args.prefix}_touch_positions.png',dpi=160)
    plt.close(fig)
    outputs+=[DATA/f'{args.prefix}_touch_timeline.png',DATA/f'{args.prefix}_touch_positions.png']

# HTML app using fully self-contained canvas rendering
strokes_json=json.dumps(strokes,separators=(',',':'))
histo_json=json.dumps(taps)

html_template='''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>__TITLE__ — iPad Touch Replay</title>
<style>
:root{color-scheme:dark;--bg:#0a101b;--panel:#131e30;--muted:#9faabd;--line:#314159;--fg:#f2f7ff;--accent:#65d5e7}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(140deg,#0b1424,#0d1727 65%,#111c2e);font:14px system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--fg)}
main{max-width:1180px;margin:auto;padding:26px 18px 48px}header{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;flex-wrap:wrap;margin-bottom:18px}
h1{font-size:25px;margin:0 0 8px;font-weight:750;letter-spacing:.01em}p{color:var(--muted);line-height:1.65;margin:5px 0}.pill{font-size:12px;letter-spacing:.05em;color:#9ee8e9;border:1px solid #356777;border-radius:99px;padding:7px 12px}
.panel{border:1px solid #2a3a51;border-radius:16px;background:rgba(18,30,47,.92);padding:16px;margin-bottom:16px;box-shadow:0 10px 28px #0002}
.stage-wrap{position:relative;background:#070e17;border:1px solid #3b4f66;border-radius:11px;overflow:hidden;min-height:220px}canvas#stage{display:block;width:100%;aspect-ratio:1194/834;max-height:680px;object-fit:contain;cursor:crosshair}
.stage-label{position:absolute;pointer-events:none;left:12px;top:12px;font-size:12px;letter-spacing:.06em;color:#a9c5d1;background:#091523c7;border:1px solid #2d4056;padding:5px 8px;border-radius:5px}
.controls{display:flex;flex-wrap:wrap;gap:11px;align-items:center;margin-bottom:14px}
button,select{background:#21354a;color:var(--fg);border:1px solid #426079;padding:9px 12px;border-radius:8px;cursor:pointer}button:hover{background:#30526e}button.primary{background:#176c83;border-color:#2f9bb7;font-weight:700;min-width:86px}
input[type=range]{width:100%;accent-color:#65d5e7;cursor:pointer}.time{font:700 20px ui-monospace,SFMono-Regular,Consolas,monospace;font-variant-numeric:tabular-nums;min-width:180px}
.timeline{width:100%;height:110px;display:block;cursor:pointer;background:#101b2b;border-radius:8px;border:1px solid #2e4158}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:12px 0}.stat{background:#17253a;border:1px solid #283f5a;border-radius:10px;padding:11px}.stat span{display:block;font-size:11px;color:#a5b3c7;margin-bottom:4px}.stat strong{font-size:19px;font-variant-numeric:tabular-nums}
.flex{display:flex;align-items:center;gap:14px;flex-wrap:wrap}.small{font-size:12px;color:var(--muted)}.flex label{color:#d8e5ef}
.note{border-left:3px solid #4ba9af;padding-left:13px;color:#b5c3d4}
a{color:#85d8ea}kbd{background:#233649;padding:2px 5px;border-radius:3px}
@media(max-width:650px){main{padding:17px 10px}h1{font-size:21px}.panel{padding:11px}.stats{grid-template-columns:repeat(2,1fr)}.time{font-size:18px;min-width:135px}.stage-wrap{min-height:160px}}
</style></head><body><main>
<header><div><div class="pill" style="display:inline-block;margin-bottom:12px">実測データの再生</div><h1>__TITLE__ — Touch Replay</h1><p>iPad / BackBoard のタッチログから復元。実際の譜面映像ではなく、指の動きの記録です。</p></div><div class="pill">座標 1194 × 834 基準（推定）</div></header>
<div class="panel"><div class="controls"><button id="play" class="primary">▶ 再生</button><button id="rewind" title="10秒戻る">↶ 10秒</button><button id="forward" title="10秒進む">10秒 ↷</button><div class="time" id="time">00:00.000 / 02:52.286</div><label>速度 <select id="speed"><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option selected value="1">1×</option><option value="2">2×</option><option value="4">4×</option></select></label><label>軌跡 <select id="trail"><option value="0.2">0.2秒</option><option value="0.6" selected>0.6秒</option><option value="1.5">1.5秒</option><option value="3">3秒</option></select></label></div>
<input type="range" id="seek" min="0" max="__DURATION__" step="8" value="0" aria-label="時間移動"/>
<div class="stats"><div class="stat"><span>同時に接触中</span><strong id="active">0本</strong></div><div class="stat"><span>累計タッチ開始</span><strong id="total">0回</strong></div><div class="stat"><span>表示中のタッチID</span><strong id="ids" style="font-size:13px;word-break:break-word">—</strong></div><div class="stat"><span>最新の座標 (X, Y)</span><strong id="point" style="font-size:13px">—</strong></div></div>
<div class="stage-wrap"><canvas id="stage" width="1194" height="834"></canvas><div class="stage-label">iPad画面相当 / 左上が (0, 0)</div></div></div>
<div class="panel"><div class="flex" style="justify-content:space-between;margin-bottom:10px"><strong>タッチ開始の密度</strong><span class="small">クリックでその時刻へ移動 · 10秒ごとの接触開始件数</span></div><canvas class="timeline" id="timeline"></canvas><p class="small">0:00 = 最初に記録されたタッチ。曲の0:00とは必ずしも一致しません。静止中の経路は最後の位置を保持し、ログがない区間は補間していません。</p></div>
<div class="panel"><p><strong>表示の読み方</strong>：丸は現在接触している指、線は直近の移動軌跡。色はタッチIDごとに区別しています。短いタップも一瞬表示します。</p><p class="note">このビューアはiPadOSの診断ログから復元したものです。タッチのサンプリング欠落・出力遅延・座標系の違いはあり得ます。実際のノーツ判定時刻や譜面上の要求位置を示すものではありません。</p><p class="small">キーボード: <kbd>Space</kbd> 再生/停止 · <kbd>←</kbd>/<kbd>→</kbd> 1秒移動 · ファイルは単独でオフライン動作します。</p></div>
</main>
<script>
const STROKES=__STROKES__;
const BINS=__HIST__;
const DURATION=__DURATION__;
const seek=document.getElementById('seek'),playBtn=document.getElementById('play'),speedEl=document.getElementById('speed'),trailEl=document.getElementById('trail');
const stage=document.getElementById('stage'),ctx=stage.getContext('2d');
const timeline=document.getElementById('timeline'),tx=timeline.getContext('2d');
let playing=false,t=0,started=0,startingT=0,shown=-1;
function fmt(ms){let m=Math.floor(ms/60000),s=Math.floor(ms%60000/1000),rest=Math.round(ms%1000);return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0')+'.'+String(rest).padStart(3,'0')}
function color(id){let hue=(id*137.508)%360;return `hsl(${hue.toFixed(1)} 85% 66%)`}
function upperIndex(points,ms){let a=0,b=points.length;while(a<b){let mid=(a+b)>>1;if(points[mid][0]<=ms)a=mid+1;else b=mid}return a;}
function drawBackground(){ctx.fillStyle='#08111c';ctx.fillRect(0,0,1194,834);ctx.save();
for(let i=0;i<4;i++){ctx.fillStyle=i%2===0?'#122336':'#0d1b2b';ctx.fillRect(i*298.5,0,298.5,834)}
ctx.strokeStyle='#516880';ctx.globalAlpha=.3;ctx.lineWidth=2;
for(let i=0;i<=4;i++){ctx.beginPath();ctx.moveTo(i*298.5,0);ctx.lineTo(i*298.5,834);ctx.stroke()}
for(let i=0;i<=4;i++){ctx.beginPath();ctx.moveTo(0,i*208.5);ctx.lineTo(1194,i*208.5);ctx.stroke()}
ctx.font='20px system-ui';ctx.fillStyle='#a1b9cc';ctx.globalAlpha=.5;for(let i=0;i<4;i++)ctx.fillText('LANE '+(i+1),i*298.5+16,812);ctx.restore();}
function draw(){drawBackground();let trailms=1000*Number(trailEl.value),active=[],newHits=0,latest=null;
for(let s of STROKES){let [id,from,to,points]=s;if(from<=t)newHits++;if(from>t||to<t-trailms)continue;
let endIndex=upperIndex(points,t);if(!endIndex)continue;
let colorString=color(id);let ix=Math.max(0,upperIndex(points,t-trailms)-1);let startedPath=false;
ctx.save();ctx.strokeStyle=colorString;ctx.lineWidth=6;ctx.lineJoin='round';ctx.lineCap='round';ctx.globalAlpha=.65;ctx.beginPath();
for(let p=ix;p<endIndex;p++){let x=points[p][1]/2,y=points[p][2]/2;if(!startedPath){ctx.moveTo(x,y);startedPath=true}else ctx.lineTo(x,y)}
if(startedPath)ctx.stroke();ctx.restore();
const last=points[endIndex-1];const pos=[last[1]/2,last[2]/2];
if(t>=from&&t<=to&&last[3]!==3&&last[3]!==4){active.push([id,pos,colorString]);latest=pos}
else if(t-to<110&&t>=to&&last[3]===3){ctx.save();ctx.globalAlpha=Math.max(0,.55*(1-(t-to)/110));ctx.strokeStyle=colorString;ctx.lineWidth=5;ctx.beginPath();ctx.arc(pos[0],pos[1],22,0,Math.PI*2);ctx.stroke();ctx.restore()}
}
for(let [id,pos,c] of active){let x=pos[0],y=pos[1];ctx.save();ctx.shadowBlur=22;ctx.shadowColor=c;ctx.fillStyle=c;ctx.strokeStyle='#f3ffff';ctx.lineWidth=3;ctx.beginPath();ctx.arc(x,y,13,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.restore();ctx.fillStyle='#fff';ctx.font='bold 17px ui-monospace,monospace';ctx.fillText('0x'+id.toString(16).toUpperCase(),x+18,y-14);}
let timeval=fmt(t);if(shown!==Math.floor(t/24)){document.getElementById('time').textContent=timeval+' / '+fmt(DURATION);document.getElementById('active').textContent=active.length+'本';document.getElementById('total').textContent=newHits.toLocaleString('ja-JP')+'回';document.getElementById('ids').textContent=active.length?active.map(x=>'0x'+x[0].toString(16).toUpperCase()).join(', '):'—';document.getElementById('point').textContent=latest?latest.map(x=>x.toFixed(1)).join(', '):'—';shown=Math.floor(t/24)}
seek.value=String(Math.round(t/8)*8);drawTimeline();}
function drawTimeline(){let w=timeline.width,h=timeline.height;tx.clearRect(0,0,w,h);tx.fillStyle='#121d2e';tx.fillRect(0,0,w,h);let max=Math.max(...BINS);let marg=26,top=12,base=h-26,plot=w-marg*2,barW=plot/BINS.length;
for(let i=0;i<BINS.length;i++){let bh=(BINS[i]/max)*(base-top);tx.fillStyle='#4d99b4';tx.fillRect(marg+i*barW+1,base-bh,Math.max(1,barW-2),bh)}
tx.strokeStyle='#86e8f0';tx.lineWidth=2;let x=marg+Math.min(1,t/DURATION)*plot;tx.beginPath();tx.moveTo(x,7);tx.lineTo(x,base+5);tx.stroke();tx.fillStyle='#c5d3df';tx.font='12px system-ui';let totalSec=DURATION/1000;for(let tick=0;tick<=totalSec;tick+=30){let p=marg+(tick/totalSec)*plot;tx.fillText(Math.floor(tick/60)+':'+String(tick%60).padStart(2,'0'),Math.max(1,p-12),h-6)} }
function resizeTimeline(){let b=timeline.getBoundingClientRect();timeline.width=Math.max(300,Math.round(b.width*devicePixelRatio));timeline.height=Math.round(110*devicePixelRatio);tx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);timeline.width=Math.round(b.width*devicePixelRatio);timeline.height=Math.round(110*devicePixelRatio);tx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);}
function drawTimelineResponsive(){let b=timeline.getBoundingClientRect(),scale=window.devicePixelRatio||1;timeline.width=Math.round(b.width*scale);timeline.height=Math.round(b.height*scale);tx.setTransform(scale,0,0,scale,0,0);let W=b.width,H=b.height;tx.clearRect(0,0,W,H);tx.fillStyle='#111d2e';tx.fillRect(0,0,W,H);let m=25,base=H-25,plot=W-2*m,max=Math.max(...BINS),bw=plot/BINS.length;
for(let i=0;i<BINS.length;i++){let dh=BINS[i]/max*(H-43);tx.fillStyle='#498bac';tx.fillRect(m+i*bw+1,base-dh,Math.max(1,bw-2),dh)}
let xx=m+(t/DURATION)*plot;tx.strokeStyle='#7ee3ef';tx.lineWidth=2;tx.beginPath();tx.moveTo(xx,4);tx.lineTo(xx,base+2);tx.stroke();tx.fillStyle='#a8c0d0';tx.font='11px system-ui';for(let v=0;v<=Math.floor(DURATION/30000);v++){let tick=v*30000,x=m+tick/DURATION*plot;tx.fillText(`${Math.floor(tick/60000)}:${String(Math.floor((tick%60000)/1000)).padStart(2,'0')}`,x-10,H-7)} }
// override using responsive measurements; no external scripts required
function drawTimeline(){drawTimelineResponsive()}
function setT(v){t=Math.min(DURATION,Math.max(0,Math.round(v)));if(playing){started=performance.now();startingT=t}draw()}
function tick(now){if(!playing)return;t=Math.min(DURATION,startingT+(now-started)*Number(speedEl.value));draw();if(t>=DURATION){playing=false;playBtn.textContent='▶ 再生';return}requestAnimationFrame(tick)}
function toggle(){playing=!playing;playBtn.textContent=playing?'Ⅱ 一時停止':'▶ 再生';if(playing){if(t>=DURATION)t=0;startingT=t;started=performance.now();requestAnimationFrame(tick)}}
playBtn.onclick=toggle;document.getElementById('rewind').onclick=()=>setT(t-10000);document.getElementById('forward').onclick=()=>setT(t+10000);
seek.addEventListener('input',()=>setT(Number(seek.value)));
trailEl.onchange=draw;speedEl.onchange=()=>{if(playing){startingT=t;started=performance.now()}};
timeline.addEventListener('click',e=>{let b=timeline.getBoundingClientRect(),frac=Math.max(0,Math.min(1,(e.clientX-b.left-25)/(b.width-50)));setT(frac*DURATION)});
window.addEventListener('keydown',e=>{if(e.target.tagName==='SELECT'||e.target.tagName==='INPUT')return;if(e.code==='Space'){e.preventDefault();toggle()}if(e.code==='ArrowRight')setT(t+1000);if(e.code==='ArrowLeft')setT(t-1000)});
window.addEventListener('resize',draw);draw();
</script></body></html>'''
html=html_template.replace('__STROKES__',strokes_json).replace('__HIST__',histo_json).replace('__DURATION__',str(lastms)).replace('__TITLE__',escape(args.title))
# ensure no unintended file path under template
out=BUILD/f'{args.prefix}_touch_replay.html'
out.write_text(html,encoding='utf-8',newline='\n')
print('done',{'duration_sec':lastms/1000,'touches':len(strokes),'records':len(all_events),'sample_records':len(rows),'skipped_lines':log['skipped'],'bins':taps,'files':[(p.name,p.stat().st_size) for p in [out,*outputs]]})
