# video_trj_integration_lite — 架構

> v0.2。精簡自 `video_trj_integration`（多相機 + StoneX CSV 版本）。使用說明見 [README.md](README.md)。

## 1. 範圍

| 需求 | 做法 |
|---|---|
| 軌跡格式 | GPX 1.0/1.1、NMEA 0183（RMC / GGA / GNS / ZDA，任何 talker：GP/GN/GL/GA/BD） |
| 影片支援 | **PyAV（內含 FFmpeg）** 為主要解碼器，OpenCV 為選配備援；支援 VFR、HEVC、10-bit、旋轉 metadata |
| 單一影片 | 一次一支影片 + 一份軌跡，無多相機同步 |
| 時間對準 | 對時影格 + 該格時鐘時間 + Offset 微調；CLI 另有雙錨點（時鐘漂移修正）與 metadata 模式 |
| 輸出方式 | 固定頻率（images/s）或固定距離（每 N 公尺） |

輸出：抽出的影像、每張的內插 WGS84 座標（CSV），並可寫入 EXIF GPS（Metashape、COLMAP 等可直接讀取）。

不在範圍內：多相機、StoneX CSV、影片內嵌 GPS（GoPro GPMF、DJI SRT）；軌跡讀取器採註冊制，日後可擴充。

## 2. 核心原則

1. **統一時間基準**：絕對時間一律為 UTC epoch 秒（`float`）；影片時間一律為 **PTS 秒數**，不以「影格編號 / fps」推算，VFR 才會正確。
2. **處理邏輯與 UI 分離**：`vtl` 不 import tkinter，全部可由 CLI 與 unittest 驅動。
3. **時區只出現在邊界**：使用者輸入與檔名才套用時區；GPX、NMEA 一律視為 UTC。
4. **不覆寫前次結果**：每次匯出建立新的 `vtl_YYYYMMDD_HHMMSS[_n]/`。
5. **背景執行緒不碰 widget**：UI 狀態在主執行緒讀取後傳入，結果經 `App.q` 回報。

## 3. 目錄結構

```
video_trj_integration_lite/
├─ main.py                  # 入口：無參數 / 檔案路徑 → UI；子指令 → CLI
├─ vtl/                     # 核心（無 GUI）
│  ├─ models.py             # dataclasses：TrackPoint, Track, VideoInfo, Alignment, ExportConfig…；狀態常數
│  ├─ track/
│  │  ├─ __init__.py        # load_track()：依副檔名 / 內容嗅探分派（READERS 註冊表）
│  │  ├─ gpx.py             # namespace 無關的 GPX 解析
│  │  ├─ nmea.py            # checksum、同 epoch 句型合併、跨午夜
│  │  └─ interp.py          # bracket 內插、gap 判斷、haversine、速度/航向
│  ├─ video/
│  │  ├─ __init__.py        # open_video()：PyAV 失敗才退回 OpenCV
│  │  ├─ base.py            # VideoSource Protocol、旋轉工具
│  │  ├─ pyav_source.py     # 主要後端
│  │  └─ opencv_source.py   # 備援後端
│  ├─ align.py              # PTS↔UTC 建立方式（單/雙錨點、metadata）、每支影片的設定記憶
│  ├─ timefmt.py            # 時間 / 時區 / offset 解析與所有格式化
│  ├─ sampler.py            # 匯出時刻表：時間或距離取樣 + 篩選
│  ├─ export.py             # 循序解碼 → 寫圖 → CSV / JSON
│  ├─ geotag.py             # EXIF GPS + DateTimeOriginal（piexif）
│  └─ cli.py                # probe / track-info / export
├─ vtl_ui/                  # Tkinter 桌面介面
│  ├─ app.py                # 主視窗、拖放、對時、匯出
│  ├─ workers.py            # 預覽解碼執行緒（最新請求優先）
│  ├─ speed_plot.py         # 速度曲線 Canvas
│  └─ compat.py             # 平台差異：字型、開啟資料夾
├─ tests/                   # unittest；合成影片與軌跡在測試時產生
├─ packaging/
│  ├─ vtl.spec              # PyInstaller 設定（UI 與 CLI 兩個單檔 EXE）
│  ├─ vtl_cli.py            # CLI EXE 的入口
│  └─ build_exe.bat         # 產生 dist\VideoTrjLite.exe 與 dist\vtl.exe
├─ pyproject.toml           # 套件資訊、依賴、entry points（vtl / vtl-ui）
├─ requirements.txt         # 執行依賴
├─ requirements-dev.txt     # + PyInstaller、pyflakes
└─ run_ui.bat / run_ui.sh   # 從原始碼啟動 UI（Windows / Linux）
```

