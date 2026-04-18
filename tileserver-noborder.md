# 日本政府見解に準拠した独立型ベクタータイルサーバ構築手順 (Toner-en 版)

OpenStreetMap.jp の **Toner-en スタイル** (`maptiler-toner-en`) の視覚仕様を踏襲しつつ、**OSM.jp サーバへの一切のランタイム依存を排除**した自前ベクタータイルサーバを **Ubuntu Server 24.04.4 LTS** に構築する手順。

**前提条件:**

| 項目 | 値 |
|---|---|
| OS | Ubuntu Server 24.04.4 LTS |
| ログインユーザ | `shimotsuki` (sudo 権限あり) |
| ビルド作業ディレクトリ | `/work/shimotsuki/planetiler` |
| tileserver-gl データディレクトリ | `/home/shimotsuki/tileserver-gl/data` |
| nginx 静的ルート | `/home/shimotsuki/http/tile.hogehoge.com` |
| tileserver-gl 実行ユーザ | `shimotsuki:shimotsuki` |
| nginx 実行ユーザ | `shimotsuki:shimotsuki` (標準の `www-data` から変更) |
| 公開ドメイン | `tile.hogehoge.com` (A/AAAA レコードが当サーバを指す) |
| 公開ポート | TCP 80, 443 (ファイアウォール開放済み) |
| リバースプロキシ | nginx (必須) |
| SSL 証明書 | Let's Encrypt / apt の certbot + `python3-certbot-nginx` |

> **ディレクトリ分離の意図**: `/work/shimotsuki/planetiler` はビルド作業専用 (PBF、tmp、中間 MBTiles)。配信系は一切 `/work` を参照せず、`/home/shimotsuki/tileserver-gl/data` と `/home/shimotsuki/http/tile.hogehoge.com` のみで完結する。リビルド時はビルド成果物 (MBTiles) をクロスファイルシステム対応のアトミック置換で配信側に反映する (§11 参照)。

---

## 1. 設計方針

### 1.1 配信する 2 つのスタイル

OSM.jp が提供する以下 2 スタイルを自前化し、**両方とも tile.hogehoge.com から配信**する。

| スタイル | ベース配色 | 情報量 | 用途 |
|---|---|---|---|
| **Maptiler-Toner-en** | 白背景・黒描画の高コントラスト | 少 | 白黒印刷、データ可視化の背景、目に優しい下地 |
| **Maptiler-Basic-en** | 水色・ベージュ・グレーのカラー | 多 | 一般用途、道路網・POI・細街路あり |

両者ともテキスト要素は `{name:latin}` で統一され、ラベルは英語 (または `name:en` が無いものは空で描画されない、§8 参照)。

### 1.2 境界線と国名ラベルの扱い

境界線の描画制御は以下 3 つの処理を組み合わせる。**処理順と効力の順を明確にするため**、描画パイプラインに沿って記述する:

```
[処理前]  元スタイルのレイヤー:
           ├─ admin_country_z0-4 / boundary_country_z0-4  (admin_level<=2 専用, 低ズーム)
           ├─ admin_country_z5-  / boundary_country_z5-   (admin_level<=2 専用, 高ズーム)
           └─ admin_sub / boundary_state                  (admin_level=4 等の州/県境)

[§1.2.1] boundary source-layer の全レイヤーに maritime!=1 を AND 合成
[§1.2.2] admin_country_* / boundary_country_* を削除
         admin_sub / boundary_state の filter に admin_level=2, 3 を合流
[§1.2.4] 合流後の admin_sub / boundary_state にも maritime!=1 を AND 合成 (本命)
[§1.2.3] place.class=country の maxzoom を 5 に設定

[処理後]  描画に使われるレイヤー:
           └─ admin_sub / boundary_state  (admin_level=2,3,4 すべてを同一スタイルで描画
                                           かつ maritime=1 は除外される)
```

#### 1.2.1 海上国境線の非表示 (過渡段階)

OSM.jp の 2 スタイルはいずれも**外部オーバレイ** (`hoppo` / `takeshima` vector タイル) に依存して、海上国境を白塗りで物理的に覆い隠す設計。本構成では OSM.jp ランタイム依存を排除したいため、別の手法が必要。

両スタイルの色使いの違い:

| 要素 | Toner-en | Maptiler-Basic-en |
|---|---|---|
| background | `#ffffff` (白) | `hsl(47,26%,88%)` (薄ベージュ) |
| water | `rgba(0,0,0,1)` (黒) | `hsl(205,56%,73%)` (水色) |
| admin_country | `rgba(0,0,0,1)` (黒) | `hsl(0,0%,60%)` (中間グレー) |

Toner-en は **「水と国境線が同色 (黒) のため海上国境が自然に不可視」** という偶然の性質があるが、Maptiler-Basic-en は水色とグレーで色が違うため、海上国境線が明瞭に見えてしまう。

