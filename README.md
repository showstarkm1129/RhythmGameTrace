# Arcaea Touch Studio — Codex引き継ぎ資料

作成日: 2026-10-19 / 現状: 実機のタッチ取得・マルチタッチ復元・動画重ね合わせプロトタイプまで動作確認済み。

## 0. Codexへの依頼（そのまま使える）

このフォルダはWindowsからUSB接続した非脱獄iPadの実タッチ入力を記録し、Arcaeaの譜面動画と比較するプロジェクトの引き継ぎ一式です。READMEの確立事項を最初に読んでください。既存の `viewer/arcaea_touch_video_sync.html` と `source/*.py` を読んで、動く機能を維持したまま改善を進めてください。まず起動と入力データの読み込み・座標の描画・動画の同期を確認し、**実測データと推測、ログ時計と動画時計、接触IDと指そのものを厳密に区別**してください。コード中にあった `/mnt/data/` 固定パスはリポジトリ相対パスに修正済みです（§5「再ビルド」）。YouTube埋め込みが動かない環境でもローカル動画で使える状態を守ってください。作業の前に変更対象と確認方法を簡潔に説明し、変更後に検証してください。

## 1. 目的・制約・ユーザーの意図

- 目的: リズムゲーム **Arcaea** の実プレイにおける、指ごとのタッチ開始・移動・終了・時刻・座標を復元し、譜面動画に重ねてズレやミスの原因を比較・分析する。
- 実験譜面: **Cataclysm Cry (INS)**。プレイ時スコアはユーザー申告で **約9,790,000点**、体感で約3回大きく崩れた（どの時点かは未同定）。YouTubeチャートビュー: https://www.youtube.com/watch?v=WzXKOxGYWBU
- 実機: **iPad13,6 / iPadOS 26.6.2**（会話中の自己申告）。**非脱獄、Developer Mode有効、USBでWindowsに接続**。アクセスガイド（Guided Access）ONでも実験済み。
- PC: Windows PowerShell、Python 3.14、**pymobiledevice3 11.36.0**（会話中の環境申告）。一部環境ではPython `requests` 依存警告が出るが、タッチ座標の取得成功とは別件。
- プレイアプリを改造・フック・脱獄せず、OS診断ログから読み取る手段を確立した。
- ユーザーはこの先のバグ修正、画角や時間同期の微調整、開発作業をCodexに移管する方針。

## 2. 最重要：実機で成功した取得経路

### 取得コマンド（PowerShell）

```powershell
py -m pymobiledevice3 developer dvt oslog --pid 70 --format json |
  Select-String -SimpleMatch '"message": "Digitizer token: 0xD8DEEB04;' |
  ForEach-Object { $_.Line } |
  Set-Content -Path "$HOME\Desktop\touch_compact.jsonl" -Encoding utf8
```

**重要**: この環境のオプションは `--format`。**`--output-format` は存在しない**（過去に誤案内あり）。

- `70` は **当時の `backboardd` のPID**。再起動などで変わる可能性がある。必要なら `py -m pymobiledevice3 developer dvt oslog --help` で実装に合ったオプションを確認する。
- `0xD8DEEB04` は**実験当時にArcaea向けとみられた変換済み座標系のDigitizerトークン**。環境やアプリによって固定かどうか未検証。ハードコードの長期使用には注意。
- JSONL 1行=1OSログレコード。`message` に `subevents` 全文が入るため、**複数指の座標が省略されない**。
- 出力は `Set-Content` 直行で、膨大な標準出力を表示しない。前段 `Select-String` では1行JSONをフィルタし、元の複数行テキストログで同様のフィルタを行わない（`subevents` が途中切れする）。
- これはOSログ取得後にホストでフィルタする方式であり、**iPad側のログ生成/転送自体を軽くできるとは限らない**。長時間の収集性能は要評価。
- 収集終了は Ctrl+C。**接触中に収集を止めると終了イベントを取り逃がす**可能性がある。実験では接触終了後に数秒以上待って終了を推奨。

### その他の試行

- `py -m pymobiledevice3 developer dvt core-profile-session parse-live -e HID_HandleReport -e HID_DispatchHIDEvent -e HID_PostEvent` はHID動作の発生時間を見られるが、**座標・指IDの取得には不十分**だった。接触中に一部イベントが約120Hzで並ぶが、それらの引数を指IDやXYと誤認しない。
- `oslog` の通常テキスト出力で `com.apple.BackBoard/TouchEvents` に座標とIDを発見。**テキストの `Select-String` は複数指 `subevents` を途中で切りやすい**ため、JSON形式に移行した。
- `com.apple.Multitouch.PalmRejection` の `number_of_fingers` 等も見つかったが、採用済みの主要データは `BackBoard` の `Digitizer token`。

## 3. JSONL実データの構造