依賴方向（單向）：`vtl_ui → vtl.export → {vtl.sampler, vtl.video, vtl.geotag} → {vtl.align, vtl.track, vtl.timefmt} → vtl.models`

## 4. 資料模型（`models.py`）

- `TrackPoint(t, lat, lon, alt, speed, course, fix, hdop, sats, segment)`：`t` 為 UTC；`segment` 為 GPX trkseg 編號，跨段不內插。
- `Track(points, source, path, warnings)`：依時間排序、去重。
- `VideoInfo(path, duration, start_pts, end_pts, avg_fps, is_vfr, width, height, rotation, codec, creation_time, backend)`。
- `Alignment(anchor_pts, anchor_utc, scale=1)`：`utc = anchor_utc + (pts − anchor_pts) × scale`，`shifted(s)` 加上 offset。
- `ExportConfig`：`rate`、`distance_m`（>0 時改為距離取樣）、`in_pts` / `out_pts`、`max_gap_s`、`min_move_m`、格式、EXIF、時區。
- 狀態：`ok`、`outside_coverage`、`gnss_gap`、`below_min_movement`、`frame_unavailable`、`no_track`。

## 5. 軌跡

- **格式判斷**：副檔名優先（`.gpx` / `.nmea` `.nma` `.log` `.txt`），不明時嗅探內容。新增格式只需在 `READERS` 註冊。
- **GPX**：以 local-name 比對（不受 namespace 影響）；讀 `trk/trkseg/trkpt`，沒有則退回 `rte` / `wpt`；沒有時間的點捨棄並記入 warnings。
- **NMEA**：容忍行首前綴、驗證 checksum；同一時間戳的 RMC / GGA / GNS 合併；日期優先序 RMC → ZDA → 使用者提供（UI 會跳窗詢問）；時間回捲時日期 +1。
- **內插**：`bisect` 找前後點線性內插；超出範圍 → `outside_coverage`；前後點間隔 > `max_gap_s` 或跨 segment → `gnss_gap`。速度/航向來源沒有時由前後點推算。

## 6. 影片後端

PyAV 為主要後端（wheel 內含完整 FFmpeg，VFR 每格有真實 PTS，可讀旋轉與 `creation_time`）；OpenCV 只在 PyAV 打不開時使用，且為選配。

- **預覽**：`frame_at(pts)` 精準 seek 並快取。`_decode_from` 遇到 seek 落在目標之後（MPEG-TS 常見）會往前退 1、2、4… 秒重試。
- **匯出**：`iter_raw` 循序解碼，不逐張 seek；只把要輸出的影格轉成 RGB，存檔交給執行緒池。
- **旋轉**：讀 display matrix（退回 `rotate` tag），存檔前套用。

## 7. 時間對準

UI 的模型：

```
畫面時間 = 對時影格的時間 + (播放位置 − 對時影格) + Offset
```