**採用する汎用解法**: OpenMapTiles スキーマの `boundary` レイヤーは `maritime` (0 or 1) フィールドを持ち、海上国境線は `maritime=1` でマークされる ([OpenMapTiles schema](https://openmaptiles.org/schema/))。これは planetiler-openmaptiles も同仕様で出力する。MapLibre style の `boundary` source-layer を参照する全レイヤーに `["!=", "maritime", 1]` フィルターを AND で追加する。

> **この時点では過渡段階**: §1.2.2 で国境専用レイヤー (`admin_country_*` / `boundary_country_*`) 自体が削除されるため、それらにかけた maritime フィルターは結果的に不要となる。しかし処理順序としては「中立化前に boundary 全レイヤーへ一律ガード」→「中立化で一部削除・一部合流」という流れにすることで、合流時に既に maritime ガードが入っている状態を作り、§1.2.4 での重複チェックが不要になる (patch_style.py の `add_filter_clause()` は冪等)。

#### 1.2.2 陸上国境線の「行政区画線」化

海上国境線を消しても、陸上の国境線は残る。日本の場合は陸続きで他国と接しないため問題にならないが、配信対象が日本国外 (特に係争地を抱える国々 — インド・パキスタン・中国のカシミール地方、タイ・カンボジア国境、エチオピア・エリトリア、トルコ・シリアなど) を含む場合、**陸上国境の引き方自体が論争を招く**。OSM はこれらを **実効支配に基づく de facto マッピング** で描く方針だが、実効支配と国際承認は必ずしも一致しない。

そこで、**陸上国境 (`admin_level=2`) と地域圏 (`admin_level=3`) を、都道府県/州界 (`admin_level=4`) と同一スタイルで描画する**という方針を採用する。具体的には:

- 国境専用レイヤー (元スタイルでは `admin_country_*` / `boundary_country_*`) を**削除**
- 州/県境を描くレイヤー (`admin_sub` / `boundary_state`) のフィルター条件に `admin_level=2, 3` を合流

これにより、拡大時には「これは国境線ではなく、単なる行政区画の境界」という立場を取れる。係争地の具体的な国境をどう引くかの議論から距離を置ける。

#### 1.2.3 国名ラベルの低ズーム限定表示

境界線で国を区別しなくなると、「どこが何国か」が地図上から消えてしまう。完全に消すと実用性が下がるため、**国名ラベルのみ低ズーム (z0–4) で表示**する方針を取る。具体的には `place.class=country` の symbol レイヤーに `"maxzoom": 5` を設定。

挙動の整理:

| ズーム域 | 陸上国境線 (admin_level=2) | 県境/州界 (admin_level=4) | 国名ラベル |
|---|---|---|---|
| z0–4 | 非描画 (§1.2.2 で低ズーム用の国境レイヤー削除済み) | 元々描かれない | **表示** (世界地図に国名だけ) |
| z5 以上 | **県境と同一スタイル**で描画 | 描画 | **非表示** |

低ズームでは国名だけが浮かび「ここが日本、ここが中国」は分かる。拡大すると全ての境界が同じ破線で描かれ、国の形は図示されなくなる。

#### 1.2.4 合流後レイヤーでの海上国境線非表示 (本命フィルター)

§1.2.2 で `admin_level=2, 3` を合流させた先の `admin_sub` / `boundary_state` レイヤーには、**最終的な描画対象となる way に対して `["!=", "maritime", 1]` フィルターが掛かっている状態**が必要。これが**本構成で実際に効く海上境界線非表示フィルター**である。

具体例: 根室 ↔ 北方領土間の海上に引かれた `admin_level=2, maritime=1` の way は、§1.2.2 の合流により `admin_sub` / `boundary_state` レイヤーの描画対象に入ってしまう。ここで `maritime!=1` フィルターが掛かっていないと、都道府県界と同じスタイルで海上に線が引かれる。竹島周辺、尖閣諸島周辺も同じ。

そのため、`patch_style.py` は以下 2 段階で maritime ガードを付与する:

1. **§1.2.1 の段階**: `boundary` source-layer を参照する**全レイヤー**に maritime ガード (広く一律に)
2. **§1.2.2 の中立化の後、追加で一度**: `admin_sub` / `boundary_state` に maritime ガード (合流で新たに範囲が広がったため再確認)

実装上は `add_filter_clause()` が冪等 (既に入っていれば重複追加しない) なので、単に両方の段階で呼んでおけば安全。

> **一般論として海上に行政区画線が引かれることは稀**: 瀬戸内海の県境など一部例外を除き、県・州レベルの境界は沿岸までで止まる。そのため `admin_sub` / `boundary_state` への maritime フィルターは実用上ほぼ不可視の挙動になるが、合流により取り込んだ `admin_level=2` の海上線を消す用途で実効的に機能する。

### 1.3 問題表記の抹消は「バッファ除去」だけで完結する

- 北方領土・竹島の**陸地 + 2km バッファ**内のあらゆる feature (land, POI, 道路, 建物, 境界 way) を osmium でソース PBF から除去
- 除去後のエリアはレンダリングで「背景色のまま」となり、島嶼は**ラベルもアイコンも無い無地のシルエット**として残る
    - Toner-en: 海 (黒) の中に白シルエット
    - Maptiler-Basic-en: 海 (水色) の中に薄ベージュのシルエット
- 島を横断していた境界 way は除去時に穴の内部が抜けるため、島面上に国境線が出現しない
- 島外 (開けた海) を走る境界 way は §1.2 の `maritime` フィルターにより非表示

結論: **OSM.jp の `hoppo` / `takeshima` タイルは一切不要**。両スタイルから該当ソースとレイヤーを削除する。

### 1.4 独立化のためのセットアップ時ダウンロード物と公開インフラ

セットアップ時のみ 1 度だけ外部から取得し、以降のタイル配信は完全ローカルで動作:

| リソース | 取得元 | 頻度 |
|---|---|---|
| 全球 OSM PBF | planet.openstreetmap.org / Geofabrik | 初回 + 週次 (rebuild) |
| 北方領土・竹島ポリゴン (バッファ生成用) | **OSM.jp の MVT** を `scripts/fetch_osmjp.py` で `$REPO/geojson/{hoppo,takeshima}.geojson` に展開 (本リポジトリには同梱せず、`.gitignore`) | **初回セットアップ時のみ**。地理的境界が更新された場合のみ手動リフレッシュ |
| Planetiler jar | GitHub Releases | 初回のみ |
| 海岸線・水域ポリゴン、Natural Earth | Planetiler `--download` が自動取得 | 初回 + 週次 (rebuild) |
| Maptiler-Toner style.json | [openmaptiles/maptiler-toner-gl-style](https://github.com/openmaptiles/maptiler-toner-gl-style) `v1.0` (BSD 3-Clause + CC-BY 4.0) | 初回のみ |
| Maptiler-Toner sprite × 4 | 同リポジトリ `gh-pages` (github.io) | 初回のみ |
| Maptiler-Basic style.json | [openmaptiles/maptiler-basic-gl-style](https://github.com/openmaptiles/maptiler-basic-gl-style) `v1.10` (BSD 3-Clause + CC-BY 4.0) | 初回のみ |
| Maptiler-Basic sprite × 4 | 同リポジトリ `gh-pages` (github.io) | 初回のみ |
| フォント (Noto Sans, Nunito 各種ウェイト) | [openmaptiles/fonts](https://github.com/openmaptiles/fonts) (OFL) | 初回のみ |
| SSL 証明書 | Let's Encrypt (ACME HTTP-01) | 初回 + 60 日ごとに自動更新 |

> **OSM.jp 依存は初回 + 任意の手動リフレッシュ時のみ**:
> - 週次 rebuild ループからは OSM.jp 完全消失 (`scripts/rebuild.sh` は `$REPO/geojson/*.geojson` を入力に使うのみで、自分では取得しない)
> - 初回セットアップで一度だけ `scripts/fetch_osmjp.py` を叩いて `$REPO/geojson/` に GeoJSON を保存 (§5)。以降のリフレッシュは年単位の手動運用 (上流の島嶼座標自体がほぼ不変のため)
> - **本リポジトリには OSM.jp 由来データを同梱しない** (`.gitignore` で `geojson/*.geojson` を除外)。これにより、リポジトリの配布物は一律 GPL-2.0 で完結し、CC-BY-SA 2.0 / ODbL の share-alike 義務は**操作者が fetch した瞬間にその操作者の手元のファイルにのみ発生**する形になる
>
> ライセンスへの影響は §13 参照。OSM.jp の CC-BY-SA 2.0 は本構成の配信物には**一切伝播しなくなる** (操作者が手元で fetch した `geojson/` 配下のデータを除く)。

Let's Encrypt は**定常的な外部依存**となる (更新に 60 日周期の接続が必須) が、タイル配信自体は依然としてローカル完結。証明書を手動管理したい場合は `certbot certonly --manual` や私設 CA に切替可能。

migu1c-regular / migu2m-regular は日本語専用フォントだが、両スタイルとも `{name:latin}` を使うため日本語字形は描画されない。よってこれらの参照はスタイル改変で Noto Sans に差し替える。

### 1.5 公開構成

```
外部クライアント
    │ HTTPS (tile.hogehoge.com)
    ▼
[nginx]  (:443) ← certbot が /etc/letsencrypt/live/tile.hogehoge.com に証明書を配置
    │  ・TLS 終端
    │  ・proxy_cache tiles (20 GB, 7 日)
    │  ・CORS / セキュリティヘッダ
    │ HTTP (loopback)
    ▼
[tileserver-gl]  (127.0.0.1:8080)  ← 外部非公開
    │
    ▼
openmaptiles.mbtiles  (共通)
styles/maptiler-toner-en/  +  sprites/maptiler-toner-en/
styles/maptiler-basic-en/  +  sprites/maptiler-basic-en/
fonts/  (共通)
```

### 1.6 処理フロー

```
[初回セットアップ時のみ]
OSM.jp hoppo/takeshima MVT (z=10)
   │ scripts/fetch_osmjp.py で polygon 取得
   ▼
$REPO/geojson/{hoppo,takeshima}.geojson  (gitignore 済み、リポジトリには非収録)

[以降の週次 rebuild ループ]
$REPO/geojson/{hoppo,takeshima}.geojson
      │ buffer_clip.py (測地 2km バッファ + 世界外周 .poly)
      ▼
   world_minus_islands.poly
      │
[global.osm.pbf] ──┤ osmium extract (-p)
                   ▼
            [clipped.osm.pbf]
                   │ Planetiler (OpenMapTiles profile, --languages=en,ja,ko,ru)
                   ▼
            [final.mbtiles]
                   │
            tileserver-gl + 改変 Toner-en スタイル

# OSM.jp は週次ループに登場しない。リフレッシュ時のみ
# scripts/fetch_osmjp.py を手動実行する。
```

---

## 2. 環境要件

### 2.1 ハードウェア

#### RAM / CPU / 処理時間

全球 (`planet.osm.pbf`、~85 GB) を前提とする。

| RAM | CPU | 処理時間 (8 core) |
|---|---|---|
| 128 GB (または `--storage=mmap` で 32 GB) | 8 core 以上 | 3〜8 時間 |

#### SSD 容量の内訳

| 種別 | 項目 | サイズ |
|---|---|---:|
| 恒久 | OS (Ubuntu 24.04) + 依存パッケージ | ~8 GB |
| 恒久 | Planetiler jar / tileserver-gl (`node_modules`) / Python venv | ~1 GB |
| 恒久 | Toner-en 資産 (style, sprite, fonts) | ~100 MB |
| サイクル毎 | ソース PBF (`planet.osm.pbf`) | ~85 GB |
| サイクル毎 | `clipped.osm.pbf` | ~85 GB |
| サイクル毎 | Natural Earth + land/water polygons | ~1 GB |
| ビルド中 tmp | Planetiler node map + feature store | **~200–300 GB** |
| 恒久 | 現行 MBTiles (稼働中) | ~100 GB |
| ビルド中 | 新 MBTiles (差替え前) | ~100 GB |
| 恒久 | nginx タイルキャッシュ (`/var/cache/nginx/tiles`) | ~20 GB |
| 恒久 | systemd journal, rebuild ログ | ~2–5 GB |

#### 推奨 SSD 構成

| シナリオ | ビルド中ピーク | 定常状態 | 推奨 SSD |
|---|---:|---:|---|
| 単一ボリューム | ~600–700 GB | ~250 GB | **2 TB NVMe** |
| 分離構成 | 同上 | 同上 | **500 GB NVMe (配信系) + 1 TB NVMe (tmp 専用)** |

- **NVMe 必須**。Planetiler の node map は random I/O が支配的で、SATA SSD では処理時間が倍以上になる
- **`/work` だけを大容量 SSD にマウント**すれば OS パーティションは小さくて済む (例: OS 64 GB + `/work` 2 TB)
- **tmpdir を別ボリュームに逃がすことも可能**: `java -jar ... --tmpdir=/mnt/fast-nvme/tmp` で Planetiler の中間ファイルだけ別ディスクへ。配信系ディスクをコンパクトに保てる
- **`--storage=ram`** (RAM に 128 GB+ 積める場合) を使うと tmp ディスク使用量が 200–300 GB → ~100 GB まで下がる

### 2.2 ディレクトリ

ログインユーザ `shimotsuki` が全ての作業を実施する前提。ビルド作業用 (`/work/shimotsuki/planetiler`) と、配信データ用 (`/home/shimotsuki/tileserver-gl/data`)、nginx 静的ルート用 (`/home/shimotsuki/http/tile.hogehoge.com`) の 3 ツリーを用意する。スクリプト本体は本リポジトリに集約済み (§2.3) のため、`/work/.../scripts` は作成しない。

```bash
# ビルド作業用 (大容量 SSD を想定)
sudo mkdir -p /work/shimotsuki/planetiler/{src,pbf,geojson,mbtiles,build,venv}
sudo chown -R shimotsuki:shimotsuki /work/shimotsuki

# tileserver-gl の配信データ用 (両スタイル分の styles/sprites サブディレクトリ)
mkdir -p /home/shimotsuki/tileserver-gl/data/{styles,sprites,fonts}
mkdir -p /home/shimotsuki/tileserver-gl/data/styles/{maptiler-toner-en,maptiler-basic-en}
mkdir -p /home/shimotsuki/tileserver-gl/data/sprites/{maptiler-toner-en,maptiler-basic-en}

# nginx 静的ルート用 (デモページや静的資産)
mkdir -p /home/shimotsuki/http/tile.hogehoge.com

# 以後の作業カレントはビルド用
cd /work/shimotsuki/planetiler
```

### 2.3 本リポジトリの配置と `$REPO` 変数

本書で参照する Python / Bash スクリプトと、systemd ユニット・nginx 設定・sudoers・tileserver-gl `config.json`・デモ HTML は、すべて本リポジトリに収録されている。以降の手順は環境変数 `REPO` が本リポジトリのチェックアウト先を指す前提で書かれている (既定値: `/home/shimotsuki/tileserver-noborder`)。

```bash
# クローン (例: ホームディレクトリ直下)
cd /home/shimotsuki
git clone https://github.com/<owner>/tileserver-noborder.git
export REPO=/home/shimotsuki/tileserver-noborder
```

リポジトリの主要レイアウト:

```
$REPO/
├── scripts/                       # Executable tools (Python / Bash)
│   ├── fetch_osmjp.py             § 5 (initial setup + rare refresh; only OSM.jp-touching tool)
│   ├── buffer_clip.py             § 6
│   ├── verify_buffer.py           § 6
│   ├── patch_style.py             § 9.3
│   ├── apply_sea_mask.py          § 12.1
│   └── rebuild.sh                 § 11
├── geojson/                       # § 5; tracked: README.md + LICENSE.
│   │                              #       *.geojson are gitignored
│   │                              #       (operator fetches them via scripts/fetch_osmjp.py)
│   ├── README.md
│   └── LICENSE
├── data/tileserver-gl/
│   └── config.json                § 9.5
├── etc/                           # Mirrors deploy paths under /etc
│   ├── systemd/system/*.service / *.timer
│   ├── nginx/sites-available/tile.hogehoge.com{,.http-only}
│   ├── letsencrypt/renewal-hooks/deploy/reload-nginx.sh
│   └── sudoers.d/tileserver-rebuild
└── web/
    └── demo.html                  § 10.3
```

スクリプトは `$REPO/scripts/` から直接実行する (インストール不要)。設定ファイルは `sudo install` で所定のパスに配置する。

> **プレースホルダ**: 各設定ファイル中の `tile.hogehoge.com` (ドメイン) と `shimotsuki` (ユーザ名) はテンプレート値。別環境にデプロイする場合は `sed -i` で一括置換するか、デプロイ前に手で書き換える。

---

## 3. Step 0: 環境構築

```bash
sudo apt update
sudo apt install -y \
    openjdk-21-jdk \
    osmium-tool \
    python3 python3-venv python3-pip \
    git curl wget jq sqlite3 unzip \
    build-essential libcairo2-dev libjpeg-dev libpango1.0-dev \
    libgif-dev librsvg2-dev libpixman-1-dev

# Node.js LTS
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Python venv (shapely + pyproj は buffer_clip.py 用、その他はメンテナンス用 fetch_osmjp.py 用)
python3 -m venv /work/shimotsuki/planetiler/venv
source /work/shimotsuki/planetiler/venv/bin/activate
pip install --upgrade pip
pip install shapely pyproj                              # 週次 rebuild ループに必要
pip install requests mercantile mapbox-vector-tile      # scripts/fetch_osmjp.py 用 (初回セットアップ時に必要、§5 参照)

# Planetiler
cd /work/shimotsuki/planetiler/src
wget -q https://github.com/onthegomap/planetiler/releases/latest/download/planetiler.jar
java -jar planetiler.jar --help | head -3

# tileserver-gl をプロジェクトローカルにインストール (sudo 不要)
# /home/shimotsuki/tileserver-gl を npm プロジェクトとして扱う
cd /home/shimotsuki/tileserver-gl
npm init -y
npm install tileserver-gl
# ディスク小 / PNG レンダリング不要ならこちらでも可:
#   npm install tileserver-gl-light

# 実行ファイルのパス確認
ls -la /home/shimotsuki/tileserver-gl/node_modules/.bin/tileserver-gl
/home/shimotsuki/tileserver-gl/node_modules/.bin/tileserver-gl --version
```

> **グローバル (`-g`) を使わない理由**: `sudo npm install -g` は `/usr/lib/node_modules/` に書き込むため特権が必要になる。プロジェクトローカルインストールなら shimotsuki 権限で完結し、依存ライブラリ (sharp 等のネイティブビルドを伴うパッケージ) も同ユーザのキャッシュ (`~/.npm`) を使う。また、tileserver-gl をバージョンアップしたい際も `cd /home/shimotsuki/tileserver-gl && npm update tileserver-gl` で完結する。
>
> この方式の結果、最終的なディレクトリ構成は:
>
> ```
> /home/shimotsuki/tileserver-gl/
> ├── package.json                         # npm プロジェクトメタ
> ├── package-lock.json
> ├── node_modules/
> │   └── .bin/tileserver-gl               # ← systemd が ExecStart で指す実行ファイル
> └── data/                                # 配信データ (§2.2 で作成済み)
>     ├── config.json
>     ├── openmaptiles.mbtiles             # 共通タイルデータ
>     ├── styles/
>     │   ├── maptiler-toner-en/style.json
>     │   └── maptiler-basic-en/style.json
>     ├── sprites/
>     │   ├── maptiler-toner-en/{sprite,sprite@2x}.{json,png}
>     │   └── maptiler-basic-en/{sprite,sprite@2x}.{json,png}
>     └── fonts/                           # 共通 (Noto Sans, Nunito)
> ```

---

## 4. Step 1: OSM PBF の取得

```bash
cd /work/shimotsuki/planetiler/pbf
wget https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf -O global.osm.pbf

# 整合性確認 (MD5 または SHA256 の公式値と照合)
wget https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf.md5
md5sum -c planet-latest.osm.pbf.md5 2>/dev/null \
    || md5sum global.osm.pbf  # 手動比較: ファイル名が異なるため手元で照合
```

- 転送量が大きいため、研究機関のミラー (国内では [AIST FTP](https://ftp.osmfoundation.org/) や [Geofabrik](https://download.geofabrik.de/planet.html)) の利用を推奨
- 初回のみダウンロード、以降は週次で更新差分を取得 (§11 参照)

> **注記**: 北方領土はロシア領として、竹島は韓国領として記述される feature が存在するため、地域抽出ではこれらを取りこぼすリスクがある。本書が全球データを前提とするのはこの理由による。

---

## 5. Step 2: 島嶼ポリゴンの取得 (初回セットアップ時のみ)

バッファ切り抜きの `.poly` を作るため島嶼ポリゴンを OSM.jp から取得する。本リポジトリには**同梱しない**ポリシー (`.gitignore` で `geojson/*.geojson` を除外) のため、操作者が初回セットアップ時に一度だけ取得する。属性は使わずジオメトリのみ利用。z=10 で解像度 ~150 m、2 km バッファに吸収されるため十分。

詳細とライセンスは `geojson/README.md` および `geojson/LICENSE` を参照。

### 5.1 抽出スクリプト

リポジトリ内の `scripts/fetch_osmjp.py` を使用する。OSM.jp の TileJSON エンドポイントから島嶼 polygon を MVT 経由で取得し、ジオメトリのみ (`properties` は空) の GeoJSON に展開する。

| 引数 | 意味 |
|---|---|
| `--tilejson` | TileJSON URL (例: `https://tile.openstreetmap.jp/data/hoppo.json`) |
| `--layer` | MVT 内の vector layer 名 (本書では `island`) |
| `--zoom` | 取得ズーム (既定 10、TileJSON の min/max にクランプ) |
| `--out` | 出力 GeoJSON パス |

### 5.2 実行

```bash
source /work/shimotsuki/planetiler/venv/bin/activate
# (要 pip install: requests mercantile mapbox-vector-tile shapely pyproj — §3 参照)

"$REPO/scripts/fetch_osmjp.py" \
    --tilejson https://tile.openstreetmap.jp/data/hoppo.json \
    --layer island --zoom 10 \
    --out "$REPO/geojson/hoppo.geojson"

"$REPO/scripts/fetch_osmjp.py" \
    --tilejson https://tile.openstreetmap.jp/data/takeshima.json \
    --layer island --zoom 10 \
    --out "$REPO/geojson/takeshima.geojson"

deactivate

jq '.features | length' "$REPO/geojson/"*.geojson
```

POI レイヤーは bbox が島嶼 polygon と同一のため取得不要 (2 km バッファで吸収される)。

> **以降の rebuild では再取得不要**: 取得したファイルは `$REPO/geojson/` に保存され、週次 rebuild ループ (§11) はこれを入力として使うのみ。OSM.jp は二度と叩かない。地理的境界の更新が必要になった場合のみ、上記コマンドを再実行してファイルを上書きする (年単位の頻度)。

---

## 6. Step 3: 2km バッファ付き `.poly` を生成

測地ベースのバッファ (Azimuthal Equidistant 射影) により、全緯度で正確に 2 km を確保。全球を外周、バッファ済み島嶼領域を「穴」として定義した osmium 用 `.poly` を出力する。

リポジトリ内の `scripts/buffer_clip.py` を使用する。入力はリポジトリ収録の GeoJSON、デフォルト出力先は `/work/shimotsuki/planetiler/build/{world_minus_islands.poly, islands_buffered.geojson}`。

| 引数 | 意味 |
|---|---|
| `--inputs` | 入力 GeoJSON (複数指定可、和集合を取る) |
| `--buffer-m` | バッファ幅 (メートル、既定 2000) |
| `--out` | 出力 `.poly` パス |
| `--debug` | バッファ結果の GeoJSON (QGIS 等で目視検証用) |

```bash
cd /work/shimotsuki/planetiler
source venv/bin/activate
"$REPO/scripts/buffer_clip.py" \
    --inputs "$REPO/geojson/hoppo.geojson" "$REPO/geojson/takeshima.geojson" \
    --buffer-m 2000
deactivate
```

生成物:

- `build/world_minus_islands.poly` — osmium に渡すクリップ定義
- `build/islands_buffered.geojson` — 目視検証用のバッファ結果 (QGIS 等で確認)

**検証:** バッファが実際に島から 2 km 拡張されていることを確認 (`scripts/verify_buffer.py` は択捉島北端の南北 1 km / 3 km の 2 点を内外判定する。両方期待通りなら exit 0)。

```bash
"$REPO/scripts/verify_buffer.py" --geojson build/islands_buffered.geojson
```

---

## 7. Step 4: osmium で島嶼 +2km 内の全 feature を除去

```bash
cd /work/shimotsuki/planetiler
osmium extract \
    -p build/world_minus_islands.poly \
    --strategy=smart \
    --overwrite \
    -o pbf/clipped.osm.pbf \
    pbf/global.osm.pbf

# 検証: Hoppo bbox 内に name 付き feature が残っていないか
osmium extract --overwrite \
    --bbox 145.3,43.3,148.9,45.6 \
    -o /tmp/resid.osm.pbf pbf/clipped.osm.pbf
osmium tags-filter /tmp/resid.osm.pbf 'name' \
    --output-format=opl -o /tmp/resid_named.opl --overwrite
echo "Hoppo 内の name 付き残存 feature 数: $(wc -l < /tmp/resid_named.opl)"

osmium extract --overwrite \
    --bbox 131.85,37.22,131.89,37.27 \
    -o /tmp/resid_t.osm.pbf pbf/clipped.osm.pbf
osmium tags-filter /tmp/resid_t.osm.pbf 'name' \
    --output-format=opl -o /tmp/resid_t_named.opl --overwrite
echo "Takeshima 内の name 付き残存 feature 数: $(wc -l < /tmp/resid_t_named.opl)"
```

両者ともほぼ 0 になるはず。0 でない場合、残存は境界 relation のメンバー参照等に由来する**ジオメトリを伴わない要素**で、レンダリングには影響しない。完全除去したい場合は §7.1 を適用。

### 7.1 (任意) 名前付き feature の二重除去

```bash
osmium tags-filter /tmp/resid.osm.pbf 'name' 'place' 'amenity' 'natural=peak' 'tourism' \
    --output-format=opl -o /tmp/rm_h.opl --overwrite
osmium tags-filter /tmp/resid_t.osm.pbf 'name' 'place' 'amenity' 'natural=peak' 'tourism' \
    --output-format=opl -o /tmp/rm_t.opl --overwrite
awk '{print $1}' /tmp/rm_h.opl /tmp/rm_t.opl | sort -u > /tmp/rm_ids.txt
if [[ -s /tmp/rm_ids.txt ]]; then
    osmium removeid --id-file=/tmp/rm_ids.txt \
        -o pbf/clipped_final.osm.pbf --overwrite pbf/clipped.osm.pbf
    mv pbf/clipped_final.osm.pbf pbf/clipped.osm.pbf
fi
```

---

## 8. Step 5: Planetiler で MBTiles をビルド

```bash
cd /work/shimotsuki/planetiler

# 全球ビルド。RAM 128GB 以上なら --storage=ram で高速化、
# それ以下なら --storage=mmap でディスクにオフロード。
java -Xmx100g -jar src/planetiler.jar \
    --osm_path=pbf/clipped.osm.pbf \
    --download \
    --output=mbtiles/final.mbtiles \
    --force \
    --storage=mmap \
    --nodemap-storage=mmap \
    --nodemap-type=array \
    --languages=en,ja,ko,ru \
    --transliterate=false \
    --building-merge-z13=false
```

**オプションの要点:**

- `--download` : Natural Earth と水域ポリゴン (海岸線) を自動取得。セットアップ時のみの外部アクセス。
- `--languages=en,ja,ko,ru` : 各 `name:LANG` 属性を出力に保持する言語。`name:latin` の生成ソースとして `name:en` が必要なため `en` を必ず含める。
- `--transliterate=false` : **自動ローマ字化(音訳)の完全無効化**。OpenMapTiles のデフォルトでは `name:en` 等の Latin スクリプト名が無い場合、ICU による音訳で `name:latin` を生成するが、日本語・中国語・ハングル等で極めて低品質な結果を出力する (例: 東京 → "Dong Jing" 相当)。このフラグを指定すると以下の挙動になる:
    - `name:en` があれば → `name:latin = name:en` (自然な英語表記)
    - `name:en` が無く元の `name` が非 Latin のみ → **`name:latin` は空** → Toner-en の `{name:latin}` 参照で**ラベルそのものが描画されない**
    - 副次効果: 高コストな音訳処理がスキップされビルドが**数〜十数%高速化**
- `-Xmx100g` : 全球ビルド時の JVM 最大ヒープ。RAM 実装量の 75% 程度を目安に。128GB 実装なら `-Xmx100g`、64GB 実装なら `-Xmx48g` 等。
- `--storage=mmap`, `--nodemap-storage=mmap`, `--nodemap-type=array` : RAM より大きい node map を mmap でディスク展開する設定。RAM 128GB+ の環境で高速化したい場合は `--storage=ram` に差し替える (ただし tmp ディスクは変わらず必要)。

> **挙動の確認**
> ビルド後、`name:en` が欠落した日本の田舎の集落などで `name:latin` が空になっていることを確認できる:
> ```bash
> # 任意の place タイルから name フィールドの中身をのぞく (tippecanoe-decode を使う場合)
> curl -s http://127.0.0.1:8080/data/openmaptiles/10/897/404.pbf | \
>     tippecanoe-decode -c /dev/stdin 10 897 404 | \
>     jq '.features[] | select(.properties.class=="village") | .properties | {name, "name:en", "name:latin"}'
> ```
> 旧動作では `name:latin` にヘンテコ音訳が入っていた行が、新動作では空欄になっている。

**検証:**

```bash
sqlite3 mbtiles/final.mbtiles "SELECT name, value FROM metadata WHERE name IN ('format','minzoom','maxzoom','bounds');"
sqlite3 mbtiles/final.mbtiles "SELECT value FROM metadata WHERE name='json';" | jq '.vector_layers[].id'
```

`water`, `boundary`, `place`, `transportation` 等、標準 OpenMapTiles レイヤーが並ぶこと。`island` / `island_poi` は**含まれない** (OSM.jp 由来を合成しないため、これが仕様通り)。

---

## 9. Step 6: 自前 tileserver-gl のセットアップ

### 9.1 スタイル本体と sprite の取得

本構成では **2 つのスタイル** を配信する。スタイル本体は OpenMapTiles 公式 (BSD 3-Clause + CC-BY 4.0) から直接取得する。OSM.jp 経由ではない。

| スタイル ID (ローカル配信名) | 上流リポジトリ | 上流タグ | 特徴 |
|---|---|---|---|
| `maptiler-toner-en` | [openmaptiles/maptiler-toner-gl-style](https://github.com/openmaptiles/maptiler-toner-gl-style) | `v1.0` | 白黒・高コントラスト・情報量少 |
| `maptiler-basic-en` | [openmaptiles/maptiler-basic-gl-style](https://github.com/openmaptiles/maptiler-basic-gl-style) | `v1.10` | カラー・情報量多 |

> **`-en` サフィックスについて**: 上流の style.json はラベルで `{name:latin}` を直接参照する英語ベース構成のため、別途「英語版」が用意されているわけではない。本書ではローカル配信ディレクトリ名の慣例として `-en` を維持する (旧 OSM.jp 配信名との互換性および「Latin スクリプト固定描画」の意図を明示するため)。

公開 URL:
- `https://tile.hogehoge.com/styles/maptiler-toner-en/style.json`
- `https://tile.hogehoge.com/styles/maptiler-basic-en/style.json`

```bash
cd /home/shimotsuki/tileserver-gl/data
mkdir -p styles/maptiler-toner-en sprites/maptiler-toner-en \
         styles/maptiler-basic-en sprites/maptiler-basic-en \
         fonts

# style.json は release tag 固定で取得 (再現性のため)
curl -fsSL -o styles/maptiler-toner-en/style.json \
    https://raw.githubusercontent.com/openmaptiles/maptiler-toner-gl-style/v1.0/style.json
curl -fsSL -o styles/maptiler-basic-en/style.json \
    https://raw.githubusercontent.com/openmaptiles/maptiler-basic-gl-style/v1.10/style.json

# sprite は gh-pages ブランチ (github.io) から取得
fetch_sprites() {
    local id=$1 repo=$2
    for f in sprite.json sprite.png "sprite@2x.json" "sprite@2x.png"; do
        curl -fsSL -o "sprites/${id}/${f}" \
            "https://openmaptiles.github.io/${repo}/${f}"
    done
}
fetch_sprites maptiler-toner-en  maptiler-toner-gl-style
fetch_sprites maptiler-basic-en  maptiler-basic-gl-style

ls -la styles/ sprites/
```

> **ライセンス**: 上流 style.json のコードは BSD 3-Clause、design (look & feel) は CC-BY 4.0 (Toner はさらに Stamen Design への ISC ベース由来あり)。改変版を再配布する際の attribution 文字列は `patch_style.py` が `metadata.attribution` に自動で書き込む。詳細は §13。

### 9.2 フォントの取得 (openmaptiles/fonts)

上流 style.json が参照するフォント:

| スタイル | 参照フォント |
|---|---|
| `maptiler-basic-en` | `Noto Sans Regular`、`Noto Sans Bold` |
| `maptiler-toner-en` | `Noto Sans Italic`、`Noto Sans Bold Italic`、`Nunito Extra Bold`、`Nunito Semi Bold` |

すべて [openmaptiles/fonts](https://github.com/openmaptiles/fonts) (OFL) に収録済み。

```bash
cd /home/shimotsuki/tileserver-gl/data
wget -q https://github.com/openmaptiles/fonts/archive/refs/heads/master.tar.gz -O /tmp/omt-fonts.tar.gz
tar -xzf /tmp/omt-fonts.tar.gz -C /tmp/
mv /tmp/fonts-master/"Noto Sans Regular"      fonts/
mv /tmp/fonts-master/"Noto Sans Bold"         fonts/
mv /tmp/fonts-master/"Noto Sans Italic"       fonts/
mv /tmp/fonts-master/"Noto Sans Bold Italic"  fonts/
mv /tmp/fonts-master/"Nunito Extra Bold"      fonts/
mv /tmp/fonts-master/"Nunito Semi Bold"       fonts/
rm -rf /tmp/fonts-master /tmp/omt-fonts.tar.gz
ls fonts/
```

> 旧版 (OSM.jp の改変スタイル) は `migu1c-regular` / `migu2m-regular` (日本語フォント) や `Nunito Regular` / `Nunito Bold` も参照していたが、上流オリジナルにはこれらの参照がないため取得不要。`patch_style.py` の `migu*` 置換ロジック (§9.3) は上流スタイルでは no-op として残置 (旧 OSM.jp スタイルとの後方互換のため、削除はしない)。

### 9.3 スタイルの独立化 + 境界線中立化パッチ

以下 7 項目を一括で両スタイルに適用する Python スクリプト。

1. `openmaptiles` ソースの URL を mbtiles スキームに変更
2. `hoppo` / `takeshima` ソースを削除 → OSM.jp へのランタイム依存を排除
3. それらを参照する 5 つのレイヤー (`island-hoppo`, `island-hoppo-name`, `island-takeshima`, `island-takeshima-name`, `island-takeshima-poi`) を削除
4. `migu1c-regular` / `migu2m-regular` → `Noto Sans Regular` に置換
5. **`boundary` レイヤーの全フィルターに `["!=", "maritime", 1]` を追加** → 海上国境線のみ非表示
6. **`admin_level` ≤ 2 を描画する国境専用レイヤーを削除し、`admin_level` = 2, 3 を州/県境 (`admin_level` = 4) のレイヤーに合流** → 陸上の国境線も州/県境と同一スタイルで描画される
7. **国名ラベル (`place.class=country`) の `maxzoom` を 5 に制限** → z0–4 のみ表示、z5 以上では非表示
8. `sprite` と `glyphs` URL を絶対 URL 化

> **設計意図の整理**
>
> | 項目 | 旧挙動 | 本パッチ後 |
> |---|---|---|
> | 海上国境線 | Toner-en: 黒-on-黒で偶然不可視 / Basic-en: はっきり見える | **両スタイルとも `maritime=1` で明示的に非描画** |
> | 陸上国境線 (admin_level=2) | 独立レイヤーで太線描画 | **都道府県/州界 (admin_level=4) と同一スタイルで描画** |
> | 地域圏等 (admin_level=3) | 本来は描かれないことが多い | **admin_level=4 と同一に統一** |
> | 国の区別 | 境界線で視覚的に明瞭 | **境界線では区別できない** (どの行政境界も同じ破線) |
> | 国の位置 | 全ズームで国名ラベル表示 | **低ズーム (z0–4) のみ国名表示、z5 以上は非表示** |
>
> これにより、拡大時は「これは国境線ではなく、単なる行政区画線です」という立場を取れる。低ズームでは国名ラベルで国の位置を示しつつ、境界線は描かないため、国の形自体が図示されない。日本が海上でしか他国と接しないため maritime フィルター 1 行で要件が足りたが、陸続きの係争地 (カシミール、タイ・カンボジア国境等) にも同じロジックで距離を置ける汎用実装。

本リポジトリ内の `scripts/patch_style.py` がこれらすべてを冪等に適用する。両スタイルへの適用を以下のループで行う:

```bash
for STYLE in maptiler-toner-en maptiler-basic-en; do
    "$REPO/scripts/patch_style.py" \
        --input  /home/shimotsuki/tileserver-gl/data/styles/${STYLE}/style.json \
        --output /home/shimotsuki/tileserver-gl/data/styles/${STYLE}/style.json \
        --style-id ${STYLE} \
        --mbtiles-id openmaptiles \
        --public-url https://tile.hogehoge.com
done
```

実行ログには各スタイルについて以下の数値が出力される: 削除されたレイヤー数 (`country-only layers removed` = 2)、合流パッチが当たったレイヤー数 (`sub-national layers merged` = 1)、`maxzoom=5` を設定した国名ラベル数 (`country labels`)、maritime ガードを付与した boundary レイヤー数 (`boundary layers w/ maritime-guard`)。

スクリプトの主要シンボル (検証コマンド §10.2.1 やトラブルシューティング §12 から参照される):

| 識別子 | 意味 |
|---|---|
| `MARITIME_GUARD` | `["!=", "maritime", 1]` フィルター句。boundary レイヤーへ AND 合成 |
| `add_filter_clause(layer, clause)` | フィルター AND 合成 (冪等) |
| `inject_admin_levels(f, extra)` | `admin_level` 条件に追加レベルを合流 |
| `COUNTRY_ONLY_LAYER_IDS` | 削除対象レイヤー ID 集合 (`admin_country_*`, `boundary_country_*`) |
| `SUB_LAYER_IDS` | 合流先レイヤー ID 集合 (`admin_sub`, `boundary_state`) |
| `COUNTRY_LABEL_MAXZOOM = 5` | 国名ラベルのカットオフ |
| `neutralize_country_boundaries(style)` | (A)(B)(C) を一括実施 |

> 実行後、各スタイルの `boundary` 関連レイヤー (Toner-en で 3 個、Maptiler-Basic-en で 3 個) すべてに maritime ガードが入る。また両スタイルから OSM.jp 依存 (hoppo/takeshima ソースと 5 レイヤー) が削除される。

### 9.4 MBTiles を配置

ビルド作業ディレクトリ (`/work`) と配信ディレクトリ (`/home`) が別ファイルシステムの可能性があるため、**クロスファイルシステム対応のアトミック配置**を行う。`cp` で配信側にコピー後、同一 FS 内で `mv` rename する (`rename(2)` は同一 FS 内でのみアトミック)。

```bash
# 配信側に一度コピー (この時点では .new という仮ファイル名)
cp /work/shimotsuki/planetiler/mbtiles/final.mbtiles \
   /home/shimotsuki/tileserver-gl/data/openmaptiles.mbtiles.new

# 同一 FS 内 rename でアトミック置換
mv -f /home/shimotsuki/tileserver-gl/data/openmaptiles.mbtiles.new \
      /home/shimotsuki/tileserver-gl/data/openmaptiles.mbtiles
```

初回配置時は rename 元ファイルが無い状態からでも同じコマンドで問題なく動作する。

### 9.5 tileserver-gl の config.json

本リポジトリ収録の `data/tileserver-gl/config.json` を tileserver-gl のデータディレクトリに配置する:

```bash
install -m 0644 -o shimotsuki -g shimotsuki \
    "$REPO/data/tileserver-gl/config.json" \
    /home/shimotsuki/tileserver-gl/data/config.json
```

これにより、2 スタイル × 1 共通 MBTiles (`openmaptiles.mbtiles`) の配信となる。MBTiles の重複保持は不要。

公開される URL の対応関係:

| URL | tileserver-gl 内部パス |
|---|---|
| `https://tile.hogehoge.com/styles/maptiler-toner-en/style.json` | `styles/maptiler-toner-en/style.json` |
| `https://tile.hogehoge.com/styles/maptiler-toner-en/sprite.{png,json}` | `sprites/maptiler-toner-en/sprite.*` |
| `https://tile.hogehoge.com/styles/maptiler-basic-en/style.json` | `styles/maptiler-basic-en/style.json` |
| `https://tile.hogehoge.com/styles/maptiler-basic-en/sprite.{png,json}` | `sprites/maptiler-basic-en/sprite.*` |
| `https://tile.hogehoge.com/data/openmaptiles.json` | `openmaptiles.mbtiles` のメタデータ |
| `https://tile.hogehoge.com/data/openmaptiles/{z}/{x}/{y}.pbf` | `openmaptiles.mbtiles` のタイル本体 |
| `https://tile.hogehoge.com/fonts/{fontstack}/{range}.pbf` | `fonts/{fontstack}/{range}.pbf` |

`serve_rendered: false` はラスタライズ (PNG) を無効化してメモリ使用量を抑制。ベクタータイルのみで足りる用途向け。必要なら `true` に。

### 9.6 systemd ユニット

tileserver-gl はループバック専用バインドとし、外部公開は後段の nginx に任せる (TLS 終端と CORS / キャッシュを nginx 側で一元管理するため)。本リポジトリ収録の `etc/systemd/system/tileserver-gl.service` を配置する:

```bash
sudo install -m 0644 -o root -g root \
    "$REPO/etc/systemd/system/tileserver-gl.service" \
    /etc/systemd/system/tileserver-gl.service

sudo chown -R shimotsuki:shimotsuki /home/shimotsuki/tileserver-gl/data
sudo systemctl daemon-reload
sudo systemctl enable --now tileserver-gl.service
systemctl status tileserver-gl.service --no-pager

# ループバック以外でバインドされていないことを確認
ss -ltnp | grep 8080   # 127.0.0.1:8080 のみが listen
```

### 9.7 nginx + certbot によるリバースプロキシ構築

**本構成では nginx リバースプロキシが必須**。外部アクセスは必ず `https://tile.hogehoge.com` 経由で nginx を通し、裏側の tileserver-gl はループバックでのみ応答する。

#### 9.7.1 パッケージインストール

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
sudo systemctl enable --now nginx
```

`python3-certbot-nginx` プラグインを入れると、certbot が nginx 設定を自動編集して証明書配置と HTTP→HTTPS リダイレクトを組み立ててくれる。

#### 9.7.1a nginx 実行ユーザを shimotsuki に変更

Ubuntu の apt nginx は既定で `www-data:www-data` で動作する。本構成ではキャッシュ・ログ・静的配信ツリーを全て `shimotsuki` が所有するため、nginx ワーカーも同ユーザに降格させて権限問題を回避する。

```bash
# /etc/nginx/nginx.conf の user ディレクティブを書き換え
sudo sed -i 's|^user .*;|user shimotsuki shimotsuki;|' /etc/nginx/nginx.conf
grep ^user /etc/nginx/nginx.conf
# -> user shimotsuki shimotsuki;

# ログディレクトリのオーナーを変更 (既存ログは保持)
sudo chown -R shimotsuki:shimotsuki /var/log/nginx

# logrotate がローテート後の新規ログを適切なオーナーで作成するよう設定を更新
sudo sed -i \
    -e 's|create 0640 www-data adm|create 0640 shimotsuki adm|' \
    -e 's|create www-data adm|create shimotsuki adm|' \
    /etc/logrotate.d/nginx

# pid ファイルの配置先 /var/run/nginx.pid は tmpfs 上で systemd 管理なのでそのまま

# 構文検査 + 再起動
sudo nginx -t
sudo systemctl restart nginx

# ワーカーが shimotsuki 権限で動作していることを確認
ps -eo user,pid,cmd | grep 'nginx:' | grep -v grep
# -> master は root、worker は shimotsuki になる

# /home/shimotsuki のアクセス権が nginx ワーカーから辿れるか確認
# (同一ユーザで動くのでまず問題ないが、後で他ユーザ運用に切り替える場合の保険)
ls -ld /home/shimotsuki
# drwxr-x--- 程度でよい。o+x (755) は不要
```

> **バインド権限**: TCP 80 / 443 は特権ポートだが、nginx の master プロセスは `root` で起動してから `user` ディレクティブで指定されたユーザにワーカーを降格するため、`shimotsuki` でも問題なく listen できる。systemd unit の `ExecStart` も変更不要。

#### 9.7.2 DNS と FW 事前条件

- `tile.hogehoge.com` の A / AAAA レコードが本サーバのグローバル IP を指していること
- TCP 80 / 443 が外部から到達可能であること (certbot の HTTP-01 challenge は 80 番を使用)

```bash
# 事前確認
dig +short tile.hogehoge.com
curl -I http://tile.hogehoge.com/  # nginx デフォルトページが見えれば OK
```

#### 9.7.3 nginx 初期設定 (HTTP のみ)

certbot を走らせる前に、server_name が正しく認識されるよう最低限の HTTP 設定を置く。本リポジトリ収録の `etc/nginx/sites-available/tile.hogehoge.com.http-only` を `tile.hogehoge.com` という名前で配置する (`.http-only` サフィックスは付けない):

```bash
sudo install -m 0644 -o root -g root \
    "$REPO/etc/nginx/sites-available/tile.hogehoge.com.http-only" \
    /etc/nginx/sites-available/tile.hogehoge.com

sudo ln -sf /etc/nginx/sites-available/tile.hogehoge.com /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

#### 9.7.4 SSL 証明書取得 (Let's Encrypt)

```bash
sudo certbot --nginx \
    -d tile.hogehoge.com \
    --agree-tos \
    -m admin@hogehoge.com \
    --redirect \
    --non-interactive
```

オプションの意味:
- `--nginx` : nginx プラグインで設定ファイルを自動編集
- `--redirect` : HTTP→HTTPS の 301 リダイレクトを自動挿入
- `-m` : 有効期限切れ通知の送信先メール

certbot は `/etc/letsencrypt/live/tile.hogehoge.com/{fullchain,privkey}.pem` に証明書を配置し、systemd timer `certbot.timer` で自動更新を仕込む。apt 版 certbot では **自動更新は標準で有効**なので追加の cron 設定は不要。

```bash
# 自動更新タイマーの確認
systemctl list-timers | grep certbot
# 更新のドライラン (実際に更新せず検証)
sudo certbot renew --dry-run
```

#### 9.7.5 nginx 本設定 (リバースプロキシ + キャッシュ + CORS)

certbot 実行後、本リポジトリ収録の `etc/nginx/sites-available/tile.hogehoge.com` で同名ファイルを上書きする。この設定には HTTP→HTTPS リダイレクト、proxy_cache (`/var/cache/nginx/tiles`、20 GB / 7 日)、CORS、`/demo.html` の nginx 直接配信 (§10.3) がすべて含まれる。

```bash
sudo install -m 0644 -o root -g root \
    "$REPO/etc/nginx/sites-available/tile.hogehoge.com" \
    /etc/nginx/sites-available/tile.hogehoge.com

sudo mkdir -p /var/cache/nginx/tiles
sudo chown -R shimotsuki:shimotsuki /var/cache/nginx/tiles

sudo nginx -t && sudo systemctl reload nginx
```

> **certbot が自動編集した内容との関係**: `certbot --nginx` (§9.7.4) は §9.7.3 で配置した HTTP-only 設定に SSL ディレクティブを追記するが、本ステップでは設定ファイル全体を本リポジトリ版で**上書き**する。本リポジトリ版には `ssl_certificate` / `ssl_certificate_key` / `include /etc/letsencrypt/options-ssl-nginx.conf;` / `ssl_dhparam` が直書きされているため、certbot の編集結果は失われても問題ない。証明書ファイル自体は `/etc/letsencrypt/live/tile.hogehoge.com/` に残っており、設定が参照する。

#### 9.7.6 疎通確認

```bash
# HTTPS 到達
curl -IL https://tile.hogehoge.com/

# 両スタイルの JSON
curl -s https://tile.hogehoge.com/styles/maptiler-toner-en/style.json | jq '.sources | keys'
curl -s https://tile.hogehoge.com/styles/maptiler-basic-en/style.json | jq '.sources | keys'

# CORS 確認
curl -I -H "Origin: https://example.com" \
    https://tile.hogehoge.com/data/openmaptiles/10/897/407.pbf \
    | grep -i "access-control"

# キャッシュ動作 (2 回目は X-Cache-Status: HIT)
curl -s -o /dev/null -D - https://tile.hogehoge.com/data/openmaptiles/10/897/407.pbf | grep -i x-cache
curl -s -o /dev/null -D - https://tile.hogehoge.com/data/openmaptiles/10/897/407.pbf | grep -i x-cache
```

#### 9.7.7 証明書更新時の nginx リロード

certbot は更新成功時に `/etc/letsencrypt/renewal-hooks/deploy/` 配下のスクリプトを実行する。本リポジトリ収録の `etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` を配置する:

```bash
sudo install -m 0755 -o root -g root \
    "$REPO/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh" \
    /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

#### 9.7.8 スタイル内部 URL の確認

§9.3 で `--public-url https://tile.hogehoge.com` を指定している場合、この節は確認のみでよい。

```bash
# 両スタイルの sprite/glyphs が https://tile.hogehoge.com/... の絶対 URL になっている
for STYLE in maptiler-toner-en maptiler-basic-en; do
    echo "=== ${STYLE} ==="
    jq '{sprite, glyphs}' /home/shimotsuki/tileserver-gl/data/styles/${STYLE}/style.json
done
```

想定出力例 (`maptiler-toner-en`):

```json
{
  "sprite": "https://tile.hogehoge.com/styles/maptiler-toner-en/sprite",
  "glyphs": "https://tile.hogehoge.com/fonts/{fontstack}/{range}.pbf"
}
```

そうなっていない場合は §9.3 の patch_style.py を `--public-url` 付きで再実行し、`sudo systemctl restart tileserver-gl` を実施する。

---

## 10. 動作確認

### 10.1 エンドポイントの疎通

まずループバック側 (tileserver-gl 直接) で疎通、次に公開側 (nginx 経由 HTTPS) で疎通を確認する。

```bash
# --- 内部 (127.0.0.1:8080) ---
for STYLE in maptiler-toner-en maptiler-basic-en; do
    echo "=== ${STYLE} ==="
    curl -s http://127.0.0.1:8080/styles/${STYLE}/style.json | jq '.sources | keys'
    # -> ["openmaptiles"]  だけが含まれ、hoppo/takeshima が無いこと
done

curl -s http://127.0.0.1:8080/data/openmaptiles.json | jq '.vector_layers[].id'
# -> water, boundary, place, transportation 等が並ぶ

# --- 外部 (https://tile.hogehoge.com) ---
for STYLE in maptiler-toner-en maptiler-basic-en; do
    curl -sI https://tile.hogehoge.com/styles/${STYLE}/style.json
    # -> HTTP/2 200
done

curl -sI https://tile.hogehoge.com/data/openmaptiles/10/897/407.pbf
# -> HTTP/2 200
```

### 10.2 島嶼領域が空であることの確認

```bash
# 択捉島中心付近の z=12 タイルが空であることを確認
# tile coord ≈ (3641, 1472) at z=12 for (148.0, 44.5)
curl -s https://tile.hogehoge.com/data/openmaptiles/12/3641/1472.pbf -o /tmp/t.pbf
file /tmp/t.pbf && wc -c /tmp/t.pbf
# 極小サイズ (数百 bytes 以下) または空タイル
```

### 10.2.1 境界線中立化パッチが有効であることの確認

§9.3 の `patch_style.py` が正しく適用され、以下が成立していることを確認する。処理の論理順に沿って確認 (A)→(B)→(C)→(D) で進める。

**(A) admin_level=2 専用レイヤーが削除されている (§1.2.2)**

```bash
for STYLE in maptiler-toner-en maptiler-basic-en; do
    echo "=== ${STYLE} ==="
    jq '.layers[] | select(.id | test("admin_country|boundary_country"))' \
        /home/shimotsuki/tileserver-gl/data/styles/${STYLE}/style.json
done
```

出力が空 (null 以外何も出ない) であれば、国境専用レイヤーが全て削除されている。

**(B) 州/県境レイヤーに admin_level=2, 3 が合流している (§1.2.2)**

```bash
for STYLE in maptiler-toner-en maptiler-basic-en; do
    echo "=== ${STYLE} ==="
    jq '.layers[] | select(.id == "admin_sub" or .id == "boundary_state") | {id, filter}' \
        /home/shimotsuki/tileserver-gl/data/styles/${STYLE}/style.json
done
```

`filter` 内の admin_level に関する条件 (`["in","admin_level",...]`) に 2, 3, 4 がすべて含まれていれば OK。

**(C) 合流後レイヤーに maritime!=1 が掛かっている (§1.2.4 — 本命フィルター)**

上記 (B) と同じコマンドで、`admin_sub` / `boundary_state` の `filter` に `["!=","maritime",1]` が**含まれていることを目視確認**。これが**根室 ↔ 北方領土間などの海上 admin_level=2 way の描画を抑止する実効フィルター**。欠落していると、合流で取り込まれた海上国境が県境と同じスタイルで描画されてしまう。

ワンライナー確認:

```bash
for STYLE in maptiler-toner-en maptiler-basic-en; do
    for LAYER in admin_sub boundary_state; do
        result=$(jq -r --arg l "$LAYER" \
            '.layers[] | select(.id == $l) | .filter | tostring | contains("\"!=\",\"maritime\",1") // false' \
            /home/shimotsuki/tileserver-gl/data/styles/${STYLE}/style.json)
        [ -n "$result" ] && echo "${STYLE}/${LAYER}: maritime guard = ${result}"
    done
done
```

両スタイルで該当レイヤーに `true` が出れば OK。

**(D) 国名ラベルが z0–4 限定に制限されている (§1.2.3)**

```bash
for STYLE in maptiler-toner-en maptiler-basic-en; do
    echo "=== ${STYLE} ==="
    jq '.layers[] | select(."source-layer"=="place") | select(.filter | tostring | test("country")) | {id, maxzoom, filter}' \
        /home/shimotsuki/tileserver-gl/data/styles/${STYLE}/style.json
done
```

`class=country` を含む symbol レイヤーの `maxzoom` が `5` になっていれば OK。

### 10.3 ブラウザでの表示確認

デモページは tileserver-gl の配信下ではなく、**nginx が静的ファイルとして直接配信**する場所 (§2.2 で作成済み) に置く。これにより、tileserver-gl が落ちていてもデモは到達可能。`/demo.html` の location ブロックは §9.7.5 の nginx 設定にすでに含まれているため、本節では HTML ファイルの配置のみで足りる。

```bash
install -m 0644 -o shimotsuki -g shimotsuki \
    "$REPO/web/demo.html" \
    /home/shimotsuki/http/tile.hogehoge.com/demo.html
```

ブラウザで `https://tile.hogehoge.com/demo.html#7/44.4/146.2` を開き、右上のラジオボタンで両スタイルを切り替えて以下を確認:

**共通確認項目 (両スタイルで期待される状態)**

| ズーム域 | 確認項目 | 期待される状態 |
|---|---|---|
| z=3 (#3/35/138) | 世界俯瞰 | **境界線は一切描かれない**。国名ラベルだけが大陸上に浮かぶ (`Japan`, `Russia`, `China` 等) |
| z=5 (#5/44/145) | 国境付近にズーム | 国名ラベルが消える。陸上の日露国境・日韓国境相当の線は **都道府県界と同じ破線・同じ色** で描画 (スタイルにより色は異なる) |
| z=7 (#7/44.4/146.2) | 北方領土 | 無地のシルエット。ラベル、道路、POI 等は一切描画されない |
| z=7 (#7/37.24/131.87) | 竹島 | 同様に無地のシルエット |
| z=7 | 択捉島付近の海上国境線 | **見えない** (maritime フィルターで非描画) |
| z=7 | 北海道本土内の都道府県境 | **陸上国境と同じスタイル**で描画 (国境と県境が視覚的に区別できない) |
| z=7 | カシミール地方を表示 (#7/34/76) | インド-パキスタン-中国の係争境界が**県境と同じスタイル**で描画 (「これは国境線ではなく行政区画線です」) |
| z=7 | 地名ラベル | `name:en` がある都市は英語表示。`name:en` が無い小集落・山名等はラベル非描画 (`--transliterate=false`) |

**スタイル別の見え方**

| スタイル | 島のシルエット色 | 海の色 | 境界線 (z5+) の色 |
|---|---|---|---|
| Maptiler-Toner-en | 白 (`#fff`) | 黒 | 黒系の破線 (高コントラスト) |
| Maptiler-Basic-en | 薄ベージュ (`hsl(47,26%,88%)`) | 水色 (`hsl(205,56%,73%)`) | 中間グレー半透明の破線 (`hsla(0,0%,60%,0.5)`) |

島嶼が「背景と同じ色のシルエット」として残る点は両スタイル共通。色を完全に海と同化させたい場合は §12.1 参照。

---

## 11. 運用: 定期更新

OSM 本体は日々更新されるため、週次でリビルドを行う。島嶼ポリゴン (`$REPO/geojson/`) は初回セットアップ時に手元に取得済み (§5)、以降は週次 rebuild で再利用するのみで OSM.jp は週次ループに登場しない (§1.4 参照)。ビルド処理は `shimotsuki` 権限で実施し、MBTiles の置換と tileserver-gl の再起動、および nginx キャッシュ破棄のみ特権操作とする。

### 11.1 リビルドスクリプト本体

リポジトリ内の `scripts/rebuild.sh` を使用する。インストール不要 (systemd サービスがリポジトリ内のパスを直接指す)。スクリプトは以下を順に実行する:

1. `planet.osm.pbf` を最新版で更新
2. `$REPO/geojson/{hoppo,takeshima}.geojson` を入力に `buffer_clip.py` を実行 → `world_minus_islands.poly` を再生成 (毎回実行、ローカル計算のみ、所要数秒)
3. `osmium extract -p` で島嶼 +2 km を切除
4. Planetiler で全球ビルド (出力先は仮名 `final.new.mbtiles`)
5. **クロスファイルシステム対応のアトミック置換**: ビルド成果物をいったん配信ディスクに `cp` してから同一 FS 内で `mv` rename
6. `sudo systemctl restart tileserver-gl.service` で SQLite ハンドルをリフレッシュ
7. `sudo find /var/cache/nginx/tiles -type f -delete` で古いタイルを破棄、`sudo systemctl reload nginx`

スクリプトは `REPO` 環境変数で本リポジトリを参照する (既定値: `/home/shimotsuki/tileserver-noborder`)。systemd サービス側で `Environment=REPO=...` を上書きすれば別パスにも対応可能。

> **OSM.jp 再取得は週次に含めない**: 旧版は (2) で月 1 回 OSM.jp の MVT を取得していたが、リポジトリ収録のスナップショットを使う方式に切り替えた。上流リフレッシュは `scripts/fetch_osmjp.py` を手動で叩く運用 (§1.4 / `geojson/README.md`)。

### 11.2 sudoers で必要最小限の特権を付与

rebuild.sh が叩く 3 コマンドだけをパスワード無しで実行可能にする。本リポジトリ収録の `etc/sudoers.d/tileserver-rebuild` を配置:

```bash
sudo install -m 0440 -o root -g root \
    "$REPO/etc/sudoers.d/tileserver-rebuild" \
    /etc/sudoers.d/tileserver-rebuild
sudo visudo -c -f /etc/sudoers.d/tileserver-rebuild   # 構文検査
```

### 11.3 systemd service / timer

本リポジトリ収録の `etc/systemd/system/tileserver-rebuild.{service,timer}` を配置:

```bash
sudo install -m 0644 -o root -g root \
    "$REPO/etc/systemd/system/tileserver-rebuild.service" \
    /etc/systemd/system/tileserver-rebuild.service

sudo install -m 0644 -o root -g root \
    "$REPO/etc/systemd/system/tileserver-rebuild.timer" \
    /etc/systemd/system/tileserver-rebuild.timer

sudo systemctl daemon-reload
sudo systemctl enable --now tileserver-rebuild.timer
```

> サービスの `ExecStart` は `/home/shimotsuki/tileserver-noborder/scripts/rebuild.sh` を直接指す。リポジトリを別パスにクローンした場合は、サービスファイル上書き or `Environment=REPO=...` 追加 + `ExecStart` パス調整を行う。

---

## 12. トラブルシューティング

| 症状 | 対処 |
|---|---|
| Planetiler が OOM | `-Xmx` を下げて `--storage=mmap --nodemap-storage=mmap --nodemap-type=array` を追加 |
| osmium が `.poly` を拒否 | `head -20 build/world_minus_islands.poly` でフォーマット確認 (1 行目:名前、2 行目:外周名、`END` 3 連、`!hole_N` で穴) |
| 島のシルエットが表示されない | 水域ポリゴンが誤って島域にかぶっている可能性。Planetiler を `--download` 付きで再実行し、land-polygons-split-3857.zip が取得されたか確認 |
| 島のエリアが黒で埋まる | OSM coastline が Hoppo/Takeshima を land として主張している想定と一致しない。黒塗りにしたい場合は §12.1 |
| 島内に道路・建物が残る | `§7.1` の tags-filter + removeid 二重除去を実施 |
| タイルは出るが真っ白 | sprite / fonts が 404。tileserver-gl の起動ログと `curl http://127.0.0.1:8080/fonts/Noto%20Sans%20Regular/0-255.pbf` を確認 |
| `{name:latin}` が空 | Planetiler で `--languages=en,...` を指定していない。再ビルド必要 |
| 非 Latin 名 (日本語等) のラベルが不自然に音訳された文字で出る | `--transliterate=false` を指定していない。指定すると `name:en` 等の Latin 名が無い場合にラベル自体が描画されなくなる (§8 参照) |
| ラベルが英語以外 | OpenMapTiles の仕様: `name:latin` は `name:en` が存在すればそれを優先。OSM 側のタグ依存 |
| ブラウザで Mixed Content | `§9.3` の `patch_style.py` に `--public-url https://tile.hogehoge.com` を指定して再生成 |
| 海上国境線がまだ見える (例: 根室↔北方領土間) | §9.3 の patch_style.py 再実行が必要。**§10.2.1 (C)** のコマンドで `admin_sub` / `boundary_state` の filter に `["!=","maritime",1]` が入っているか確認。これが本命フィルター。OpenMapTiles の古い `boundary` レイヤー定義では `maritime` フィールドが欠落している可能性があるため、Planetiler のバージョンも確認 (本書前提の v0.9+ では含まれる) |
| 陸上国境線が他の行政区画より太い/目立つ線で描画される | `admin_country_*` / `boundary_country_*` レイヤーが削除されていない。**§10.2.1 (A)** のコマンドで確認し、残っていれば §9.3 の patch_style.py を再実行 |
| 陸上国境が描画されない (消えている) | `admin_sub` / `boundary_state` のフィルター拡張が効いていない。**§10.2.1 (B)** で admin_level 条件に 2, 3, 4 がすべて含まれているか確認 |
| 低ズームで何も描画されない (国名すら出ない) | **§10.2.1 (D)** で国名ラベルの `maxzoom` が 5 に制限されているが、対応する symbol レイヤー自体が元スタイルから欠落している可能性。OSM.jp からの style.json 再取得を行ってから patch_style.py を再実行 |
| 拡大しても国が区別できない | **これは仕様** (§1.2.2)。陸上国境も県境と同一スタイルで描画される。国の形を示したい場合は §9.3 patch_style.py から `neutralize_country_boundaries()` の呼び出しをコメントアウトして再実行 |
| `certbot --nginx` 失敗 (HTTP-01 unauthorized) | 80 番への外部到達性と DNS A レコードを確認 (`dig tile.hogehoge.com`、`curl -I http://tile.hogehoge.com/.well-known/acme-challenge/test`)。IPv6 AAAA が古い IP を指している場合も失敗する |
| certbot 自動更新が失敗する | `sudo journalctl -u certbot.timer -e` と `sudo certbot renew --dry-run` を確認。更新成功時に nginx reload が走らない場合は §9.7.7 のフックを確認 |
| リビルド後も古いタイルが返る | nginx `proxy_cache` の残留。§11 の rebuild.sh (6)(7) で `/var/cache/nginx/tiles` が削除されているか確認 |
| 502 Bad Gateway | tileserver-gl が `127.0.0.1:8080` で待ち受けていない。`ss -ltnp | grep 8080` と `journalctl -u tileserver-gl -e` を確認 |
| nginx 起動に失敗 (Permission denied) | §9.7.1a の `user shimotsuki shimotsuki;` 変更後に `/var/log/nginx` のオーナーが shimotsuki になっていない。`sudo chown -R shimotsuki:shimotsuki /var/log/nginx` |
| nginx のログが書き込めない | logrotate で再生成されたログが古いオーナーになっている。`/etc/logrotate.d/nginx` の `create` 行が `shimotsuki adm` を指しているか確認 |
| デモページが 403 Forbidden | nginx ワーカーが shimotsuki になっているか確認 (`ps -eo user,pid,cmd \| grep 'nginx:'`)。また `/home/shimotsuki` のパーミッションが `755` 以上で nginx ワーカーから辿れるか確認 |

### 12.1 島を「海と同化」として扱いたい場合

§1.3 でバッファ除去後の島シルエットは「海の中に背景色のシルエットが浮かぶ」状態となる。これを完全に海と同化させたい場合、スタイルに海色のマスクレイヤーを追加する。両スタイルで海の色が異なるため、スタイルごとに色を変える必要がある (Toner-en は黒、Basic-en は水色)。

リポジトリ内の `scripts/apply_sea_mask.py` を使用する。`--style-id` を渡すと既定色を選択 (`--color` で上書き可)。`water` レイヤー直後に `jp-sea-mask` レイヤーを挿入する冪等処理 (既存があれば差し替え)。

```bash
MASK=/work/shimotsuki/planetiler/build/islands_buffered.geojson
for STYLE in maptiler-toner-en maptiler-basic-en; do
    "$REPO/scripts/apply_sea_mask.py" \
        --style /home/shimotsuki/tileserver-gl/data/styles/${STYLE}/style.json \
        --mask  "$MASK" \
        --style-id ${STYLE}
done

sudo systemctl restart tileserver-gl
sudo find /var/cache/nginx/tiles -type f -delete
sudo systemctl reload nginx
```

この手法はバッファ済み 2km 領域そのものを海と同色で塗るため、島と周辺 2km の海が同色で一体化し、視覚的に「島が消えた」状態になる。両スタイルで同時に適用される。

---

## 13. ライセンス・帰属

成果物ごとに適用ライセンスが異なる。配信される地図と、本リポジトリ内に置かれる中間データを混同しないこと。

### 13.1 成果物別の適用ライセンス

| 成果物 | 適用ライセンス | 必要な帰属表記 |
|---|---|---|
| 配信タイル (`final.mbtiles` および `data/openmaptiles/{z}/{x}/{y}.pbf`) | **ODbL 1.0** (上流 OSM データ) + **CC-BY 4.0** (OpenMapTiles スキーマ) | `© OpenStreetMap contributors`、および `OpenMapTiles` の併記 |
| 配信スタイル (`styles/maptiler-toner-en/style.json`) | **BSD 3-Clause** (code) + **CC-BY 4.0** (design)。design 由来は Stamen Toner (ISC) だが、上流 LICENSE.md は **MapTiler への独占的 copyright 付与例外** を明記 | `© MapTiler` + `© OpenStreetMap contributors` のみ (Stamen, OpenMapTiles 表記不要) |
| 配信スタイル (`styles/maptiler-basic-en/style.json`) | **BSD 3-Clause** (code) + **CC-BY 4.0** (design)。design 由来は Mapbox Open Styles | `© OpenMapTiles` + `© OpenStreetMap contributors` のみ (MapTiler 表記不要) |
| 配信 sprite (`sprites/maptiler-{toner,basic}-en/`) | 各 style.json と同一 | 各 style.json と同一 |
| フォント (`fonts/Noto Sans*`、`fonts/Nunito*`) | **SIL Open Font License (OFL) 1.1** | フォント自体の OFL 表記 (再配布時に同梱) |
| 操作者が手元で fetch した `$REPO/geojson/{hoppo,takeshima}.geojson` | **CC-BY-SA 2.0** (OSMFJ タイル由来) + **ODbL 1.0** (上流 OSM) | `geojson/README.md` および `geojson/LICENSE` 記載のとおり。**本リポジトリには同梱しない** (`.gitignore`) ため、共有義務は操作者がそれらのファイルを再配布した場合にのみ発生 |

### 13.2 share-alike (CC-BY-SA 2.0) の伝播は操作者の `geojson/` 内に限定される

本構成では、CC-BY-SA 2.0 (= OSM.jp タイルライセンス) のシェアアライク義務を負うのは**操作者が `scripts/fetch_osmjp.py` で fetch した手元の `$REPO/geojson/` 配下のファイルのみ**。配信される地図 (タイル + スタイル + sprite + フォント) にも、**本リポジトリの配布物にも一切伝播しない**。理由は以下の 3 点:

**(a) リポジトリには OSM.jp 由来データを同梱しない**

`geojson/*.geojson` は `.gitignore` で除外されており、本リポジトリの clone / tarball / fork には一切含まれない。したがって**リポジトリの配布物自体は GPL-2.0 で完結**し、CC-BY-SA 2.0 / ODbL の義務は発生しない。義務はあくまで「操作者が手元で fetch した瞬間にその操作者個人の手元のファイルに対して」発生する。

**(b) スタイル + sprite は上流 openmaptiles から直接取得 (§9.1, §1.4)**

旧版は OSM.jp の改変済みスタイル (CC-BY-SA 2.0) をベースにしていたが、現構成では openmaptiles 公式 (BSD 3-Clause + CC-BY 4.0、share-alike なし) を直接取得する。よって配信スタイル + sprite は CC-BY-SA 2.0 と無関係。

**(b) MBTiles は OSM.jp の polygon を「内容として」含まない**

OSM.jp の MVT から抽出した polygon (`geojson/`) は、`buffer_clip.py` → `world_minus_islands.poly` (中間ファイル) → `osmium extract -p` の clip mask として用いられるだけで、出力 `clipped.osm.pbf` および `final.mbtiles` には埋め込まれない。`osmium extract -p` は `.poly` の穴の内側を削除する操作であり、`.poly` 自体の座標を出力にコピーしない。

これは「ステンシルの著作権はスプレーアートに伝播しない」のと同じ位置付け: CC-BY-SA 2.0 のシェアアライク条項は派生著作物 (内容を含むもの・改変したもの) に適用される条項であり、入力パラメータ / clip mask としての利用には及ばない。これは (a)(b) と独立した論点で、仮にデータが同梱されていたとしても MBTiles 側には伝播しない。

### 13.3 配信時のクライアント表示例

各スタイルごとに、上流 LICENSE.md が指定する credit 例に従う。

**Toner-en の場合**: 上流 LICENSE.md が "An exception has been granted: required copyright on this style is (C) MapTiler" と明記しているため、`© MapTiler` のみで OpenMapTiles および Stamen Design の表記は不要。
```
© MapTiler | © OpenStreetMap contributors
```

**Basic-en の場合**: 上記のような例外なし。OpenMapTiles credit がそのまま必須。
```
© OpenMapTiles | © OpenStreetMap contributors
```

`scripts/patch_style.py` はスタイル ID に応じて適切な attribution 文字列 (リンク付き HTML) を `metadata.attribution` に自動で埋め込む。MapLibre GL JS / `maplibre-gl-leaflet` を使う場合は、これが自動表示されるため手動指定不要。Leaflet 直叩き等の場合は §14.2 の各例を参照。

> **license labels (CC-BY, ODbL 等) はクレジットに含めない**: 上流 LICENSE.md の例も含めていない。OSM の `/copyright` ページや MapTiler の copyright ページへのハイパーリンクで法的な参照可能性が確保されるため、credit 文字列自体は最小限で足りる。

> **ベクタータイル本体だけを利用する場合 (style.json を使わない)**: §14.2.2 のように `Leaflet.VectorGrid` 等で `data/openmaptiles/{z}/{x}/{y}.pbf` のみを消費する場合は、style 由来の credit が不要となり `© OpenMapTiles | © OpenStreetMap contributors` のみで足りる (OpenMapTiles スキーマと OSM データの帰属のみ残る)。

### 13.4 リポジトリ全体のライセンス境界

| 場所 | ライセンス | 備考 |
|---|---|---|
| **リポジトリ全体 (`scripts/`、`etc/`、`data/`、`web/`、`geojson/README.md`、`geojson/LICENSE`、`tileserver-noborder.md` 等)** | **GPL-2.0** (top-level `LICENSE`) | リポジトリの clone / tarball / fork 配布物は一律 GPL-2.0 で完結 |
| 操作者が手元で fetch した `$REPO/geojson/{hoppo,takeshima}.geojson` | **CC-BY-SA 2.0 + ODbL** | **本リポジトリには同梱しない** (`.gitignore`)。`geojson/LICENSE` は「fetch されたデータに適用されるライセンス」を説明する informational ファイル |

### 13.5 補足

本構成のタイル配信ループ (週次 rebuild) は**完全ローカル動作**: OSM.jp サーバへの通信なし。OSM.jp が参照されるのは初回セットアップ時に `scripts/fetch_osmjp.py` を 1 度実行するときのみ (§5)。以降は年単位の手動リフレッシュを除き OSM.jp は不要。

リポジトリ自体は OSM.jp 由来データを 1 ビットも含まない (`.gitignore` で `geojson/*.geojson` を除外)。CC-BY-SA 2.0 / ODbL の share-alike 義務はリポジトリ配布物には発生せず、操作者が手元で fetch した時点でその操作者の手元のファイルにのみ発生する。

### 13.6 本リポジトリの改変スタイルに対する著作権ポリシー

**本リポジトリは、`scripts/patch_style.py` によるスタイル改変部分に対して新たな著作権を主張しない**。改変部分は上流と同一のライセンス (BSD 3-Clause for code / CC-BY 4.0 for design) でそのまま頒布されるものとし、本リポジトリの名称・URL・改変者表記の追加クレジットを下流配信地図に求めない。

**結果として、配信地図に表示するクレジット文字列は §13.3 / §14.3 のとおり上流 LICENSE.md の例と完全一致する**。すなわち:

- Toner-en: `© MapTiler | © OpenStreetMap contributors`
- Basic-en: `© OpenMapTiles | © OpenStreetMap contributors`

「Modified by tileserver-noborder」「Boundary-neutral rendering」等の追加クレジットは**ライセンス上不要**である (任意の付記は妨げない)。

> **背景**: `patch_style.py` の改変はすべて公開された規則による機械的変換 (boundary フィルター追加、admin_level 合流、country ラベル zoom キャップ、フォント置換、URL 絶対化等) であり、改変ロジック自体はリポジトリの GPL-2.0 で別途保護される。改変結果の style.json それ自体に新たな著作権主張を重ねる意図はない。これは上流の openmaptiles プロジェクトの credit 例 (§13.3) を尊重し、下流ユーザの帰属表記負担を増やさないためのポリシー判断である。

---

## 14. クライアント埋め込み

本サーバ (`tile.hogehoge.com`) を Web 地図に組み込む際の典型パターンと、各パターンで必要となる帰属表記をまとめる。デモは §10.3 が `MapLibre GL JS` で 1 例を示しているが、本節は実用組み込み (Leaflet ベースのサイト等) を想定したガイドである。

### 14.1 推奨パターン: MapLibre GL JS (`metadata.attribution` 自動表示)

最もシンプルで、**ライセンス義務 (帰属表記) が自動で満たされる**ため第一の選択肢として推奨する。`scripts/patch_style.py` が style.json の `metadata.attribution` に必要文言を埋め込むため、MapLibre GL JS の `AttributionControl` がそれを地図右下に自動表示する。

```html
<link href="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.css" rel="stylesheet" />
<script src="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.js"></script>
<div id="map" style="height:600px"></div>
<script>
  const map = new maplibregl.Map({
    container: 'map',
    style: 'https://tile.hogehoge.com/styles/maptiler-toner-en/style.json',
    center: [139.767, 35.681], zoom: 9
  });
  // AttributionControl はデフォルトで有効。明示する場合のみ:
  // map.addControl(new maplibregl.AttributionControl({ compact: false }));
</script>
```

利点: ベクター描画 (拡大しても綺麗)、スタイル忠実再現、attribution 自動。スタイルを `patch_style.py` で再生成すると attribution も自動追従。

### 14.2 Leaflet からの利用

Leaflet は素のままではベクタータイルを描画できない。3 つの選択肢があり、それぞれ前提条件と必要なクレジットが異なる。

#### 14.2.1 ラスタータイル直叩き (要 `serve_rendered: true`)

`L.tileLayer` で PNG タイルを取得する最もシンプルな方式。**前提**: `data/tileserver-gl/config.json` の `serve_rendered` を `true` に切り替える必要がある (本書既定は `false`、§9.5 参照)。トレードオフは tileserver-gl のメモリ使用量増 (数百 MB 以上)。

attribution 文字列はスタイルごとに異なる (§13.3 参照、上流 LICENSE.md の指示に準拠)。

**Toner-en**:
```js
L.tileLayer('https://tile.hogehoge.com/styles/maptiler-toner-en/{z}/{x}/{y}.png', {
  attribution: '<a href="https://www.maptiler.com/copyright/">&copy; MapTiler</a> | ' +
               '<a href="https://www.openstreetmap.org/copyright">&copy; OpenStreetMap contributors</a>',
  maxZoom: 14
}).addTo(map);
```

**Basic-en**:
```js
L.tileLayer('https://tile.hogehoge.com/styles/maptiler-basic-en/{z}/{x}/{y}.png', {
  attribution: '<a href="https://openmaptiles.org/">&copy; OpenMapTiles</a> | ' +
               '<a href="https://www.openstreetmap.org/copyright">&copy; OpenStreetMap contributors</a>',
  maxZoom: 14
}).addTo(map);
```

#### 14.2.2 ベクタータイル + `Leaflet.VectorGrid` プラグイン

style.json を**使わず**、ベクタータイル本体 (`.pbf`) のみを消費して自前のスタイル定義で描画する方式。`vectorTileLayerStyles` を自分で書く必要があるため、本プロジェクトの「Toner-en の見た目を維持」という意図とは外れる。**MapTiler クレジットは不要** (style を使わないため)、`OpenMapTiles` (スキーマ) と `OpenStreetMap contributors` (データ) のみで足りる。

```js
L.vectorGrid.protobuf('https://tile.hogehoge.com/data/openmaptiles/{z}/{x}/{y}.pbf', {
  attribution: '<a href="https://openmaptiles.org/">&copy; OpenMapTiles</a> | ' +
               '<a href="https://www.openstreetmap.org/copyright">&copy; OpenStreetMap contributors</a>',
  vectorTileLayerStyles: { /* 自前スタイル定義 */ }
}).addTo(map);
```

#### 14.2.3 `@maplibre/maplibre-gl-leaflet` プラグイン (Leaflet ベース推奨)

既存 Leaflet サイトに最小変更でベクタータイル + 改変 Toner-en スタイルを組み込みたい場合の最良の選択。**attribution は MapLibre が style.json から自動取得**するため手動記述不要。

```html
<link href="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.css" rel="stylesheet" />
<script src="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.js"></script>
<script src="https://unpkg.com/@maplibre/maplibre-gl-leaflet"></script>
<script>
  const map = L.map('map').setView([35.681, 139.767], 9);
  L.maplibreGL({
    style: 'https://tile.hogehoge.com/styles/maptiler-toner-en/style.json'
  }).addTo(map);
</script>
```

### 14.3 必須クレジット早見表

各上流 LICENSE.md の credit 例に基づく必要最小限のクレジット (license labels (ODbL, CC-BY 等) はクレジット文字列に含めない)。

| パターン | スタイル | 表示する credit | 記述方法 |
|---|---|---|---|
| §14.1 MapLibre GL JS | Toner-en | `© MapTiler ` + `© OpenStreetMap contributors` | **自動** (style.json `metadata.attribution`) |
| §14.1 MapLibre GL JS | Basic-en | `© OpenMapTiles ` + `© OpenStreetMap contributors` | **自動** (同上) |
| §14.2.1 Leaflet ラスター | Toner-en | `© MapTiler ` + `© OpenStreetMap contributors` | **手動** (§14.2.1 のスニペット) |
| §14.2.1 Leaflet ラスター | Basic-en | `© OpenMapTiles ` + `© OpenStreetMap contributors` | **手動** (同) |
| §14.2.2 Leaflet VectorGrid | (style 不使用) | `© OpenMapTiles ` + `© OpenStreetMap contributors` | **手動** (style 不使用のため MapTiler 不要) |
| §14.2.3 maplibre-gl-leaflet | Toner-en | `© MapTiler ` + `© OpenStreetMap contributors` | **自動** |
| §14.2.3 maplibre-gl-leaflet | Basic-en | `© OpenMapTiles ` + `© OpenStreetMap contributors` | **自動** |

> **Toner で OpenMapTiles / Stamen が不要な根拠**: 上流 [maptiler-toner-gl-style/LICENSE.md](https://github.com/openmaptiles/maptiler-toner-gl-style/blob/master/LICENSE.md) は OpenMapTiles credit 要件に対し "An exception has been granted: required copyright on this style is (C) MapTiler" と明記。`© MapTiler` のみで両方 (OpenMapTiles 要件 + design CC-BY 4.0) を満たす。Stamen Design (ISC) は「design 由来」の表示であって credit 義務ではない。

> **Basic で MapTiler が不要な根拠**: 上流 [maptiler-basic-gl-style/LICENSE.md](https://github.com/openmaptiles/maptiler-basic-gl-style/blob/master/LICENSE.md) の credit 例は `© OpenMapTiles` + `© OSM contributors` のみ。MapTiler は code (BSD 3-Clause) の copyright 保持者だが、配信地図 UI の credit には不要。

> **表示要件 (各ライセンス共通)**: 帰属表記は地図閲覧者が**画面遷移・拡大縮小なしに視認できる**位置に配置する。Leaflet の既定 `attributionControl` (右下) で要件を満たす。隠しメニューや About ページ専用への配置は不可 (ODbL 4.2 / CC-BY 4.0 3a の趣旨)。

> **HTTPS 配信前提**: 上記すべての例は本サーバが HTTPS (`tile.hogehoge.com`) で配信されている前提。HTTP の場合は Mixed Content ブロックで動かない。`patch_style.py` の `--public-url https://tile.hogehoge.com` 指定によりスタイル内 URL は絶対化済み (§9.3)。

> **BSD 3-Clause (code) の取扱い**: 配信地図 UI には不要。リポジトリ内に上流 LICENSE.md を保持しておけば足りる (本リポジトリ自体には現在同梱していないが、`styles/<id>/LICENSE.md` として配置するか、§14 から URL リンクで参照する運用で問題なし)。

> **編集姿勢の表記は任意**: 本プロジェクトは国境中立化を行うが、これはライセンス要件ではなく仕様。クレジット欄に「Boundary-neutral rendering」等を併記するかは任意。

### 14.4 どのパターンを選ぶか

| 状況 | 推奨パターン |
|---|---|
| 新規サイト、可能なら MapLibre GL JS を直接使える | **§14.1** (MapLibre GL JS 単体) |
| 既存 Leaflet サイトに後付け、見た目をスタイル通りに維持したい | **§14.2.3** (maplibre-gl-leaflet) |
| 既存 Leaflet サイトに後付け、ラスターで十分・サーバ側変更可 | **§14.2.1** (raster + `serve_rendered=true`) |
| Leaflet で完全に独自スタイル設計したい | **§14.2.2** (VectorGrid) |

---

## 付録: 前版 (合成方式) からの主な変更点

| 項目 | v1 (合成方式) | v3 (本書) |
|---|---|---|
| OSM.jp MVT の用途 | 英語化して出力 MBTiles に統合 | **バッファ計算のジオメトリ取得のみ** |
| 翻訳スクリプト | 必須 | **削除** |
| 島嶼 MBTiles ビルド | 必須 (tippecanoe / Planetiler YAML) | **削除** |
| tile-join 結合 | 必須 | **削除** |
| 最終 MBTiles 数 | 基盤 + 島嶼 = 2 個 → 結合 | **1 個 (Planetiler 単体出力)** |
| 使用スタイル | カスタム (独自 island レイヤー) | **Toner-en 改変版** |
| 海上国境線の非表示 | 白塗りオーバレイ | **黒-on-黒の自然非表示** (Toner-en 仕様) |
| 日本語→英語表記 | 翻訳辞書で明示 | **`{name:latin}` による自動転写** |
| OSM.jp へのランタイム依存 | 無し | **無し (本書の主眼)** |
| 手順のステップ数 | 10+ | **6 + 配信** |