`data/touch_compact.jsonl` のレコード例（実測ログから抜粋）:

```json
{"timestamp":"2026-10-19T15:52:30.842303","pid":70,"message_type":"Debug","subsystem":"com.apple.BackBoard","category":"TouchEvents","image_name":"backboardd","message":"Digitizer token: 0xD8DEEB04; down; sgp; behavior: foreground; gen: 95740; subevents: [path: 1; {604.5, 687.5}; down; touchID: 0xDE5; {604.5, 687.5}]"}
```

`message` の構成要素:

- `Digitizer token: 0xD8DEEB04` : 今回採用した座標系のログ識別。
- `down`, `move`, `up`, `range (out)` : 接触状態を含むイベント。先頭の全体状態と各subeventの指状態を混同しない。
- `gen` : 一連の更新世代らしき番号。*正式な定義は未検証*。
- `subevents: [path: n; {x, y}; 状態; touchID: 0x....; ... ]` : 1イベントに複数の`path` / `touchID` / 座標が含まれ得る。**各subeventを個別に抽出すること**。
- `touchID` は1回の接触を追跡するための識別子。永続的な「同じ人物の指」を表すものではない。
- 既存のPython抽出正規表現:

```python
import re
rx = re.compile(r'path:\s*(\d+);\s*\{([\d.]+),\s*([\d.]+)\};\s*(.*?)touchID:\s*(0x[\da-fA-F]+)')
```

`phase`: subeventごとに `down` / `move` / `up` / `range_out` / `unchanged` を判定。継続中の複数の指をイベントが再掲するため、**各行数や各subevent数をタップ回数として数えない**。touchIDごとに1つの接触を作る。

時間はOSログ `timestamp` をISO形式として読み、`t0=最初のタッチログ時刻` との差を取っている。**譜面開始時刻、最初のノーツ、録画開始のいずれとも一致保証はない**。一部の最初のタッチはゲーム開始前の無駄なタップかもしれない。

## 4. 実プレイで解析済みの数値（`touch_compact.jsonl`）

| 項目 | 実測 |
|---|---:|
| JSONLレコード数 / 座標つきイベント数 | 15,758 |
| subeventとして抽出した座標サンプル | 27,069 |
| 一意なtouchID | 1,143 |
| 記録の先頭 | 2026-10-19 15:52:30.842303 |
| 記録の末尾 | 2026-10-19 15:55:23.128261 |
| 最初から最後まで | 172.285958秒 |
| 移動中など連続時の典型的な記録間隔 | 約8.3ms（概ね120Hz） |
| 1イベント内の最大接触ID数 | 8 |
| 終端が未確認のtouchID | 1つ（`0x1257`） |

- 8個の同時接触IDの存在はログで確認できるが、**すべて実際の独立した意図的な指操作とは未確定**。掌などの誤接触、イベント表現も検証すること。
- 最終ログ付近: `touchID: 0x1257` が 171.743〜172.286秒に `move` で続いたまま打ち切り。最後のX,Yは `(495,641)`。`touchID: 0x125B` の `up` も最終イベント付近に現れる。終盤の入力が録画に対して不足して見える問題をユーザーが報告している。
- **ログ終端が欠けた理由は未特定**（フィルタリング、DVTの出力負荷、取得終了時刻、ログ発生そのもの、画角・同期のズレ等）。約6秒程度に見えるという前回の観察は同期の暫定値に依存するため確定的な欠落秒数ではない。
- 上記タイムスタンプはログ出力時刻であり、**センサーの物理的なサンプリング時刻と厳密に一致する保証はない**。

## 5. 生成された解析・ビューアの構成

```text
ArcaeaCodexHandoff/
  README.md                        <- このファイル。まず読む
  viewer/
    arcaea_touch_video_sync.html  <- 主アプリ・タッチ埋め込み済み単一HTML
    start_viewer.bat              <- Windows起動用
    launch_viewer.py              <- localhost HTTPサーバー（既定127.0.0.1:8765）
    使い方.txt
  source/
    touchlog.py                   <- JSONLパーサ（共通）。ビューア内ローダー parseTouchLog() と同じ規則
    viewer_template.html          <- ビューアのテンプレート（HTML/CSS/JSを直接編集する場所）
    analyze_touches.py            <- 実ログの統計解析（既定 data/touch_compact.jsonl、引数で別ファイル）
    build_touch_replay.py         <- JSONL -> data/ のCSV・統計図 + build/ の単体再生HTML
    build_video_sync.py           <- JSONL + テンプレート -> viewer/ の動画比較HTML一式 + build/ のZIP
  build/                          <- 生成物（単体再生HTML・ZIP。git管理外）
  data/
    touch_compact.jsonl           <- 実プレイの一次ログ。最重要。
    cataclysm_touch_samples.csv  <- 1座標サンプル1行
    cataclysm_touch_contacts.csv <- 1接触ID1行の要約
    cataclysm_touch_positions.png
    cataclysm_touch_timeline.png
    multitouch_test.txt           <- 途中で複数指サブイベントが切れている対照データ
    touch_coordinates.txt         <- 1本指スライドのテキストログ
  refs/
    studio_preview.png
```

