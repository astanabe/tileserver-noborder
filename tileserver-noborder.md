# 日本政府見解に準拠した独立型ベクタータイルサーバ構築手順 (Toner-en 版)

OpenStreetMap.jp の **Toner-en / Basic-en スタイル**の視覚仕様を踏襲しつつ、**ビルド・配信・初回セットアップのいずれでも OSM.jp に一切依存しない**自前タイルサーバ(**ベクター + ラスター**)を **Ubuntu Server 24.04.4 LTS** に構築する手順。係争地の陸上境界は行政区画線と同一描画、海上境界線は非表示、北方領土・竹島・尖閣は地物を残しつつ文字情報を除去する。

**前提条件:**

| 項目 | 値 |
|---|---|
| OS | Ubuntu Server 24.04.4 LTS |
| ログインユーザ | `foobar` (sudo 権限あり) |
| ビルド作業ディレクトリ | `/work/foobar/planetiler` |
| tileserver-gl データディレクトリ | `/home/foobar/tileserver-gl/data` |
| nginx 静的ルート | `/home/foobar/http/tile.hogehoge.com` |
| tileserver-gl 実行ユーザ | `foobar:foobar` |
| nginx 実行ユーザ | `foobar:foobar` (標準の `www-data` から変更) |
| 公開ドメイン | `tile.hogehoge.com` (A/AAAA レコードが当サーバを指す) |
| 公開ポート | TCP 80, 443 (ファイアウォール開放済み) |
| リバースプロキシ | nginx (必須) |
| SSL 証明書 | Let's Encrypt / apt の certbot + `python3-certbot-nginx` |

> **ディレクトリ分離の意図**: `/work/foobar/planetiler` はビルド作業専用 (PBF、tmp、中間 MBTiles)。配信系は一切 `/work` を参照せず、`/home/foobar/tileserver-gl/data` と `/home/foobar/http/tile.hogehoge.com` のみで完結する。リビルド時はビルド成果物 (MBTiles) をクロスファイルシステム対応のアトミック置換で配信側に反映する (§11 参照)。

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
[§1.2.4] 合流後の admin_sub / boundary_state にも maritime!=1 を AND 合成
[§1.2.3] place.class=country の maxzoom を 5 に設定
[§1.2.5] 不透明な water 塗りを boundary レイヤーの上へ移動 (= 海上の境界線を物理的に被覆)
         + transportation を water の上へ持ち上げ (橋/トンネルは残す)  ← 全海上境界線の本命解

[処理後]  描画に使われるレイヤー (下から上の順):
           ├─ admin_sub / boundary_state  (admin_level=2,3,4 を同一スタイルで描画)
           ├─ water 塗り                  (海上の境界線セグメントを被覆 = 海上では不可視)
           └─ transportation              (橋・桟橋・フェリー・トンネルは海上でも可視)
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

#### 1.2.4 合流後レイヤーでの海上国境線非表示 (maritime=1 のみ・第一段)

§1.2.2 で `admin_level=2, 3` を合流させた先の `admin_sub` / `boundary_state` レイヤーには、**最終的な描画対象となる way に対して `["!=", "maritime", 1]` フィルターが掛かっている状態**が必要。

具体例: 根室 ↔ 北方領土間の海上に引かれた `admin_level=2, maritime=1` の way は、§1.2.2 の合流により `admin_sub` / `boundary_state` レイヤーの描画対象に入ってしまう。ここで `maritime!=1` フィルターが掛かっていないと、都道府県界と同じスタイルで海上に線が引かれる。

そのため、`patch_style.py` は以下 2 段階で maritime ガードを付与する:

1. **§1.2.1 の段階**: `boundary` source-layer を参照する**全レイヤー**に maritime ガード (広く一律に)
2. **§1.2.2 の中立化の後、追加で一度**: `admin_sub` / `boundary_state` に maritime ガード (合流で新たに範囲が広がったため再確認)

実装上は `add_filter_clause()` が冪等 (既に入っていれば重複追加しない) なので、単に両方の段階で呼んでおけば安全。

> **maritime フィルターだけでは不十分 (重要)**: `maritime!=1` は OSM で `maritime=yes` とタグ付けされた境界 way しか消せない。ところが日本の**国内の海峡をまたぐ都道府県界** (本州⇔四国・本州⇔九州・九州⇔四国の瀬戸内海、北海道⇔本州の津軽海峡など) や、**北海道 ⇔ 北方領土間の係争境界**は、OSM 上 `maritime=0` (海上タグなし) であることが多く、このフィルターを素通りして海上に描画される。属性ベースのフィルターでは「全ての海上境界線」を消せない。これを完全に解決するのが次の §1.2.5(water 被覆)であり、§1.2.4 はあくまで第一段(maritime=1 の確実な除去)と位置づける。

#### 1.2.5 全海上境界線の water 被覆 (属性非依存・最終解)

§1.2.4 が取りこぼす `maritime=0` の海上境界線を含め、**水域上のあらゆる境界線を確実に非表示**にするため、レイヤー順による物理被覆を行う。両スタイルとも海 (ocean) は不透明な `water` 塗りポリゴンとして描画される(背景色ではない)ことを利用する:

1. **`water` 塗りレイヤーを `boundary` 線レイヤーの直上へ移動**する。海上の境界線セグメントは不透明な water 塗りで覆われて不可視になる。陸上には water ポリゴンが無いため、陸上の府県界は影響を受けず表示されたままになる。
2. ただし橋・桟橋・フェリー等、**水域上に正当に描かれる交通要素まで覆われてしまう**ため、`transportation` source-layer の全レイヤーを water 塗りの上へ持ち上げる。これで本州四国連絡橋などの海上の橋が再び見える。

> **トンネルについて**: このスタイルは道路の線形 (surface/bridge/tunnel) を `brunnel` 条項を持たない同一の道路・鉄道レイヤーで描く (toner には専用 tunnel レイヤーが存在しない)。そのため transportation を持ち上げると東京湾アクアラインや関門トンネル等の海底トンネル区間も一緒に可視化される。「橋は見せてトンネルだけ隠す」はレイヤー順だけでは両立不能で、**海上にトンネルが描かれることは許容**する(要件は「海上の境界線が消えること」のみ)。
>
> **water 名ラベル**: 海・湖の名前ラベル (water_name 等の symbol) は boundary より後(上)に描かれるため、移動した water 塗りより上に残り、引き続き表示される。

この処理は `patch_style.py` の `mask_sea_boundaries()` が冪等に行う(既に water が boundary より上にあれば何もしない)。レイヤーの**追加はせず移動のみ**でレイヤー数は変わらない。

### 1.3 島内は「文字情報のみ除去」— 地物 (河川・地形等) は残す

対象領域(北方領土・竹島の**陸地 + 2km バッファ**、および尖閣諸島の指定矩形 — §6 `--bbox`)内について、feature を削除するのではなく、**画面に描画される「文字」を生成するタグだけを剥がす**。具体的には `name` / `name:*` / `alt_name` 等の名称、route `ref` (路線番号シールド)、`addr:housenumber` (番地) を除去し、それ以外(ジオメトリと非文字タグ)はそのまま残す (`scripts/strip_island_labels.py`)。尖閣諸島を含めるのは、魚釣島等に中国語由来の英語表記が併記されるため。

- 結果、島には**河川・湖沼・海岸線・土地被覆・道路・建物などの地物がそのまま描画され、地名・施設名・道路名・番地などのラベルは一切出ない**。
    - Toner-en: 海 (黒) の中に、無名だが河川・道路等のディテールを持つ島
    - Maptiler-Basic-en: 海 (水色) の中に、同様の無名ディテールを持つ島
- **`--transliterate=false` (§8) との相乗**: `name:en` を持たない地物は元々ラベルが出ないため、明示的な除去が効くのは主に `name:en` を持つ地名 (集落名・島名等)。
- **境界線**: 島周辺の係争境界 way も geometry として残るが、§1.2 の中立化が適用される。海上の係争線・海峡線は §1.2.5 の water 被覆で非表示、陸上の境界は県境と同一スタイル (§1.2.2)。`maritime` フィルター (§1.2.1/§1.2.4) は `maritime=1` のみ。

処理の流れ (§7.2): 島域を `osmium extract` で別 PBF に抽出 → OPL 経由で文字タグ除去 → `osmium merge` で本体に戻す。本体側 (`clipped`) は §7 / §7.1 の clip + 残渣除去で**名前付きの島内地物を既に除去済み**なので、ラベルの供給源は除去版だけになり、ラベル漏れは起きない。

結論: **OSM.jp の `hoppo` / `takeshima` タイルは一切不要**。両スタイルから該当ソースとレイヤーを削除する。島は地物を残しつつ無名(河川・地形は見える)で描かれ、中立性は「名称・行政表記を出さない」ことで担保する。

### 1.4 独立化のためのセットアップ時ダウンロード物と公開インフラ

セットアップ時のみ 1 度だけ外部から取得し、以降のタイル配信は完全ローカルで動作:

| リソース | 取得元 | 頻度 |
|---|---|---|
| 全球 OSM PBF | planet.passportcontrol.net (国内ミラー、優先) / planet.openstreetmap.org (フォールバック) | 初回 + 年次 (rebuild) |
| 係争地の切り出し領域 (北方領土・竹島・尖閣) | **明示座標** (`scripts/rebuild.sh` 内にハードコード)。外部取得なし | (取得物ではない) |
| Planetiler jar | GitHub Releases | 初回のみ |
| 海岸線・水域ポリゴン、Natural Earth | Planetiler `--download` が自動取得 | 初回 + 年次 (rebuild) |
| Maptiler-Toner style.json | [openmaptiles/maptiler-toner-gl-style](https://github.com/openmaptiles/maptiler-toner-gl-style) `v1.0` (BSD 3-Clause + CC-BY 4.0) | 初回のみ |
| Maptiler-Toner sprite × 4 | 同リポジトリ `gh-pages` (github.io) | 初回のみ |
| Maptiler-Basic style.json | [openmaptiles/maptiler-basic-gl-style](https://github.com/openmaptiles/maptiler-basic-gl-style) `v1.10` (BSD 3-Clause + CC-BY 4.0) | 初回のみ |
| Maptiler-Basic sprite × 4 | 同リポジトリ `gh-pages` (github.io) | 初回のみ |
| フォント (Noto Sans, Nunito — 可変フォント) | [google/fonts](https://github.com/google/fonts) の `ofl/notosans`, `ofl/nunito` (OFL) — `scripts/build_fonts.sh` で instance 化 + PBF 変換 | 初回のみ |
| フォント build ツール (generate.js のみ) | [openmaptiles/fonts](https://github.com/openmaptiles/fonts) | 初回のみ |
| SSL 証明書 | Let's Encrypt (ACME HTTP-01) | 初回 + 60 日ごとに自動更新 |

> **OSM.jp 依存は完全にゼロ**:
> - 係争地 (北方領土・竹島・尖閣) の切り出し領域は `scripts/rebuild.sh` 内の**明示座標** (`buffer_clip.py --polygon` / `--bbox`) で定義し、初回・年次ともに OSM.jp を一切叩かない。`fetch_osmjp.py` / `geojson/` は legacy (`buffer_clip.py --inputs` 用・§5)。
> - OSM.jp の CC-BY-SA 2.0 由来データはビルドにも配信物にも入らない。ライセンスは §13 参照。

Let's Encrypt は**定常的な外部依存**となる (更新に 60 日周期の接続が必須) が、タイル配信自体は依然としてローカル完結。証明書を手動管理したい場合は `certbot certonly --manual` や私設 CA に切替可能。

migu1c-regular / migu2m-regular は日本語専用フォントだが、両スタイルとも `{name:latin}` を使うため日本語字形は描画されない。よってこれらの参照はスタイル改変で Noto Sans に差し替える。

### 1.5 公開構成

```
外部クライアント
    │ HTTPS (tile.hogehoge.com)
    ▼
[Cloudflare]  ← 前段 (任意)。エッジキャッシュ + DDoS 緩和 + origin 隠蔽
    │ HTTPS (Host / X-Forwarded-Proto を転送)
    ▼
[nginx]  (:443) ← certbot が /etc/letsencrypt/live/tile.hogehoge.com に証明書を配置
    │  ・TLS 終端
    │  ・proxy_cache tiles (20 GB, 7 日。.png/.pbf 等)
    │  ・CORS / セキュリティヘッダ
    │ HTTP (loopback)
    ▼
[tileserver-gl]  (127.0.0.1:8080)  ← 外部非公開。ベクター + ラスター (serve_rendered:true)
    │
    ▼
openmaptiles.mbtiles  (共通)
styles/maptiler-{toner,basic}-en/  +  sprites/maptiler-{toner,basic}-en/
fonts/  (共通, 8 書体)
```

### 1.6 処理フロー

```
[年次 rebuild ループ — OSM.jp は一切登場しない]
係争地の明示座標 (rebuild.sh にハードコード)
      │ buffer_clip.py --polygon/--bbox (世界外周 .poly + 領域 geojson)
      ▼
   world_minus_islands.poly  +  islands_buffered.geojson
      │
[global.osm.pbf] ──┤ osmium extract -p          → clipped.osm.pbf (島域カット)
                   │ 残渣 removeid (§3.5)        → 名前付き島内地物を除去
                   │ 島抽出 → strip_island_labels.py (name/ref/番地除去) → merge (§3.6)
                   ▼
            [clipped.osm.pbf]  (島は地物そのまま・文字のみ無し)
                   │ Planetiler (OpenMapTiles profile, --languages=en, --transliterate=false)
                   ▼
            [final.mbtiles]
                   │
            tileserver-gl (serve_rendered:true) + 改変 Toner-en/Basic-en スタイル
                   │
            nginx (TLS, proxy_cache, CORS) → Cloudflare

# 係争地は座標で定義。fetch_osmjp.py / geojson/ は legacy(§5)。
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

ログインユーザ `foobar` が全ての作業を実施する前提。ビルド作業用 (`/work/foobar/planetiler`) と、配信データ用 (`/home/foobar/tileserver-gl/data`)、nginx 静的ルート用 (`/home/foobar/http/tile.hogehoge.com`) の 3 ツリーを用意する。スクリプト本体は本リポジトリに集約済み (§2.3) のため、`/work/.../scripts` は作成しない。

```bash
# ビルド作業用 (大容量 SSD を想定)
sudo mkdir -p /work/$USER/planetiler/{src,pbf,geojson,mbtiles,build}
sudo chown -R $USER:$USER /work/$USER

# tileserver-gl の配信データ用 (両スタイル分の styles/sprites サブディレクトリ)
mkdir -p $HOME/tileserver-gl/data/{styles,sprites,fonts}
mkdir -p $HOME/tileserver-gl/data/styles/{maptiler-toner-en,maptiler-basic-en}
mkdir -p $HOME/tileserver-gl/data/sprites/{maptiler-toner-en,maptiler-basic-en}

# nginx 静的ルート用 (デモページや静的資産)
mkdir -p $HOME/http/tile.hogehoge.com

# 以後の作業カレントはビルド用
cd /work/$USER/planetiler
```

> **このブロックは clone 前**: まだ `deploy.env` を source できないため、`$HOME` / `$USER`(=ログインユーザ)でパスを組み立てている。リポジトリとサーバを同一ユーザで運用する前提なので、これで実ユーザのホーム配下に正しく作られる。ビルド作業ディレクトリだけは `/work/$USER/planetiler` を既定とするが、別ボリューム構成で `deploy.env` の `BUILD_ROOT` を変える場合はそのパスに読み替えること。静的ルート (`$HOME/http/<DOMAIN>`) のディレクトリ名は `$DOMAIN` と一致させる必要があり、§9 以降のインストール手順は `deploy.env` を source して `$HTTP_ROOT` 等を使う。

### 2.3 本リポジトリの配置と `$REPO` 変数

本書で参照する Python / Bash スクリプトと、systemd ユニット・nginx 設定・sudoers・tileserver-gl `config.json`・デモ HTML は、すべて本リポジトリに収録されている。以降の手順は環境変数 `REPO` が本リポジトリのチェックアウト先を指す前提で書かれている (既定値: `/home/foobar/tileserver-noborder`)。

```bash
# クローン (例: ホームディレクトリ直下)
cd $HOME
git clone https://github.com/<owner>/tileserver-noborder.git

# 本書の全コマンドブロックが参照する $REPO を設定する。
# 別の場所に clone した場合は、その絶対パスに置き換えること。
export REPO=$HOME/tileserver-noborder

# 新しいシェルを開くたびに再設定が必要になるのを避けるため、
# 作業期間中はログインシェルにも書いておくと確実 (任意):
echo "export REPO=$REPO" >> ~/.bashrc
```

> **重要 — `$REPO` は各シェルで必要**: §9.x / §10.x の多くのブロックは冒頭で `. "$REPO/deploy.env"` を実行して `$DOMAIN` 等を読み込む。`$REPO` が未設定のシェル (= clone 後に開き直した端末、SSH 再接続、`tmux`/`screen` の別ペイン等) でこれらを実行すると、`. "$REPO/deploy.env"` が `. "/deploy.env"` に展開されて失敗し、以降の `$DOMAIN` 等がすべて空になる。**作業を再開するシェルでは必ず最初に `export REPO=...` を実行する** (上記のように `~/.bashrc` に書いておけば自動化される)。
>
> なお `deploy.env` 自身も末尾で `REPO=...` を定義しているが (§3.1)、これは `scripts/render-configs.sh` と systemd ユニットの `ExecStart` 用であって、**シェルの `$REPO` を肩代わりするものではない** — `deploy.env` を source するにはその前に `$REPO` でその場所を特定できている必要があるため (鶏と卵)。対話手順では上記の `export REPO=...` が起点となる。

リポジトリの主要レイアウト:

```
$REPO/
├── deploy.env.example             § 3.1; copy to deploy.env, edit for your deployment
├── deploy.env                     gitignored; operator-edited (USER_NAME, DOMAIN, etc.)
├── staging/                       gitignored; output of scripts/render-configs.sh
│   └── {etc,data,web}/...         rendered with operator's values, ready to install
├── scripts/                       # Executable tools (Python / Bash)
│   ├── fetch_osmjp.py             § 5 (initial setup + rare refresh; only OSM.jp-touching tool)
│   ├── buffer_clip.py             § 6
│   ├── verify_buffer.py           § 6
│   ├── residual_label_ids.py      § 7 / § 7.1 (post-clip residual inspection)
│   ├── build_fonts.sh             § 9.2 (google/fonts → instance → PBF)
│   ├── patch_style.py             § 9.3
│   ├── apply_sea_mask.py          § 12.1
│   ├── render-configs.sh          § 3.1; renders etc/, data/, web/ → staging/
│   └── rebuild.sh                 § 11; sources deploy.env at runtime
├── geojson/                       # § 5; tracked: README.md + LICENSE.
│   │                              #       *.geojson are gitignored
│   │                              #       (operator fetches them via scripts/fetch_osmjp.py)
│   ├── README.md
│   └── LICENSE
├── data/tileserver-gl/            # source-of-truth template (default values)
│   └── config.json                § 9.5
├── etc/                           # source-of-truth template (default values)
│   ├── systemd/system/*.service / *.timer
│   ├── nginx/sites-available/tile.hogehoge.com{,.http-only}
│   ├── letsencrypt/renewal-hooks/deploy/reload-nginx.sh
│   └── sudoers.d/tileserver-rebuild
└── web/
    └── demo.html                  § 10.3
```

スクリプトは `$REPO/scripts/` から直接実行する (インストール不要)。配信側の設定ファイル (etc/、data/、web/) は **`scripts/render-configs.sh` で `staging/` に展開してから** `sudo install` で所定のパスに配置する (詳細は §3.1 と §9.x)。

> **プレースホルダ**: `etc/`、`data/`、`web/` 以下のファイルにはデフォルト値 (`tile.hogehoge.com`、`foobar`、`/work/foobar/planetiler` 等) が直書きされているが、これらは「テンプレート値」であって render プロセスで置換される。手動で `sed` する必要はない。

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
    libgif-dev librsvg2-dev libpixman-1-dev \
    libopengl0           # tileserver-gl (@maplibre/maplibre-gl-native) の実行時に必要

# Node.js LTS
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Python venv for $REPO/scripts/*.py
#   - shapely + pyproj   → buffer_clip.py (年次 rebuild ループに必要)
#   - requests, mercantile, mapbox-vector-tile → fetch_osmjp.py (初回セットアップ時のみ、§5)
# スクリプトと同じリポジトリ配下に置く ($REPO/venv/)。gitignore 済み。
python3 -m venv "$REPO/venv"
source "$REPO/venv/bin/activate"
pip install --upgrade pip
pip install shapely pyproj
pip install requests mercantile mapbox-vector-tile

# Planetiler
cd /work/$USER/planetiler/src
wget -q https://github.com/onthegomap/planetiler/releases/latest/download/planetiler.jar
java -jar planetiler.jar --help | head -3

# tileserver-gl をプロジェクトローカルにインストール (sudo 不要)
# $HOME/tileserver-gl を npm プロジェクトとして扱う
cd $HOME/tileserver-gl
npm init -y
npm install tileserver-gl
# ディスク小 / PNG レンダリング不要ならこちらでも可:
#   npm install tileserver-gl-light

# canvas の prebuild バイナリは自前の libpng 1.6.37 を同梱しており、
# Ubuntu 24.04 のシステム libpng 1.6.43 とミスマッチを起こして
# tileserver-gl 起動時に "libpng version mismatch" で abort する。
# ソースから再ビルドしてシステム libpng へリンクさせる。
# 上で $REPO/venv を activate しているため、node-gyp が venv の
# Python 3.12 を拾い distutils 削除にぶつかる。system python3 を明示する。
env -u VIRTUAL_ENV PATH=/usr/bin:/bin:/usr/local/bin \
    npm rebuild canvas --build-from-source --python=/usr/bin/python3

# 実行ファイルのパス確認
ls -la $HOME/tileserver-gl/node_modules/.bin/tileserver-gl
$HOME/tileserver-gl/node_modules/.bin/tileserver-gl --version
```

> **再発防止**: この `/home/foobar/tileserver-gl/` で `npm install` / `npm update` を再実行すると canvas が prebuild バイナリで上書きされ、同じ libpng 不整合が再発する。これを避けるには `.npmrc` を置いてソースビルドを強制しておくとよい:
>
> ```bash
> echo 'build_from_source=true' > /home/foobar/tileserver-gl/.npmrc
> ```

> **グローバル (`-g`) を使わない理由**: `sudo npm install -g` は `/usr/lib/node_modules/` に書き込むため特権が必要になる。プロジェクトローカルインストールなら foobar 権限で完結し、依存ライブラリ (sharp 等のネイティブビルドを伴うパッケージ) も同ユーザのキャッシュ (`~/.npm`) を使う。また、tileserver-gl をバージョンアップしたい際も `cd /home/foobar/tileserver-gl && npm update tileserver-gl` で完結する。
>
> この方式の結果、最終的なディレクトリ構成は:
>
> ```
> /home/foobar/tileserver-gl/
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

### 3.1 デプロイ設定 (`deploy.env`) と config の render

本リポジトリの `etc/`、`data/`、`web/` 以下のファイルにはデフォルト値 (`foobar` / `tile.hogehoge.com` / `/work/foobar/planetiler` 等) が直書きされている。別環境にデプロイする場合や設定を後から変更したい場合は、**ソースを直接編集せず**、`deploy.env` に変数値を書いて `scripts/render-configs.sh` で `staging/` に展開する。

```bash
cd "$REPO"

# 自分の環境用に値を編集
cp deploy.env.example deploy.env
$EDITOR deploy.env

# render: etc/, data/, web/ → staging/
./scripts/render-configs.sh
```

主要な変数 (詳細は `deploy.env.example` 参照):

| 変数 | 必須 | 用途 |
|---|---|---|
| `USER_NAME` | (自動) | systemd の `User=`/`Group=`、sudoers entry、各 `/home/$USER_NAME/...` パス。未設定なら**現在のログインユーザ (`$(id -un)`) を自動採用**(リポジトリとサーバを同一ユーザで運用する前提)。別ユーザにする場合のみ明示指定 |
| `DOMAIN` | ✓ | nginx `server_name`、Let's Encrypt パス、demo.html、nginx 設定ファイル名 |
| `ADMIN_EMAIL` | ✓ | certbot の `-m` 引数 (本書のコマンド例で使用、render 対象外) |
| `BUILD_ROOT` | (派生) | Planetiler ビルド作業ディレクトリ |
| `TILESERVER_HOME` / `TILESERVER_DATA` | (派生) | tileserver-gl の npm プロジェクトルート / データディレクトリ |
| `HTTP_ROOT` | (派生) | nginx 静的ルート (demo.html、ACME challenge) |
| `REPO` | (派生) | systemd ユニットの ExecStart パス |

>「派生」変数は `deploy.env.example` で `${USER_NAME}` / `${DOMAIN}` から自動的に組み立てる定義になっている。レイアウトを通常から外す場合のみ明示指定する。

> **render は冪等**: 既存の `staging/` を削除して再生成するため、`deploy.env` を編集して `render-configs.sh` を再実行するだけで反映される。`scripts/rebuild.sh` は `deploy.env` を**実行時に直接 source** するため、こちらは render 対象外で `staging/` には現れない。

> 既定値のまま render した場合は `staging/` 配下が `etc/`、`data/`、`web/` と完全一致する (no-op)。

---

## 4. Step 1: OSM PBF の取得

```bash
cd /work/$USER/planetiler/pbf

# 国内ミラー (planet.passportcontrol.net) を優先し、不通なら本家へフォールバック
PLANET_URL="https://planet.passportcontrol.net/pbf/planet-latest.osm.pbf"
PLANET_URL_FALLBACK="https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf"
wget -N "$PLANET_URL" -O global.osm.pbf \
  || wget -N "$PLANET_URL_FALLBACK" -O global.osm.pbf

# 整合性確認 (ダウンロード元と同じミラーの .md5 と照合。ミラーに無ければ本家)
wget -q "${PLANET_URL}.md5" -O planet-latest.osm.pbf.md5 \
  || wget -q "${PLANET_URL_FALLBACK}.md5" -O planet-latest.osm.pbf.md5
# .md5 はファイル名 planet-latest.osm.pbf を指すので global.osm.pbf 用に読み替えて照合
sed 's/planet-latest\.osm\.pbf/global.osm.pbf/' planet-latest.osm.pbf.md5 | md5sum -c -
```

- 転送量が大きい (~85 GB) ため、**国内 OSM PBF ミラー [planet.passportcontrol.net](https://planet.passportcontrol.net/pbf/) の利用を推奨**(本家 planet.openstreetmap.org はフォールバック)
- 初回のみダウンロード、以降は年次で更新 (§11 参照)。`scripts/rebuild.sh` も同じミラー優先で取得する

> **注記**: 北方領土はロシア領として、竹島は韓国領として記述される feature が存在するため、地域抽出ではこれらを取りこぼすリスクがある。本書が全球データを前提とするのはこの理由による。

---

## 5. Step 2: 島嶼ポリゴンの取得 (legacy・任意)

> **本ステップは現構成では不要**(§6 で係争地を明示座標で定義するため)。`buffer_clip.py --inputs` に GeoJSON を渡す任意の運用をする場合のみ実施する。それ以外は §5 をスキップしてよい。

島嶼ポリゴンを OSM.jp から取得する。同梱しないポリシー (`.gitignore` で `geojson/*.geojson` を除外) のため操作者が取得する。属性は使わずジオメトリのみ利用。z=10 で解像度 ~150 m。

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
source "$REPO/venv/bin/activate"
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

> **以降の rebuild では再取得不要**: 取得したファイルは `$REPO/geojson/` に保存され、年次 rebuild ループ (§11) はこれを入力として使うのみ。OSM.jp は二度と叩かない。地理的境界の更新が必要になった場合のみ、上記コマンドを再実行してファイルを上書きする (年単位の頻度)。

---

## 6. Step 3: 切り出し領域の `.poly` を生成 (座標ベース・GeoJSON 不要)

係争地の切り出し領域を**明示座標**で定義し、全球を外周・各領域を「穴」とした osmium 用 `.poly` を出力する。**外部 GeoJSON (OSM.jp) は使わない** — リポジトリ内の `scripts/buffer_clip.py` に座標を渡すだけで、ビルドは OSM.jp に一切依存しない。

| 引数 | 必須 | 意味 |
|---|---|---|
| `--polygon` | (複数可) | 任意の経緯度ポリゴン (`lon,lat;lon,lat;...`、3 頂点以上) を**バッファ無し**で領域に追加。北方領土の輪郭等 |
| `--bbox` | (複数可) | `W,S,E,N` (min_lon,min_lat,max_lon,max_lat) の経緯度矩形を**バッファ無し**で追加。竹島・尖閣等 |
| `--inputs` | (任意) | 入力 GeoJSON (和集合)。**既定では拡大なし (verbatim)**。本構成では未使用 (アドホック用に残置) |
| `--buffer-m` | (既定 `BUFFER_KM`×1000) | `--inputs` に適用する測地バッファ (メートル)。`--bbox`/`--polygon` には適用されない |

> **範囲拡大は既定で OFF**(`--inputs` も含め全領域 verbatim)。`--inputs` を拡大したい場合のみ、環境変数 **`BUFFER_KM`** に**キロメートル**で距離を指定する(例: `BUFFER_KM=2 buffer_clip.py --inputs ...` で 2 km バッファ)。`BUFFER_KM=0`(既定)で無効。`--buffer-m`(メートル)で明示上書きも可。
| `--out` | ✓ | 出力 `.poly` パス |
| `--debug` | ✓ | 領域和集合の GeoJSON (目視検証用。§3.5/§3.6 もこれを使う) |

> `--polygon` / `--bbox` のうち少なくとも 1 つ (または `--inputs`) が必要。座標の入力は `lon,lat` 順 (GeoJSON 標準)。緯度経度 (`lat,lon`) で与えられた値は読み替えること。

```bash
# BUILD_ROOT は deploy.env の値 (既定 /work/$USER_NAME/planetiler)
. "$REPO/deploy.env"
: "${BUILD_ROOT:=/work/${USER_NAME}/planetiler}"

source "$REPO/venv/bin/activate"
"$REPO/scripts/buffer_clip.py" \
    --polygon "146.10,44.90;145.26,43.85;145.51,43.49;145.83,43.41;145.88,43.32;146.59,43.14;149.60,45.19;148.70,46.10" \
    --bbox 131.84,37.22,131.89,37.26 \
    --bbox 123.29,25.59,123.77,26.02 \
    --out   "$BUILD_ROOT/build/world_minus_islands.poly" \
    --debug "$BUILD_ROOT/build/islands_buffered.geojson"
deactivate
```

各領域 (座標は `lat,lon` を `lon,lat` に読み替え済み):

- **北方領土** — `--polygon` 8 頂点。国後・択捉・色丹・歯舞を含み、本土 (根室半島・釧路) は含まない
- **竹島** — `--bbox 131.84,37.22,131.89,37.26`
- **尖閣諸島** — `--bbox 123.29,25.59,123.77,26.02`。魚釣島等に中国語由来の英語表記が併記されるため対象に含める (§1.3)

生成物:

- `$BUILD_ROOT/build/world_minus_islands.poly` — osmium に渡すクリップ定義
- `$BUILD_ROOT/build/islands_buffered.geojson` — 領域和集合の GeoJSON (目視検証用、QGIS 等)

**検証:** 領域が対象島を包含し、本土を巻き込まないことを確認する。`islands_buffered.geojson` を地図に重ねるか、Python で代表点を内外判定する:

```bash
source "$REPO/venv/bin/activate"
python3 - "$BUILD_ROOT/build/islands_buffered.geojson" <<'PY'
import json, sys
from shapely.geometry import shape, Point
r = shape(json.load(open(sys.argv[1]))["features"][0]["geometry"])
for nm,(lo,la) in {"国後 (145.86,44.05)":(145.86,44.05),"択捉 (147.7,44.9)":(147.7,44.9),
                   "色丹 (146.75,43.85)":(146.75,43.85),"歯舞 (146.15,43.43)":(146.15,43.43),
                   "竹島 (131.87,37.24)":(131.87,37.24),"尖閣 (123.47,25.74)":(123.47,25.74),
                   "[外] 根室 (145.58,43.33)":(145.58,43.33)}.items():
    print(("IN " if r.contains(Point(lo,la)) else "out"), nm)
PY
deactivate
```

> `scripts/verify_buffer.py` は `--inputs` の 2 km バッファ専用の旧検証 (択捉北端の 1/3 km 判定)。座標ベースの本構成では上記の包含チェックを使う。

---

## 7. Step 4: osmium で本体を島嶼 +2km 抜きでクリップ

> このステップ (§7 + §7.1) は本体 `clipped.osm.pbf` を**島内地物抜き**で作る。島の地物は §7.2 で**文字を剥がして**戻す(島は無名のまま地物を描く。設計は §1.3)。

```bash
. "$REPO/deploy.env"
: "${BUILD_ROOT:=/work/${USER_NAME}/planetiler}"
cd "$BUILD_ROOT"

osmium extract \
    -p build/world_minus_islands.poly \
    --strategy=smart \
    --overwrite \
    -o pbf/clipped.osm.pbf \
    pbf/global.osm.pbf
```

**検証:** バッファ領域 (`islands_buffered.geojson`) を extract マスクに使う。粗い bbox では知床・納沙布・ハボマイ周辺の北海道本土側を巻き込んでしまうので不適。

`natural=coastline` の島輪郭線は §1.3 の仕様で silhouette として残すため、検証では「ラベル/POI/道路/建物を生む tag を直接持つ feature」だけを数える。`osmium tags-filter` は暗黙的に relation member を引きずり込むため、直接タグ付きの element のみを抽出するヘルパー `scripts/residual_label_ids.py` を用いる。

```bash
# バッファ内の残存だけを抜く (simple strategy: 完全に内側のもののみ)
osmium extract --overwrite --strategy=simple \
    -p build/islands_buffered.geojson \
    -o /tmp/resid.osm.pbf pbf/clipped.osm.pbf
osmium cat /tmp/resid.osm.pbf -f opl -o /tmp/resid.opl --overwrite

# ラベル/POI 化する tag を直接持つ element の ID 一覧
"$REPO/scripts/residual_label_ids.py" --opl /tmp/resid.opl > /tmp/rm_ids.txt
echo "バッファ内の label/POI feature: $(wc -l < /tmp/rm_ids.txt)"
```

期待値は Hoppo + Takeshima 合わせて数個〜数十個程度。`smart` strategy は relation 完結性維持のため、Habomai archipelago などの named multipolygon relation が他の広域 relation のメンバー参照で保持されることがある。これらは次節でクリーンアップする。

### 7.1 (推奨) label/POI 化する残存 feature の除去

§7 で得た `/tmp/rm_ids.txt` をそのまま `osmium removeid` に渡す。リストは「直接タグ付きの element のみ」なので、coastline silhouette を巻き込む心配はない。

```bash
cd "$BUILD_ROOT"
if [[ -s /tmp/rm_ids.txt ]]; then
    osmium removeid --id-file=/tmp/rm_ids.txt \
        -o pbf/clipped_final.osm.pbf --overwrite pbf/clipped.osm.pbf
    mv pbf/clipped_final.osm.pbf pbf/clipped.osm.pbf
fi
```

削除後に §7 の検証を再実行して `rm_ids.txt` が空になっていれば完了。この時点で `clipped.osm.pbf` は**島内地物を一切含まない**(名前付きは §7.1 で除去、その他は §7 の clip で穴抜き済み)。

### 7.2 文字を剥がした島内地物を戻す (§1.3)

島を「無地のシルエット」ではなく「**地物は描くが無名**」にするため、島域を別途抽出して**文字系タグだけを剥がし**、本体に merge で戻す。文字タグ除去は `scripts/strip_island_labels.py`(OPL を読み、`name`/`name:*`/`alt_name` 等・route `ref`・`addr:housenumber` を落とし、ジオメトリと非文字タグは保持)。

```bash
cd "$BUILD_ROOT"

# 島域の全地物を抽出 (global から。clipped は島抜きなので不可)
osmium extract --overwrite --strategy=smart \
    -p build/islands_buffered.geojson \
    -o pbf/islands.osm.pbf pbf/global.osm.pbf

# OPL 経由で文字タグを除去 (osmium-tool + Python のみ。pyosmium 不要)
osmium cat pbf/islands.osm.pbf -f opl -o - --overwrite \
  | "$REPO/scripts/strip_island_labels.py" \
  | osmium cat -F opl -f pbf -o pbf/islands_notext.osm.pbf --overwrite -

# 本体 (島抜き) と文字除去済みの島を結合
osmium merge --overwrite \
    pbf/clipped.osm.pbf pbf/islands_notext.osm.pbf \
    -o pbf/clipped_with_islands.osm.pbf
mv pbf/clipped_with_islands.osm.pbf pbf/clipped.osm.pbf
```

`clipped` 側に名前付き島内地物が無い(§7.1)ため、ラベルの供給源は除去版のみ=**ラベル漏れなし**。両方に現れ得るのは無名ジオメトリ(海岸線等)だけで、`osmium merge` の重複排除はどちらを残してもラベル上は等価。

> **検証 (rebuild なしで可)**: `osmium cat pbf/islands_notext.osm.pbf -f opl | grep -c 'Tname='` 等で名称タグが消えていること、`grep -c waterway= / highway=` 等で地物が残っていることを確認できる。実際の地図反映には §8 の Planetiler 再ビルドが必要。
>
> **所要時間**: 島抽出は global (~85 GB) の全読み、merge も `clipped` (~85 GB) の読み書きを伴うため、リビルドに数十分上乗せされる(`scripts/rebuild.sh` では §3.6 として自動実行)。

---

## 8. Step 5: Planetiler で MBTiles をビルド

```bash
. "$REPO/deploy.env"
: "${BUILD_ROOT:=/work/${USER_NAME}/planetiler}"
: "${PLANETILER_XMX:=32736m}"
cd "$BUILD_ROOT"

# 全球ビルド。node map / feature store は `--storage=mmap` でディスクに展開し、
# Java ヒープは 32 GiB 直下に固定 (CompressedOops の閾値内に収めて reference
# を 4 バイトに保つ)。残余 RAM は OS の page cache として mmap アクセスを加速。
java -Xms"$PLANETILER_XMX" -Xmx"$PLANETILER_XMX" -jar src/planetiler.jar \
    --osm_path=pbf/clipped.osm.pbf \
    --download \
    --output=mbtiles/final.mbtiles \
    --force \
    --storage=mmap \
    --nodemap-storage=mmap \
    --nodemap-type=array \
    --languages=en \
    --transliterate=false \
    --building-merge-z13=false
```

**オプションの要点:**

- `--download` : Natural Earth と水域ポリゴン (海岸線) を自動取得。セットアップ時のみの外部アクセス。
- `--languages=en` : `name:LANG` 属性を出力に保持する言語。本構成は **英語 (Latin) ラベルのみ描画** (§1.1 / §9.3 `patch_style.py` の text-field 正規化によって全 text-field が `{name:latin}` に固定される) ため、`name:en` だけあれば十分。`name:latin` は `name:en` から自動派生されるので、ここに `ja,ko,ru` 等を足しても MBTiles サイズが増えるだけで描画には影響しない。
- `--transliterate=false` : **自動ローマ字化(音訳)の完全無効化**。OpenMapTiles のデフォルトでは `name:en` 等の Latin スクリプト名が無い場合、ICU による音訳で `name:latin` を生成するが、日本語・中国語・ハングル等で極めて低品質な結果を出力する (例: 東京 → "Dong Jing" 相当)。このフラグを指定すると以下の挙動になる:
    - `name:en` があれば → `name:latin = name:en` (自然な英語表記)
    - `name:en` が無く元の `name` が非 Latin のみ → **`name:latin` は空** → Toner-en の `{name:latin}` 参照で**ラベルそのものが描画されない**
    - 副次効果: 高コストな音訳処理がスキップされビルドが**数〜十数%高速化**
- `-Xms32736m -Xmx32736m` (既定、`deploy.env` の `PLANETILER_XMX` で上書き可) : Java ヒープを 32 GiB − 32 MiB に固定。2 つの効果がある:
    - **CompressedOops 有効維持**: JVM は既定で `-Xmx` が約 32 GiB を超えると 32-bit compressed object pointer を無効化し、reference サイズが 8 B に倍増する。ヒープ使用量が 10〜30% 増え、CPU キャッシュ効率も落ちる。`32736m` は閾値の直下 (32 GiB − 32 MiB) に確実に収まる保守的な値
    - **`-Xms = -Xmx`**: 起動時に最終サイズまで確保。ヒープ拡張時の stop-the-world GC pause を排除し、ビルド中の RAM 配分を一定に保つ
- `--storage=mmap`, `--nodemap-storage=mmap`, `--nodemap-type=array` : node map (〜15 GB) と feature store (〜100 GB) を mmap でディスク展開する設定。ホットページは OS page cache が保持し、Java ヒープからは独立して管理される。

**RAM 実装別の推奨値 (全球ビルド、`--storage=mmap`):**

| RAM 実装 | `PLANETILER_XMX` | CompressedOops | page cache 余地 | 備考 |
|---|---|:---:|---:|---|
| 32 GB | `12g` | ✓ | ~18 GB | ぎりぎり。ビルドに数時間 |
| **64 GB** | **`32736m`** | **✓** | **~28 GB** | **本書推奨** |
| 128 GB+ | `32736m` | ✓ | ~90 GB | tmp ほぼ全部オンメモリで高速 |
| 128 GB+ (別案) | `100g` + `--storage=ram` | ✗ | 少 | node map 全部 heap で最速 (ただし CompressedOops off) |

> **動作確認**: `java -Xmx32736m -XX:+PrintFlagsFinal -version 2>&1 \| grep UseCompressedOops` で `bool UseCompressedOops = true` となれば OK。

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

> **`-en` サフィックスについて**: 上流の style.json はラベルで `{name:latin}` を直接参照する英語ベース構成のため、別途「英語版」があるわけではない。ローカル配信ディレクトリ名の慣例として `-en` を付け、「Latin スクリプト固定描画」の意図を示す。

公開 URL:
- `https://tile.hogehoge.com/styles/maptiler-toner-en/style.json`
- `https://tile.hogehoge.com/styles/maptiler-basic-en/style.json`

```bash
cd $HOME/tileserver-gl/data
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

### 9.2 フォントの取得 (Google Fonts 可変フォントから instance → PBF 化)

上流 style.json が参照するフォント:

| スタイル | 参照フォント |
|---|---|
| `maptiler-basic-en` | `Noto Sans Regular`、`Noto Sans Bold` |
| `maptiler-toner-en` | `Noto Sans Italic`、`Noto Sans Bold Italic`、`Nunito Regular`、`Nunito Bold`、`Nunito Semi Bold`、`Nunito Extra Bold` |

合計 **8 書体**。`maptiler-toner-en` はズーム補間の `text-font` stops 内で `Nunito Regular` / `Nunito Bold` も参照する（例: `stops:[[3,["Nunito Regular"]],[4,["Nunito Bold"]]]`）。ブラウザのベクター描画は欠落フォントを寛容に無視するが、**`serve_rendered: true`（§9.5）のサーバ側ラスター描画は、参照フォントが 1 つでも欠けるとそのタイルを `Failed to load glyph range ... Invalid range` で 500 にする**。したがって Nunito は 4 ウェイトすべてを生成する必要がある。

§9.3 の `patch_style.py` で全 `text-field` が `{name:latin}` に正規化されるため、**描画されるのは Latin (+ 可変フォントに含まれる Greek/Cyrillic) のみ**。CJK や Arabic 等の glyph は一切不要で、font stack は合計 ~16 MB に収まる。

tileserver-gl が必要とするのは各書体の **PBF font stack** (`0-255.pbf`、`256-511.pbf`、…、計 256 ファイル／書体) で、これは TTF を [fontnik](https://github.com/mapbox/node-fontnik) で SDF レンダリングして生成する。

**調達方針:**

- **TTF 源**: Google Fonts 公式 monorepo ([`google/fonts`](https://github.com/google/fonts)) に置かれている 3 つの可変フォントファイルから、`fontTools.varLib.instancer` で必要なウェイト / italic 組み合わせだけを静的 TTF に instance 化する。
    - `ofl/notosans/NotoSans[wdth,wght].ttf` → Regular (wght=400), Bold (wght=700)
    - `ofl/notosans/NotoSans-Italic[wdth,wght].ttf` → Italic, Bold Italic
    - `ofl/nunito/Nunito[wght].ttf` → Regular (wght=400), Bold (wght=700), Semi Bold (wght=600), Extra Bold (wght=800)
- **ビルドツール**: [openmaptiles/fonts](https://github.com/openmaptiles/fonts) の `generate.js` のみを取得。同 repo の fonts.json / 同梱 TTF は一切使わない (CJK や不要スクリプトを引き込んでしまうため)。
- **fontTools のみ Python 依存**が追加されるが、自動インストールされる。

上記を一発で実行するのが `scripts/build_fonts.sh`:

```bash
"$REPO/scripts/build_fonts.sh"
# 所要 1–2 分 (npm install + 可変フォントダウンロード + fontnik で 8 書体 × 256 レンジ SDF 化)
```

生成物は `/home/foobar/tileserver-gl/data/fonts/` の 8 ディレクトリ（各 1.4–2.3 MB、合計 ~16 MB）。`OUTPUT_DIR` 環境変数で別パスを指定可能。

> **フォントの補足**: `patch_style.py` の `migu*` 置換 (§9.3) は上流スタイルでは no-op(`migu1c/migu2m` 参照が無いため)。`Nunito Regular` / `Nunito Bold` は上流 toner が参照するため**生成が必要**(上の表のとおり)。

### 9.3 スタイルの独立化 + 境界線中立化パッチ

以下 10 項目を一括で両スタイルに適用する Python スクリプト。

1. `openmaptiles` ソースの URL を mbtiles スキームに変更
2. `hoppo` / `takeshima` ソースを削除 → OSM.jp へのランタイム依存を排除
3. それらを参照する 5 つのレイヤー (`island-hoppo`, `island-hoppo-name`, `island-takeshima`, `island-takeshima-name`, `island-takeshima-poi`) を削除
4. `migu1c-regular` / `migu2m-regular` → `Noto Sans Regular` に置換
5. **全 `text-field` を `{name:latin}` に正規化** → 上流スタイルの `{name:latin} {name:nonlatin}` 表記から nonlatin 部分を除去し、英語ラベルのみ描画 (§1.1 設計意図・§8 `--transliterate=false` と整合。非 Latin フォントを font stack から外せる)
6. **`boundary` レイヤーの全フィルターに `["!=", "maritime", 1]` を追加** → `maritime=1` の海上国境線を非表示 (第一段。§1.2.1/§1.2.4)
7. **`admin_level` ≤ 2 を描画する国境専用レイヤーを削除し、`admin_level` = 2, 3 を州/県境 (`admin_level` = 4) のレイヤーに合流** → 陸上の国境線も州/県境と同一スタイルで描画される
8. **国名ラベル (`place.class=country`) の `maxzoom` を 5 に制限** → z0–4 のみ表示、z5 以上では非表示
9. **`water` 塗りを `boundary` 線レイヤーの直上へ移動し、`transportation` を `water` の上へ持ち上げ** → `maritime=0` を含む**全ての海上境界線**を water 被覆で非表示にしつつ、海上の橋・桟橋・フェリー・トンネルは可視のまま (§1.2.5)
10. `sprite` / `glyphs` を tileserver-gl がローカル解決できる相対パス (`<style-id>/sprite`、`{fontstack}/{range}.pbf`) に書き換え (絶対 URL は埋め込まない)。glyphs に `fonts/` プレフィックスは付けない — tileserver-gl が描画時に `fonts://` を、配信時に `local://fonts/` を自前で前置するため、付けると描画パスが二重 `fonts/` になりラスタータイルが 500 になる

> **パッチ後の挙動**
>
> | 項目 | 結果 |
> |---|---|
> | 海上国境線 (`maritime=1`) | maritime フィルターで非描画 (第一段) |
> | 海上の境界線全般 (`maritime=0` 含む) | **water 塗りを上に重ねて被覆 → 全て非表示 (§1.2.5)** |
> | 陸上国境線 (admin_level=2,3) | 都道府県/州界 (admin_level=4) と同一スタイルで描画 |
> | 国の区別 | 境界線では区別できない (どの行政境界も同じ破線) |
> | 国の位置 | 低ズーム (z0–4) のみ国名表示、z5 以上は非表示 |
>
> 拡大時は「これは国境線ではなく行政区画線」という立場を取れ、国の形自体は図示されない。maritime フィルターだけでは `maritime=0` の海上線 (国内海峡の県境、北海道⇔北方領土の係争線等) を取りこぼすため、§1.2.5 の water 被覆を併用する。陸続きの係争地 (カシミール等) も陸上は県境化・海上は water 被覆で消える汎用実装。

本リポジトリ内の `scripts/patch_style.py` がこれらすべてを冪等に適用する。両スタイルへの適用を以下のループで行う:

```bash
for STYLE in maptiler-toner-en maptiler-basic-en; do
    "$REPO/scripts/patch_style.py" \
        --input  $HOME/tileserver-gl/data/styles/${STYLE}/style.json \
        --output $HOME/tileserver-gl/data/styles/${STYLE}/style.json \
        --style-id ${STYLE} \
        --mbtiles-id openmaptiles
done
```

> **ドメインは焼き込まない**: 項目 10 の通り、`patch_style.py` は `sprite` / `glyphs` を**ローカル相対パス**として書き込み、配信ドメインは一切埋め込まない。配信される style.json の絶対 URL 化は **tileserver-gl が行う** — nginx が転送する `Host` / `X-Forwarded-Proto` (§9.7.5 の `proxy_set_header`) からリクエスト元のドメインを使って `sprite` / `glyphs` を `https://<DOMAIN>/...` に展開する。このため on-disk の style.json はドメイン非依存で、`$DOMAIN` を変えても再パッチ不要。
>
> **重要 (tileserver-gl 5.x)**: `sprite` に絶対 http(s) URL を書くと、tileserver-gl は sprite を自前配信対象として登録せず、`/styles/<id>/sprite.json` が `400 Bad Sprite ID or Scale` になる (§12)。必ずローカル相対パス (本スクリプトの既定動作) のままにすること。

実行ログには各スタイルについて以下の数値が出力される: 削除されたレイヤー数 (`country-only layers removed` = 2)、合流パッチが当たったレイヤー数 (`sub-national layers merged` = 1)、`maxzoom=5` を設定した国名ラベル数 (`country labels`)、maritime ガードを付与した boundary レイヤー数 (`boundary layers w/ maritime-guard`)、water を boundary の上へ移動したか (`water raised above boundaries`)、water マスクの上へ持ち上げた transportation レイヤー数 (`transportation lifted over mask`)。

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
| `mask_sea_boundaries(style)` | §1.2.5: `water` を boundary の上へ移動し、`transportation` を `water` の上へ持ち上げる (海上境界線を被覆、橋/トンネルは残す)。冪等・移動のみ |

> 実行後、各スタイルの `boundary` 関連レイヤー (Toner-en で 3 個、Maptiler-Basic-en で 3 個) すべてに maritime ガードが入る。また両スタイルから OSM.jp 依存 (hoppo/takeshima ソースと 5 レイヤー) が削除される。

### 9.4 MBTiles を配置

ビルド作業ディレクトリ (`/work`) と配信ディレクトリ (`/home`) が別ファイルシステムの可能性があるため、**クロスファイルシステム対応のアトミック配置**を行う。`cp` で配信側にコピー後、同一 FS 内で `mv` rename する (`rename(2)` は同一 FS 内でのみアトミック)。

```bash
# 配信側に一度コピー (この時点では .new という仮ファイル名)
cp /work/$USER/planetiler/mbtiles/final.mbtiles \
   $HOME/tileserver-gl/data/openmaptiles.mbtiles.new

# 同一 FS 内 rename でアトミック置換
mv -f $HOME/tileserver-gl/data/openmaptiles.mbtiles.new \
      $HOME/tileserver-gl/data/openmaptiles.mbtiles
```

初回配置時は rename 元ファイルが無い状態からでも同じコマンドで問題なく動作する。

### 9.5 tileserver-gl の config.json

render 出力の `staging/data/tileserver-gl/config.json` を tileserver-gl のデータディレクトリに配置する:

```bash
. "$REPO/deploy.env"   # $USER_NAME, $TILESERVER_DATA を読み込む

install -m 0644 -o "$USER_NAME" -g "$USER_NAME" \
    "$REPO/staging/data/tileserver-gl/config.json" \
    "$TILESERVER_DATA/config.json"
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
| `https://tile.hogehoge.com/data/openmaptiles/{z}/{x}/{y}.pbf` | `openmaptiles.mbtiles` のタイル本体 (ベクター) |
| `https://tile.hogehoge.com/styles/{id}/{z}/{x}/{y}.png` | サーバ側描画ラスター (要 `serve_rendered: true`) |
| `https://tile.hogehoge.com/styles/{id}.json` | ラスター TileJSON (XYZ URL を含む) |
| `https://tile.hogehoge.com/fonts/{fontstack}/{range}.pbf` | `fonts/{fontstack}/{range}.pbf` |

> **`serve_rendered: true`（本書既定）**: サーバ側でラスター (PNG) タイルを描画する。これにより `/styles/{id}/{z}/{x}/{y}.png` の XYZ ラスター URL とランディングページのラスタービューアが有効になり、**ラスター専用クライアント（例: WordPress「Leaflet Map」プラグイン、§14.2.1）から利用できる**。描画は外部に出ず、ローカルの mbtiles + スタイル + フォント + sprite だけで完結する（OSM.jp 非依存は維持）。
>
> **前提と代償**:
> - サーバ側描画は `@maplibre/maplibre-gl-native`（GLX/X11 + OpenGL 前提）を使う。**ヘッドレスサーバでは仮想 X ディスプレイ（Xvfb）が必須**で、無いと起動時に `Failed to open X display` で異常終了する。§9.6 の systemd ユニットが `xvfb-run` で起動する。
> - フォントは**参照される全書体（§9.2 の 8 書体すべて）が揃っている必要がある**。1 つでも欠けるとラベル付きタイルが 500 になる（ベクター配信は寛容だが描画は厳格）。
> - オンザフライ描画は CPU/メモリを消費する（数百 MB 〜）。ただし nginx が `.png` をキャッシュ（§9.7.5、7 日）するため 2 回目以降は軽い。
> - ラスターが不要でメモリを切り詰めたい場合は `data/tileserver-gl/config.json` の `serve_rendered` を `false` にする（Xvfb も不要になる）。

### 9.6 systemd ユニット

tileserver-gl はループバック専用バインドとし、外部公開は後段の nginx に任せる (TLS 終端と CORS / キャッシュを nginx 側で一元管理するため)。

> **Xvfb 前提 (`serve_rendered: true` のため)**: 本ユニットの `ExecStart` は `serve_rendered: true`（§9.5）のサーバ側ラスター描画に必要な X ディスプレイを与えるため、tileserver-gl を `xvfb-run` で起動する（`Environment=LIBGL_ALWAYS_SOFTWARE=1` で Mesa ソフトウェア描画を強制）。入れずに `serve_rendered: true` のまま起動すると `Failed to open X display` で異常終了する。
>
> ```bash
> sudo apt install -y xvfb libgl1-mesa-dri
> ```
>
> - **`xvfb`** が必須（仮想 X ディスプレイ。依存で Mesa の GL スタックも入る）。
> - 描画が実際にロードするソフトウェア GL は `libGLX_mesa.so`（`libglx-mesa0`）+ `libgallium*.so`（`mesa-libgallium`）で、通常は GL スタックと共に導入済み。最小構成のサーバで欠ける場合に備え、慣用的に **`libgl1-mesa-dri`** を併せて入れておくと確実（このパッケージ自体の DRI モジュールは GLX パスでは未ロードだが、Mesa ソフト GL 一式を揃える保険）。
> - `mesa-utils`（`glxinfo`/`glxgears` の診断用）と `bumblebee`（NVIDIA Optimus 用）は **不要**。
>
> ラスターが不要なら `serve_rendered: false`（§9.5）にし、ユニットの `xvfb-run …` を素の `ExecStart=…/tileserver-gl …` に戻してもよい（その場合 Xvfb 不要）。

render 出力の systemd ユニットを配置する:

```bash
. "$REPO/deploy.env"   # $USER_NAME, $TILESERVER_DATA を読み込む

sudo install -m 0644 -o root -g root \
    "$REPO/staging/etc/systemd/system/tileserver-gl.service" \
    /etc/systemd/system/tileserver-gl.service

sudo chown -R "$USER_NAME:$USER_NAME" "$TILESERVER_DATA"
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

#### 9.7.1a nginx 実行ユーザをログインユーザに変更

Ubuntu の apt nginx は既定で `www-data:www-data` で動作する。本構成ではキャッシュ・ログ・静的配信ツリーを全てデプロイユーザ (`$USER`、例: `foobar`) が所有するため、nginx ワーカーも同ユーザに降格させて権限問題を回避する。

```bash
# /etc/nginx/nginx.conf の user ディレクティブを書き換え
sudo sed -i "s|^user .*;|user $USER $USER;|" /etc/nginx/nginx.conf
grep ^user /etc/nginx/nginx.conf
# -> user <あなたのログインユーザ> を 2 回

# ログディレクトリのオーナーを変更 (既存ログは保持)
sudo chown -R $USER:$USER /var/log/nginx

# logrotate がローテート後の新規ログを適切なオーナーで作成するよう設定を更新
sudo sed -i \
    -e "s|create 0640 www-data adm|create 0640 $USER adm|" \
    -e "s|create www-data adm|create $USER adm|" \
    /etc/logrotate.d/nginx

# pid ファイルの配置先 /var/run/nginx.pid は tmpfs 上で systemd 管理なのでそのまま

# 構文検査 + 再起動
sudo nginx -t
sudo systemctl restart nginx

# ワーカーが $USER 権限で動作していることを確認
ps -eo user,pid,cmd | grep 'nginx:' | grep -v grep
# -> master は root、worker は $USER になる

# $HOME のアクセス権が nginx ワーカーから辿れるか確認
# (同一ユーザで動くのでまず問題ないが、後で他ユーザ運用に切り替える場合の保険)
ls -ld $HOME
# drwxr-x--- 程度でよい。o+x (755) は不要
```

> **バインド権限**: TCP 80 / 443 は特権ポートだが、nginx の master プロセスは `root` で起動してから `user` ディレクティブで指定されたユーザにワーカーを降格するため、`foobar` でも問題なく listen できる。systemd unit の `ExecStart` も変更不要。

#### 9.7.2 DNS と FW 事前条件

- `tile.hogehoge.com` の A / AAAA レコードが本サーバのグローバル IP を指していること
- TCP 80 / 443 が外部から到達可能であること (certbot の HTTP-01 challenge は 80 番を使用)

```bash
. "$REPO/deploy.env"   # $DOMAIN を読み込む

# 事前確認
dig +short "$DOMAIN"
curl -I "http://$DOMAIN/"  # nginx デフォルトページが見えれば OK
```

#### 9.7.3 nginx 初期設定 (HTTP のみ)

certbot を走らせる前に、server_name が正しく認識されるよう最低限の HTTP 設定を置く。render-configs.sh で `staging/etc/nginx/sites-available/$DOMAIN.http-only` が出力される (§3.1)。これを `$DOMAIN` という名前で配置する (`.http-only` サフィックスは付けない):

```bash
. "$REPO/deploy.env"   # $DOMAIN を読み込む

sudo install -m 0644 -o root -g root \
    "$REPO/staging/etc/nginx/sites-available/$DOMAIN.http-only" \
    "/etc/nginx/sites-available/$DOMAIN"

sudo ln -sf "/etc/nginx/sites-available/$DOMAIN" /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

#### 9.7.4 SSL 証明書取得 (Let's Encrypt)

```bash
. "$REPO/deploy.env"   # $DOMAIN, $ADMIN_EMAIL を読み込む

sudo certbot --nginx \
    -d "$DOMAIN" \
    --agree-tos \
    -m "$ADMIN_EMAIL" \
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

certbot 実行後、render 出力の `staging/etc/nginx/sites-available/$DOMAIN` で同名ファイルを上書きする。この設定には HTTP→HTTPS リダイレクト、proxy_cache (`/var/cache/nginx/tiles`、20 GB / 7 日)、CORS、`/demo.html` の nginx 直接配信 (§10.3) がすべて含まれる。

```bash
. "$REPO/deploy.env"   # $DOMAIN, $USER_NAME を読み込む

sudo install -m 0644 -o root -g root \
    "$REPO/staging/etc/nginx/sites-available/$DOMAIN" \
    "/etc/nginx/sites-available/$DOMAIN"

sudo mkdir -p /var/cache/nginx/tiles
sudo chown -R "$USER_NAME:$USER_NAME" /var/cache/nginx/tiles

sudo nginx -t && sudo systemctl reload nginx
```

> **certbot が自動編集した内容との関係**: `certbot --nginx` (§9.7.4) は §9.7.3 で配置した HTTP-only 設定に SSL ディレクティブを追記するが、本ステップでは設定ファイル全体を本リポジトリ版で**上書き**する。本リポジトリ版には `ssl_certificate` / `ssl_certificate_key` / `include /etc/letsencrypt/options-ssl-nginx.conf;` / `ssl_dhparam` が直書きされているため、certbot の編集結果は失われても問題ない。証明書ファイル自体は `/etc/letsencrypt/live/tile.hogehoge.com/` に残っており、設定が参照する。

#### 9.7.6 疎通確認

```bash
. "$REPO/deploy.env"   # $DOMAIN を読み込む

# HTTPS 到達
curl -IL "https://$DOMAIN/"

# 両スタイルの JSON
curl -s "https://$DOMAIN/styles/maptiler-toner-en/style.json" | jq '.sources | keys'
curl -s "https://$DOMAIN/styles/maptiler-basic-en/style.json" | jq '.sources | keys'

# CORS 確認
curl -I -H "Origin: https://example.com" \
    "https://$DOMAIN/data/openmaptiles/10/897/407.pbf" \
    | grep -i "access-control"

# キャッシュ動作 (2 回目は X-Cache-Status: HIT)
curl -s -o /dev/null -D - "https://$DOMAIN/data/openmaptiles/10/897/407.pbf" | grep -i x-cache
curl -s -o /dev/null -D - "https://$DOMAIN/data/openmaptiles/10/897/407.pbf" | grep -i x-cache
```

#### 9.7.7 証明書更新時の nginx リロード

certbot は更新成功時に `/etc/letsencrypt/renewal-hooks/deploy/` 配下のスクリプトを実行する。本リポジトリ収録の `etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` を配置する:

```bash
sudo install -m 0755 -o root -g root \
    "$REPO/staging/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh" \
    /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

#### 9.7.8 スタイル内部 URL の確認

§9.3 で `sprite` / `glyphs` は**ローカル相対パス**で書かれており (on-disk はドメイン非依存)、絶対 URL 化は tileserver-gl が配信時にリクエストの `Host` / `X-Forwarded-Proto` から行う。したがって確認は **nginx 経由で配信される** style.json に対して行う (on-disk ファイルを直接見ても相対パスのままで正しい)。

```bash
. "$REPO/deploy.env"   # $DOMAIN を読み込む

# 配信される style.json の sprite/glyphs が https://$DOMAIN/... の絶対 URL に展開されている
for STYLE in maptiler-toner-en maptiler-basic-en; do
    echo "=== ${STYLE} ==="
    curl -s "https://$DOMAIN/styles/${STYLE}/style.json" | jq '{sprite, glyphs}'
done
```

想定出力例 (`maptiler-toner-en`、`$DOMAIN=tile.hogehoge.com` の場合):

```json
{
  "sprite": "https://tile.hogehoge.com/styles/maptiler-toner-en/sprite",
  "glyphs": "https://tile.hogehoge.com/fonts/{fontstack}/{range}.pbf"
}
```

絶対 URL に展開されない場合は §9.7.5 の nginx 設定で `proxy_set_header Host $host;` と `proxy_set_header X-Forwarded-Proto https;` が両 `location` に入っているか確認する。`sprite` がローカル相対パスでなく絶対 http URL になっている (= sprite が `400` になる) 場合は §9.3 の patch_style.py を再実行し、`sudo systemctl restart tileserver-gl` を実施する (§12 の sprite 400 の項も参照)。

---

## 10. 動作確認

### 10.1 エンドポイントの疎通

まずループバック側 (tileserver-gl 直接) で疎通、次に公開側 (nginx 経由 HTTPS) で疎通を確認する。

```bash
. "$REPO/deploy.env"   # $DOMAIN を読み込む

# --- 内部 (127.0.0.1:8080) ---
for STYLE in maptiler-toner-en maptiler-basic-en; do
    echo "=== ${STYLE} ==="
    curl -s http://127.0.0.1:8080/styles/${STYLE}/style.json | jq '.sources | keys'
    # -> ["openmaptiles"]  だけが含まれ、hoppo/takeshima が無いこと
done

curl -s http://127.0.0.1:8080/data/openmaptiles.json | jq '.vector_layers[].id'
# -> water, boundary, place, transportation 等が並ぶ

# --- 外部 (https://$DOMAIN) ---
for STYLE in maptiler-toner-en maptiler-basic-en; do
    curl -sI "https://$DOMAIN/styles/${STYLE}/style.json"
    # -> HTTP/2 200
done

curl -sI "https://$DOMAIN/data/openmaptiles/10/897/407.pbf"
# -> HTTP/2 200
```

### 10.2 島嶼領域が空であることの確認

```bash
. "$REPO/deploy.env"   # $DOMAIN を読み込む

# 択捉島中心付近の z=12 タイルが空であることを確認
# tile coord ≈ (3641, 1472) at z=12 for (148.0, 44.5)
curl -s "https://$DOMAIN/data/openmaptiles/12/3641/1472.pbf" -o /tmp/t.pbf
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
        $HOME/tileserver-gl/data/styles/${STYLE}/style.json
done
```

出力が空 (null 以外何も出ない) であれば、国境専用レイヤーが全て削除されている。

**(B) 州/県境レイヤーに admin_level=2, 3 が合流している (§1.2.2)**

```bash
for STYLE in maptiler-toner-en maptiler-basic-en; do
    echo "=== ${STYLE} ==="
    jq '.layers[] | select(.id == "admin_sub" or .id == "boundary_state") | {id, filter}' \
        $HOME/tileserver-gl/data/styles/${STYLE}/style.json
done
```

`filter` 内の admin_level に関する条件 (`["in","admin_level",...]`) に 2, 3, 4 がすべて含まれていれば OK。

**(C) 合流後レイヤーに maritime!=1 が掛かっている (§1.2.4 — 第一段フィルター)**

上記 (B) と同じコマンドで、`admin_sub` / `boundary_state` の `filter` に `["!=","maritime",1]` が**含まれていることを目視確認**。これは `maritime=1` の海上境界 way を抑止する第一段フィルター。ただし `maritime=0` の海峡県境・係争線はこれを素通りするため、最終的な海上境界線の抹消は (E) の water 被覆 (§1.2.5) が担う。両方が揃っている必要がある。

ワンライナー確認:

```bash
for STYLE in maptiler-toner-en maptiler-basic-en; do
    for LAYER in admin_sub boundary_state; do
        result=$(jq -r --arg l "$LAYER" \
            '.layers[] | select(.id == $l) | .filter | tostring | contains("\"!=\",\"maritime\",1") // false' \
            $HOME/tileserver-gl/data/styles/${STYLE}/style.json)
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
        $HOME/tileserver-gl/data/styles/${STYLE}/style.json
done
```

`class=country` を含む symbol レイヤーの `maxzoom` が `5` になっていれば OK。

**(E) 海上境界線の water 被覆が効いている (§1.2.5)**

`water` 塗りレイヤーが `boundary` 線レイヤーより後(上)にあり、`transportation` がさらにその上にあることを確認する。属性ではなくレイヤー順で海上境界線を消すため、この順序が崩れていると海上線が再び現れる。

```bash
for STYLE in maptiler-toner-en maptiler-basic-en; do
    echo "=== ${STYLE} ==="
    jq -r '
      (.layers | map(.["source-layer"]=="boundary" and .type=="line") | index(true)) as $b
      | (.layers | map(.id=="water") | index(true)) as $w
      | (.layers | to_entries | map(select(.value["source-layer"]=="transportation") | .key) | min) as $t
      | "boundary(line) idx=\($b)  water idx=\($w)  transportation 最小 idx=\($t)  -> " +
        (if $b < $w and $t > $w then "OK" else "NG (順序破綻)" end)
    ' $HOME/tileserver-gl/data/styles/${STYLE}/style.json
done
```

両スタイルで `boundary < water < transportation` (= `OK`) であれば、海上の境界線は water で被覆されて非表示、海上の橋等は可視。なお on-disk ではなく配信側 (`curl https://$DOMAIN/styles/.../style.json`) で見ても順序は同じ。

### 10.3 ブラウザでの表示確認

デモページは tileserver-gl の配信下ではなく、**nginx が静的ファイルとして直接配信**する場所 (§2.2 で作成済み) に置く。これにより、tileserver-gl が落ちていてもデモは到達可能。`/demo.html` の location ブロックは §9.7.5 の nginx 設定にすでに含まれているため、本節では HTML ファイルの配置のみで足りる。

```bash
. "$REPO/deploy.env"   # $USER_NAME, $HTTP_ROOT を読み込む

install -m 0644 -o "$USER_NAME" -g "$USER_NAME" \
    "$REPO/staging/web/demo.html" \
    "$HTTP_ROOT/demo.html"
```

ブラウザで `https://<DOMAIN>/demo.html#7/44.4/146.2` (`<DOMAIN>` は `deploy.env` の `$DOMAIN`) を開き、右上のラジオボタンで両スタイルを切り替えて以下を確認:

**共通確認項目 (両スタイルで期待される状態)**

| ズーム域 | 確認項目 | 期待される状態 |
|---|---|---|
| z=3 (#3/35/138) | 世界俯瞰 | **境界線は一切描かれない**。国名ラベルだけが大陸上に浮かぶ (`Japan`, `Russia`, `China` 等) |
| z=5 (#5/44/145) | 国境付近にズーム | 国名ラベルが消える。陸上の日露国境・日韓国境相当の線は **都道府県界と同じ破線・同じ色** で描画 (スタイルにより色は異なる) |
| z=7 (#7/44.4/146.2) | 北方領土 | 河川・地形・道路等の**地物は描画されるが、地名・施設名・道路名などのラベルは一切出ない**(§1.3 の文字除去) |
| z=7 (#7/37.24/131.87) | 竹島 | 同様に、地物は描画されラベルのみ非表示 |
| z=7 | 択捉島付近の海上国境線 | **見えない** (§1.2.5 の water 被覆で非描画) |
| z=5–7 | 北海道⇔本州・本州⇔四国・本州⇔九州・九州⇔四国の海峡 | **海上の県境が一切見えない** (これらは `maritime=0` だが §1.2.5 の water 被覆で消える) |
| z=10+ | 本州四国連絡橋・東京湾アクアライン等 | **橋・トンネルは見える** (transportation は water マスクの上に持ち上げ済み。§1.2.5) |
| z=7 | 北海道本土内の都道府県境 | **陸上国境と同じスタイル**で描画 (国境と県境が視覚的に区別できない) |
| z=7 | カシミール地方を表示 (#7/34/76) | インド-パキスタン-中国の係争境界が**県境と同じスタイル**で描画 (「これは国境線ではなく行政区画線です」)。海上部分は water 被覆で非表示 |
| z=7 | 地名ラベル | `name:en` がある都市は英語表示。`name:en` が無い小集落・山名等はラベル非描画 (`--transliterate=false`) |

**スタイル別の見え方**

| スタイル | 島のシルエット色 | 海の色 | 境界線 (z5+) の色 |
|---|---|---|---|
| Maptiler-Toner-en | 白 (`#fff`) | 黒 | 黒系の破線 (高コントラスト) |
| Maptiler-Basic-en | 薄ベージュ (`hsl(47,26%,88%)`) | 水色 (`hsl(205,56%,73%)`) | 中間グレー半透明の破線 (`hsla(0,0%,60%,0.5)`) |

島嶼の地色 (land 塗り) が背景色である点は両スタイル共通で、その上に無名の河川・道路・地形が描かれる (§1.3)。島の存在ごと完全に海と同化させたい場合は §12.1 参照。

---

## 11. 運用: 定期更新

OSM 本体は日々更新されるが、本サーバは情報の最新性より運用負荷の軽さを優先し、**年次 (1 月 1 日)** に完全リビルドを行う。ビルド所要は数時間オーダーで、その間 47 分の PBF クリップ + 数時間の Planetiler + 配信切替という構成のため、低頻度のバッチ運用が合理的。島嶼ポリゴン (`$REPO/geojson/`) は初回セットアップ時に手元に取得済み (§5)、以降は年次 rebuild で再利用するのみで OSM.jp は年次ループに登場しない (§1.4 参照)。ビルド処理は `$USER_NAME` 権限で実施し、MBTiles の置換と tileserver-gl の再起動、および nginx キャッシュ破棄のみ特権操作とする。

### 11.1 リビルドスクリプト本体

リポジトリ内の `scripts/rebuild.sh` を使用する。インストール不要 (systemd サービスがリポジトリ内のパスを直接指す)。スクリプトは以下を順に実行する:

0. **`$REPO/deploy.env` を `source`** して `USER_NAME` / `BUILD_ROOT` / `TILESERVER_DATA` 等を読み込む。`REPO` はスクリプト自身の配置パスから自動算出 (環境変数で上書き可)
1. `planet.osm.pbf` を最新版で更新 (`wget -N`)。取得元は**国内ミラー `planet.passportcontrol.net` を優先し、不通なら本家 `planet.openstreetmap.org` にフォールバック**(`PLANET_URL` / `PLANET_URL_FALLBACK` で上書き可)。**`SKIP_PLANET_DOWNLOAD=1` を付けるとこの再ダウンロードを省略**し、既存の `pbf/global.osm.pbf` で再ビルドする(パイプライン変更だけを ~80GB の再取得なしで再適用したい場合に使う)
2. `buffer_clip.py` を**明示座標** (`--polygon` で北方領土、`--bbox` で竹島・尖閣) で実行 → `world_minus_islands.poly` + `islands_buffered.geojson` を再生成 (毎回実行、ローカル計算のみ、所要数秒、OSM.jp 非依存)
3. `osmium extract -p` で本体を島嶼 +2 km 抜きでクリップ
3.5. `residual_label_ids.py` + `osmium removeid` で、smart strategy が relation 完結性維持のため巻き込んだ名前付き/ラベル/POI feature を除去 (§7.1)
3.6. 島域を別抽出 → `strip_island_labels.py` で文字タグ除去 → `osmium merge` で本体へ戻す (島は地物のまま無名化。§7.2 / §1.3)
4. Planetiler で全球ビルド (出力先は仮名 `final.new.mbtiles`)
5. **クロスファイルシステム対応のアトミック置換**: ビルド成果物をいったん配信ディスクに `cp` してから同一 FS 内で `mv` rename
6. `sudo systemctl restart tileserver-gl.service` で SQLite ハンドルをリフレッシュ
7. `sudo find /var/cache/nginx/tiles -type f -delete` で古いタイルを破棄、`sudo systemctl reload nginx`

> **Cloudflare エッジキャッシュは rebuild.sh の対象外**: 前段に Cloudflare を置いている場合、(7) の nginx パージだけではエッジの古いタイルが残る。リビルド後に Cloudflare 側のパージ(ダッシュボード or purge API)を別途実施すること。自動化したい場合は rebuild.sh 末尾に purge API 呼び出しを追加できる(トークンは `deploy.env` に置く)。

ハードコードされたパスは存在しない: 全て `deploy.env` 経由で解決される。`scripts/rebuild.sh` は render 対象外 (staging/ には現れない) — `deploy.env` を実行時に source する設計のため。

> **(2) は OSM.jp を叩かない**: 係争地の領域は明示座標で定義する (§6)。`fetch_osmjp.py` / `geojson/` は legacy で年次ループには登場しない (§1.4)。

### 11.2 sudoers で必要最小限の特権を付与

rebuild.sh が叩く 3 コマンドだけをパスワード無しで実行可能にする。本リポジトリ収録の `etc/sudoers.d/tileserver-rebuild` を配置:

```bash
sudo install -m 0440 -o root -g root \
    "$REPO/staging/etc/sudoers.d/tileserver-rebuild" \
    /etc/sudoers.d/tileserver-rebuild
sudo visudo -c -f /etc/sudoers.d/tileserver-rebuild   # 構文検査
```

### 11.3 systemd service / timer

本リポジトリ収録の `etc/systemd/system/tileserver-rebuild.{service,timer}` を配置:

```bash
sudo install -m 0644 -o root -g root \
    "$REPO/staging/etc/systemd/system/tileserver-rebuild.service" \
    /etc/systemd/system/tileserver-rebuild.service

sudo install -m 0644 -o root -g root \
    "$REPO/staging/etc/systemd/system/tileserver-rebuild.timer" \
    /etc/systemd/system/tileserver-rebuild.timer

sudo systemctl daemon-reload
sudo systemctl enable --now tileserver-rebuild.timer
```

> サービスの `ExecStart` は `/home/foobar/tileserver-noborder/scripts/rebuild.sh` を直接指す。リポジトリを別パスにクローンした場合は、サービスファイル上書き or `Environment=REPO=...` 追加 + `ExecStart` パス調整を行う。

---

## 12. トラブルシューティング

| 症状 | 対処 |
|---|---|
| Planetiler が OOM | `-Xmx` を下げて `--storage=mmap --nodemap-storage=mmap --nodemap-type=array` を追加 |
| osmium が `.poly` を拒否 | `head -20 build/world_minus_islands.poly` でフォーマット確認 (1 行目:名前、2 行目:外周名、`END` 3 連、`!hole_N` で穴) |
| 島のシルエットが表示されない | 水域ポリゴンが誤って島域にかぶっている可能性。Planetiler を `--download` 付きで再実行し、land-polygons-split-3857.zip が取得されたか確認 |
| 島のエリアが黒で埋まる | OSM coastline が Hoppo/Takeshima を land として主張している想定と一致しない。黒塗りにしたい場合は §12.1 |
| 島内に道路・建物が残る | `§7.1` の tags-filter + removeid 二重除去を実施 |
| タイルは出るが真っ白 | sprite / fonts が 404/400。tileserver-gl の起動ログと `curl http://127.0.0.1:8080/fonts/Noto%20Sans%20Regular/0-255.pbf` を確認 |
| `/styles/<id>/sprite.json` が `400 Bad Sprite ID or Scale` | style.json の `sprite` が**絶対 http URL になっている**。tileserver-gl 5.x は絶対 URL の sprite を自前配信登録しない。§9.3 の `patch_style.py` を再実行して `sprite` をローカル相対パス (`<id>/sprite`) に戻し、`sudo systemctl restart tileserver-gl` → nginx キャッシュをパージ (`find /var/cache/nginx/tiles -mindepth 1 -delete`) |
| `{name:latin}` が空 | Planetiler で `--languages=en` を指定していない。再ビルド必要 |
| 非 Latin 名 (日本語等) のラベルが不自然に音訳された文字で出る | `--transliterate=false` を指定していない。指定すると `name:en` 等の Latin 名が無い場合にラベル自体が描画されなくなる (§8 参照) |
| ラベルが英語以外 | OpenMapTiles の仕様: `name:latin` は `name:en` が存在すればそれを優先。OSM 側のタグ依存 |
| ブラウザで Mixed Content | 配信 style.json の `sprite` / `glyphs` が `http://` で返っている。tileserver-gl は `X-Forwarded-Proto` から scheme を決めるため、§9.7.5 の nginx 設定で `proxy_set_header X-Forwarded-Proto https;` が両 `location` に入っているか確認 (§9.7.8) |
| 海上の境界線が見える (海峡の県境、根室↔北方領土間など) | **§10.2.1 (E)** で `boundary < water < transportation` の順序を確認。崩れていれば §9.3 の patch_style.py を再実行 (`mask_sea_boundaries()` が water を boundary の上へ移動する)。再実行後は **tileserver-gl 再起動 + nginx キャッシュパージ** (`find /var/cache/nginx/tiles -mindepth 1 -delete`) が必要。注: `maritime!=1` フィルター (§10.2.1 C) だけでは `maritime=0` の海峡県境は消えない — 消すのは water 被覆 (§1.2.5) |
| 海上の橋・トンネルまで消えてしまった | `transportation` が water マスクの下にある。**§10.2.1 (E)** で `transportation 最小 idx > water idx` を確認。NG なら patch_style.py を再実行 (`mask_sea_boundaries()` が transportation を持ち上げる) |
| 陸上国境線が他の行政区画より太い/目立つ線で描画される | `admin_country_*` / `boundary_country_*` レイヤーが削除されていない。**§10.2.1 (A)** のコマンドで確認し、残っていれば §9.3 の patch_style.py を再実行 |
| 陸上国境が描画されない (消えている) | `admin_sub` / `boundary_state` のフィルター拡張が効いていない。**§10.2.1 (B)** で admin_level 条件に 2, 3, 4 がすべて含まれているか確認 |
| 低ズームで何も描画されない (国名すら出ない) | **§10.2.1 (D)** で国名ラベルの `maxzoom` が 5 に制限されているが、対応する symbol レイヤー自体が元スタイルから欠落している可能性。OSM.jp からの style.json 再取得を行ってから patch_style.py を再実行 |
| 拡大しても国が区別できない | **これは仕様** (§1.2.2)。陸上国境も県境と同一スタイルで描画される。国の形を示したい場合は §9.3 patch_style.py から `neutralize_country_boundaries()` の呼び出しをコメントアウトして再実行 |
| `certbot --nginx` 失敗 (HTTP-01 unauthorized) | 80 番への外部到達性と DNS A レコードを確認 (`dig "$DOMAIN"`、`curl -I "http://$DOMAIN/.well-known/acme-challenge/test"`)。IPv6 AAAA が古い IP を指している場合も失敗する |
| certbot 自動更新が失敗する | `sudo journalctl -u certbot.timer -e` と `sudo certbot renew --dry-run` を確認。更新成功時に nginx reload が走らない場合は §9.7.7 のフックを確認 |
| リビルド後も古いタイルが返る | nginx `proxy_cache` の残留。§11 の rebuild.sh (6)(7) で `/var/cache/nginx/tiles` が削除されているか確認 |
| 502 Bad Gateway | tileserver-gl が `127.0.0.1:8080` で待ち受けていない。`ss -ltnp | grep 8080` と `journalctl -u tileserver-gl -e` を確認 |
| tileserver-gl が起動直後にクラッシュ (`Failed to open X display` / core-dump) | `serve_rendered: true` のサーバ側描画に X ディスプレイが無い。`sudo apt install -y xvfb libgl1-mesa-dri` し、systemd ユニットが `xvfb-run …` で起動しているか確認 (§9.6)。ラスター不要なら `serve_rendered: false` に戻す (§9.5) |
| ラスター PNG だけ 500 (ベクター/style.json は正常)、ログに `Failed to load glyph range ... Invalid range` | ①参照フォント不足: §9.2 の 8 書体すべてが `data/fonts/` にあるか確認 (特に `Nunito Regular` / `Nunito Bold`)。`build_fonts.sh` を再実行し tileserver-gl 再起動。②glyphs パス不正: style.json の `glyphs` が `{fontstack}/{range}.pbf` (先頭 `fonts/` 無し) か確認。`fonts/` が付くと描画で二重 `fonts/` になり 500 (§9.3 項目 10)。`patch_style.py` 再実行 |
| ラスター XYZ URL が 404 (`/styles/{id}/{z}/{x}/{y}.png`) | `serve_rendered: false` のまま。§9.5 で `true` にし、Xvfb を整えて (§9.6) 再起動 |
| nginx 起動に失敗 (Permission denied) | §9.7.1a の `user foobar foobar;` 変更後に `/var/log/nginx` のオーナーが foobar になっていない。`sudo chown -R foobar:foobar /var/log/nginx` |
| nginx のログが書き込めない | logrotate で再生成されたログが古いオーナーになっている。`/etc/logrotate.d/nginx` の `create` 行が `foobar adm` を指しているか確認 |
| デモページが 403 Forbidden | nginx ワーカーが foobar になっているか確認 (`ps -eo user,pid,cmd \| grep 'nginx:'`)。また `/home/foobar` のパーミッションが `755` 以上で nginx ワーカーから辿れるか確認 |

### 12.1 島を「海と同化」として扱いたい場合

§1.3 の文字除去後も、島は陸地ポリゴン(背景色)+ 無名の地物(河川・道路等)として海の中に描かれる。島の存在ごと完全に海と同化させたい場合は、スタイルに海色のマスクレイヤーを追加して島を塗り潰す。両スタイルで海の色が異なるため、スタイルごとに色を変える必要がある (Toner-en は黒、Basic-en は水色)。

リポジトリ内の `scripts/apply_sea_mask.py` を使用する。`--style-id` を渡すと既定色を選択 (`--color` で上書き可)。`water` レイヤー直後に `jp-sea-mask` レイヤーを挿入する冪等処理 (既存があれば差し替え)。

```bash
MASK=/work/$USER/planetiler/build/islands_buffered.geojson
for STYLE in maptiler-toner-en maptiler-basic-en; do
    "$REPO/scripts/apply_sea_mask.py" \
        --style $HOME/tileserver-gl/data/styles/${STYLE}/style.json \
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

配信スタイル + sprite は openmaptiles 公式 (BSD 3-Clause + CC-BY 4.0、share-alike なし) を直接取得したもので、CC-BY-SA 2.0 とは無関係。

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

本構成のタイル配信ループ (年次 rebuild) は**完全ローカル動作**: OSM.jp サーバへの通信なし。OSM.jp が参照されるのは初回セットアップ時に `scripts/fetch_osmjp.py` を 1 度実行するときのみ (§5)。以降は年単位の手動リフレッシュを除き OSM.jp は不要。

リポジトリ自体は OSM.jp 由来データを 1 ビットも含まない (`.gitignore` で `geojson/*.geojson` を除外)。CC-BY-SA 2.0 / ODbL の share-alike 義務はリポジトリ配布物には発生せず、操作者が手元で fetch した時点でその操作者の手元のファイルにのみ発生する。

### 13.6 本リポジトリの改変スタイルに対する著作権ポリシー

**本リポジトリは、`scripts/patch_style.py` によるスタイル改変部分に対して新たな著作権を主張しない**。改変部分は上流と同一のライセンス (BSD 3-Clause for code / CC-BY 4.0 for design) でそのまま頒布されるものとし、本リポジトリの名称・URL・改変者表記の追加クレジットを下流配信地図に求めない。

**結果として、配信地図に表示するクレジット文字列は §13.3 / §14.3 のとおり上流 LICENSE.md の例と完全一致する**。すなわち:

- Toner-en: `© MapTiler | © OpenStreetMap contributors`
- Basic-en: `© OpenMapTiles | © OpenStreetMap contributors`

「Modified by tileserver-noborder」「Boundary-neutral rendering」等の追加クレジットは**ライセンス上不要**である (任意の付記は妨げない)。

> **背景**: `patch_style.py` の改変はすべて公開された規則による機械的変換 (boundary フィルター追加、admin_level 合流、country ラベル zoom キャップ、フォント置換、sprite/glyphs のローカルパス書き換え等) であり、改変ロジック自体はリポジトリの GPL-2.0 で別途保護される。改変結果の style.json それ自体に新たな著作権主張を重ねる意図はない。これは上流の openmaptiles プロジェクトの credit 例 (§13.3) を尊重し、下流ユーザの帰属表記負担を増やさないためのポリシー判断である。

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

#### 14.2.1 ラスタータイル直叩き (`serve_rendered: true`、本書既定)

`L.tileLayer` で PNG タイルを取得する最もシンプルで互換性の高い方式。**ラスター専用クライアント（WordPress「Leaflet Map」プラグイン等、後述）はこの方式のみ対応**。本書既定の `serve_rendered: true`（§9.5）で利用可能（ヘッドレスでは Xvfb が前提、§9.6）。

> **WordPress「Leaflet Map」プラグイン (bozdoz) で使う場合**: このプラグインは**ラスター XYZ 専用**（`tileurl` / `[leaflet-tilelayer]` の `url` に `{z}/{x}/{y}.png` テンプレートを指定）で、MapLibre GL / `style.json` には対応しない。したがって本サーバの**ラスター XYZ URL** をそのまま指定する:
>
> ```
> https://tile.hogehoge.com/styles/maptiler-toner-en/{z}/{x}/{y}.png
> https://tile.hogehoge.com/styles/maptiler-basic-en/{z}/{x}/{y}.png
> ```
>
> 例: `[leaflet-map][leaflet-tilelayer url="https://tile.hogehoge.com/styles/maptiler-toner-en/{z}/{x}/{y}.png"][/leaflet-map]`。`serve_rendered: false` だとこの URL は 404 になり使えない。

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

> **HTTPS 配信前提**: 上記すべての例は本サーバが HTTPS (`tile.hogehoge.com`) で配信されている前提。HTTP の場合は Mixed Content ブロックで動かない。配信される style.json 内の `sprite` / `glyphs` は tileserver-gl が nginx 経由のリクエスト (`Host` / `X-Forwarded-Proto`) から絶対 `https://` URL に展開するため (§9.3 項目 9 / §9.7.8)、サードパーティは style.json の URL をそのまま指定すれば追加設定なしに動作する。

> **BSD 3-Clause (code) の取扱い**: 配信地図 UI には不要。リポジトリ内に上流 LICENSE.md を保持しておけば足りる (本リポジトリ自体には現在同梱していないが、`styles/<id>/LICENSE.md` として配置するか、§14 から URL リンクで参照する運用で問題なし)。

> **編集姿勢の表記は任意**: 本プロジェクトは国境中立化を行うが、これはライセンス要件ではなく仕様。クレジット欄に「Boundary-neutral rendering」等を併記するかは任意。

### 14.4 どのパターンを選ぶか

| 状況 | 推奨パターン |
|---|---|
| 新規サイト、可能なら MapLibre GL JS を直接使える | **§14.1** (MapLibre GL JS 単体) |
| 既存 Leaflet サイトに後付け、見た目をスタイル通りに維持したい | **§14.2.3** (maplibre-gl-leaflet) |
| ラスター専用クライアント (WordPress「Leaflet Map」プラグイン等)、または素の `L.tileLayer` で十分 | **§14.2.1** (raster XYZ。`serve_rendered: true` は本書既定) |
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