- **對時影格**預設為第一格；時間自動帶入影片 metadata 的 `creation_time`，沒有時用軌跡起點。使用者通常改成拍到時鐘等已知時間的畫面，並輸入該格時間。
- 「自動帶入」與「使用者輸入」分開追蹤：自動值會隨時區 / 對時影格更新；使用者輸入的值不會被覆蓋，換對時影格時會依目前對準換算。
- **Offset** 用來微調（±0.1 / ±1 s，或輸入 `+8:00:00` 修正時區設錯）。
- **對準輔助**：預覽下方即時顯示目前影格的時間、座標、速度；速度曲線上的紅線是目前畫面，藍色區塊是影片涵蓋範圍，點擊可跳轉。
- 設定以「路徑 | 大小 | 修改時間」為 key 存在 `%APPDATA%\video_trj_integration_lite\alignments.json`（Linux：`$XDG_CONFIG_HOME` 或 `~/.config` 之下）。

CLI 另提供 `--use-metadata`、單錨點與雙錨點（`--anchor2-*`，計算 `scale`；偏離 1 超過 0.1% 時警示）。

## 8. 匯出管線

```
Track ─┐
       ├─► sampler.build_schedule() ─► [SampleSlot(pts, utc, status, fix)]
Align ─┘     範圍 = 影片 ∩ 軌跡（映射到 PTS）∩ in/out
             │
             ▼
export.run_export(): 循序解碼 → 寫圖（+EXIF）→ frames.csv / skipped.csv / alignment.json / run_summary.json
```

取樣規則：

- **時間模式**：`t_k = grid0 + k / rate`。`grid0` 為 `ExportConfig.grid_start`（UI 設為「開始輸出影格」）；未設定時通過對時影格（CLI 的 `--in-pts` 只裁切範圍）。
- **距離模式**（`distance_m > 0`）：以約每一格影片為候選，沿軌跡累計移動距離，每達 N 公尺保留一格（不是直線距離）。第一個可用影格與 GPS 斷訊後的第一格一定保留；每段斷訊只在 `skipped.csv` 記一筆。
- **最小位移**（時間模式）：與上一張保留影像的 haversine 距離小於門檻則略過；斷訊後重設基準。
- 匯出時，每個時刻取 PTS 最接近的影格，容差 `max(2 × 影格時長, 0.05 s)`，超出記為 `frame_unavailable`。寫出的時間、座標、EXIF 與檔名一律以**實際寫出的影格 PTS** 重新計算（不是排程時刻），所以 `frames.csv` 的 `video_pts`、`utc_time` 與座標彼此一致。距離模式的候選格點不一定是真實影格（VFR），兩個時刻落在同一格時後者記為 `duplicate_frame`。

輸出結構與欄位見 README。

## 9. 打包

PyInstaller，`packaging/vtl.spec` 產生兩個單檔 EXE：

- `VideoTrjLite.exe`：無主控台的 UI（`main.py`）；帶檔案路徑（把檔案拖到 EXE 上）會開 UI 並載入檔案。
- `vtl.exe`：主控台 CLI（`packaging/vtl_cli.py`，不含 tkinter）。視窗程式不會讓 cmd / PowerShell 等待、也拿不到 exit code，所以腳本要用這個；UI 版帶子指令時仍會 `AttachConsole` 印出結果，但只適合手動試用。
- 兩者都收錄 `av` 內含的 FFmpeg DLL（`collect_dynamic_libs("av")`）；UI 版另含 `tkinterdnd2` 的 tkdnd 擴充。
- 排除 `cv2` 與 `vtl.video.opencv_source`（選配備援，可省約 100 MB）。

## 10. 測試

`tests/` 以 unittest 撰寫，測試時用 PyAV 產生合成影片：亮度編碼時間戳，含 VFR、旋轉、MPEG-TS。GPX/NMEA 也在測試時產生，repo 不放二進位檔。

- `test_track.py`：GPX / NMEA 變體、內插、gap。
- `test_time_align.py`：時間 / 時區 / offset 解析、錨點。
- `test_export.py`：時刻表（時間、距離、開始影格）、端到端匯出、EXIF、取消、不覆寫。
- `test_ui.py`：以程式操作 Tk 介面（沒有顯示器時略過）。