ビューアはHTML/CSS/素のJavaScript、Canvas 2Dで描画。専用のJSフレームワークは不使用。ビルド時に1つのJSONLを既定データ（`EMBEDDED`）として埋め込むほか、**ビューア上で別のJSONLを開ける**（「0. タッチデータ」またはドラッグ＆ドロップ）。開いたデータはブラウザの localStorage に保存され、「埋め込みデータに戻す」で既定に戻る。JSONL→接触の変換規則は `source/touchlog.py` とテンプレート内の `parseTouchLog()` で同一（同じJSONLから同じ `strokes` になることを確認済み）。

### 表示機能

- HTML内のタッチ軌跡・指ID・同時接触数・接触開始累計。
- タイムライン: シークバーと同じ時間軸に、タッチ開始の密度・動画の範囲・基準点（T/V）・現在位置を表示。表示幅（全体/30秒/10秒/2秒）を切り替えでき、30秒以下では接触開始を1本ずつ描く。クリック/ドラッグで移動。
- 動画の指定はローカルファイル(MP4等) またはYouTubeのURL/ID。YouTube「検索」は検索結果を別タブ表示→URL貼り付け方式。埋め込み失敗事例あり。**ローカルに保存済みの動画ファイルでは実際に重ね合わせ再生成功**。
- ローカル動画は `<video>` + `URL.createObjectURL()` で読み込む。YouTubeはiframe API。HTML単体のほか `start_viewer.bat` でローカルHTTPサーバーを起動。
- 時間同期は「動画基準点」と「タッチ基準点」を1組指定してオフセットを確定、±1/10/100msなどで微調整、同期設定JSON保存復元。どちらの基準点を先に設定してもよく、設定時に自動で「切り離し」状態（動画とタッチを別々に操作）になる。「連動に戻す」でオフセットを保ったまま再連動。
- 連動中でも、動画の範囲外（曲前のタップや動画終了後）のタッチをシークで表示できる。範囲外から再生すると、タッチだけ進んで動画の範囲に入った時点で動画も再生される。
- 現在の対応式は **`touchTimeMs = videoTimeMs + offsetMs`**。録画で見えた例: 動画6.301秒 ≒ タッチ7.737秒 ⇒ `offsetMs ≒ +1436`（未精密調整）。
- **画面canvas固定解像度 `1194×834`**。動画は `object-fit: contain` で表示される。「iPad画面を動画の映像範囲に自動で合わせる」（既定ON）で、iPad座標系を動画の黒帯を除いた映像範囲に写す（YouTubeは16:9と仮定）。その上に倍率（25〜200%、0.1%刻み）/ずれ（px）の手動補正を数値入力または「✥ 位置合わせ」モード（ドラッグ・ホイール・矢印キー）で重ねる。自動合わせの項目がない古い設定JSON/保存状態は、互換のため自動合わせOFFとして読む。
- `STROKES`のXYはJS容量と精度のための**2倍固定小数表現**。描画関数 `getPosition(point)` の `/2` は意図的な復号であり、**それ自体が倍率バグというわけではない**。CSVと元ログのXYはこの1/2を適用済み。
- その他: 再生速度、シーク、実際の記録時刻への1記録ずつのステップ、前/次のタッチ開始へのジャンプ、動画だけの±1コマ（ローカル動画は再生中にフレーム間隔を計測）、軌跡の長さ/不透明度/表示範囲調整、作業状態のブラウザ自動保存。時間精度のUI刻みが1msでもYouTube動画やWindows時計で1msの物理同期を保証するわけではない。

### 起動

```powershell
# viewer/ 内で
.\start_viewer.bat
# または Python を使って
py launch_viewer.py
# 既定: http://127.0.0.1:8765/arcaea_touch_video_sync.html
```

### 再ビルド

`viewer/` 内の4ファイル（HTML / `launch_viewer.py` / `start_viewer.bat` / `使い方.txt`）は生成物。手で編集せず、ビューアは `source/viewer_template.html`、付属ファイルは `source/build_video_sync.py` を修正して再生成する。パスはスクリプト位置からの相対なので、カレントディレクトリはどこでもよい。2つのスクリプトは独立しており、`build_video_sync.py` は標準ライブラリだけで動く。PNG出力のみ `matplotlib` が必要（無ければPNGだけ省略）。

```powershell
py source\build_video_sync.py     # viewer/* , build/arcaea_touch_video_sync.zip（既定データ: data/touch_compact.jsonl）
py source\build_touch_replay.py   # data/cataclysm_touch_*.csv / *.png, build/cataclysm_touch_replay.html
# 別のプレイを使う場合
py source\build_video_sync.py data\other_play.jsonl --title "曲名 (FTR)"
py source\build_touch_replay.py data\other_play.jsonl --prefix other --title "曲名 (FTR)"
```

- 出力は改行LF固定（`newline='\n'`）。CSV 2つは従来版とバイト一致を確認済み。PNGはmatplotlibのバージョン差でバイト列が変わるが、内容は同一。
- 複数の Digitizer トークンが混ざったJSONLでは、最も多いトークンだけを使う（Python・ビューアとも）。JSONとして読めない行は数えて読み飛ばす。
- 動画ファイル（`*.mp4` 等）と `build/` は `.gitignore` 対象。

## 6. 既知の課題・未検証事項（優先順位はCodexとユーザーで決める）

1. **座標の空間校正**: object-fit の黒帯を除く自動合わせと、倍率/ずれの手動校正（可視化・自動保存あり）は実装済み。動画内のUIや画角の違いによる回転/射影のずれ、多点校正は未対応。
2. **開始時刻と時計同期**: 最初のタップには事前操作が混入。譜面開始や動画の任意フレームと対応する基準点が必要。既存はオフセット1点校正。2点同期で再生速度差/ドリフトの存在を評価できるが、ドリフトがあると決めつけない。
3. **終盤の記録途切れ**: 端に到達する前に接触ログが終了したように見える。ログ自体の終端・`up`不足・表示終了位置・同期誤差を切り分ける。収集中のカウントや`Ctrl+C`前のフラッシュなども検討する。
4. **YouTube再生**: ユーザー環境で直接埋め込みの読込が失敗。埋め込み禁止、ブラウザ、参照元、API制約など可能性が複数。まずローカル動画を確実に維持。動画の無断ダウンロード手段を前提にしない。
5. **UIとデータ構造**: ビューアでのJSONL読込と作業状態の自動保存は実装済み。非常に長い記録では localStorage の容量を超えて保存できない場合がある（その場合は次回JSONLを選び直す）。ストリーム処理やプロジェクト単位の保存は未対応。
6. **精度と譜面判定**: OS診断ログに時刻揺らぎ/欠落があり得る。ログの点間隔と動画フレームレートは別。ユーザーのスコア979万/崩れ3回は事前情報で、ログのみからどのノーツをミスしたかは特定していない。チャートビューの「LOST」「0点」は動画側の描画であり**ユーザー実プレイの判定ではない**。
7. **信頼性**: Apple内部の`TouchEvents`診断出力は公開された安定APIではない。iPadOS更新などで出力内容・表示・トークンやPIDが変わり得る。

## 7. Codexが最初に確認すべき点

- `viewer/start_viewer.bat` で起動することと、Windowsのブラウザでローカル動画が再生できること。
- `data/touch_compact.jsonl` には15,758レコード、連続した接触IDが1,143件あること。Pythonの解析スクリプトは絶対パスを直して使用すること。
- **時刻・座標の定義と符号**: `touchTime=videoTime+offset`、canvas座標1194×834、`STROKES`のXYは×2格納。ここを把握してから補正コードを変更する。
- 既存の単一HTMLを**再構築可能なソース**に分離するなら、生成ビルドスクリプトも引き継いで差分を管理すること。
- タッチごとの `down` / `up` とイベントごとの `move` / `unchanged` を誤集計しないこと。最終ID `0x1257` に終端がないことを無理に`up`扱いしない。
- OSログはアプリ内部の判定結果そのものではない。実ゲーム中のスコアやコンボ・判定との紐付けは別計測が必要。

## 8. 添付されない大容量・外部参照

- ユーザー撮影の**ビューア再生画面の録画**: `レコーディング 2026-10-19 170222.mp4`（約208MB）。ZIPには容量を抑えるため未同梱。必要なら元動画を別途提供してもらう。
- 実際に使用した**ローカルの譜面動画ファイル**はこの引き継ぎZIPに含まない。ユーザーのPC側の保存ファイルをビューアで選択する。
- 外部のチャートビュー参考動画URL: https://www.youtube.com/watch?v=WzXKOxGYWBU
- 以前のDVT/OSログ全量やsysdiagnoseは経緯資料として別途存在するが、基本実装の開始には不要。

## 9. 実績の解釈についての注意

この資料は**実際に観測されたタッチ入力を可視化する土台が成立した**ことを引き継ぐためのもの。実際のArcaeaプレイが上手かったかどうか、譜面との完全一致、ハードウェア本来の全サンプリング取得は、まだ検証できていない。まず「座標と時間の校正」を済ませてから、判定アルゴリズムやミス推定に進む。
