# OZON 爬虫系统 - 开发文档

## 目录

1. [系统架构](#系统架构)
2. [数据来源与字段说明](#数据来源与字段说明)
3. [核心模块说明](#核心模块说明)
4. [API接口文档](#api接口文档)
5. [数据采集流程](#数据采集流程)
6. [部署与配置](#部署与配置)
7. [常见问题](#常见问题)

---

## 系统架构

```
pachong/
├── backend/
│   └── app/
│       ├── main.py                    # FastAPI主应用，所有API路由
│       ├── core/
│       │   └── config.py              # 全局配置（数据库、爬虫参数、代理等）
│       ├── models/
│       │   └── database.py            # SQLAlchemy数据模型定义
│       ├── scrapers/
│       │   └── ozon_scraper.py        # 核心爬虫引擎（Playwright + composer-api）
│       └── services/
│           ├── scraper_service.py     # 爬虫服务层（任务管理、数据存储）
│           ├── export_service.py      # 数据导出服务（Excel/CSV/JSON）
│           └── scheduler_service.py   # 定时任务调度服务
├── frontend/                          # 前端页面
├── data/                              # 数据存储目录
│   ├── ozon_scraper.db               # SQLite数据库
│   └── exports/                      # 导出文件目录
├── logs/                              # 日志目录
├── requirements.txt                   # Python依赖
└── docs/                              # 文档目录
```

### 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 后端框架 | FastAPI | 异步Web框架，提供REST API |
| 爬虫引擎 | Playwright | 无头浏览器自动化，模拟真实用户 |
| 数据库 | SQLite（默认）/ MySQL | 通过SQLAlchemy ORM操作 |
| 数据导出 | openpyxl / csv / json | 支持Excel、CSV、JSON格式 |
| 任务调度 | APScheduler | 支持Cron表达式定时采集 |

---

## 数据来源与字段说明

### 数据获取方式

本爬虫通过两种方式获取OZON商品数据：

#### 方式一：搜索列表页采集（快速批量）

通过Playwright访问OZON搜索页面，拦截浏览器与OZON后端之间的 `composer-api.bx/page/json/v2` 请求响应，从 `widgetStates` 中提取结构化JSON数据。

**可获取字段：** SKU、标题、图片、链接、价格、原价、折扣、评分、评论数、卖家类型

#### 方式二：商品详情页深度采集（完整数据）

逐个访问商品详情页，通过拦截多次 `composer-api` 响应（包括分页加载的第二页数据），获取完整的商品特征信息。

**额外可获取字段：** 完整类目路径、卖家名称、品牌、尺寸（长/宽/高）、重量、体积、跟卖信息、商品创建时间、完整特征列表

### 完整字段映射表

| 字段名 | 中文名 | 数据来源 | 获取方式 | 说明 |
|--------|--------|----------|----------|------|
| `sku` | SKU | OZON页面 | 列表页/详情页 | 从商品URL中提取，如 `-1185261285/` |
| `title` | 商品标题 | composer-api | `mainState.textAtom.text` 或 `webProductHeading.title` | 商品完整标题 |
| `image_url` | 商品图片 | composer-api | `tileImage.items[0].image.link` 或 `webGallery.coverImage` | 主图URL |
| `product_url` | 商品链接 | OZON页面 | `action.link` | 完整商品详情页URL |
| `price` | 价格 | composer-api | `priceAtom.price` 或 `webPrice.price` | 当前售价（卢布） |
| `original_price` | 原价 | composer-api | `priceAtom.originalPrice` 或 `webPrice.originalPrice` | 原始价格（卢布） |
| `discount_percent` | 折扣 | composer-api | `tagAtom.text` 中的 `-XX%` | 折扣百分比 |
| `category` | 类目 | composer-api | `breadCrumbs.breadcrumbs` | 完整类目路径，如 `Электроника > Смартфоны` |
| `brand` | 品牌 | composer-api | `webLongCharacteristics` 中 `Бренд` 字段，或 SEO JSON-LD | 品牌名称 |
| `rating` | 评分 | composer-api | `webReviewProductScore.score` | 商品评分（1-5） |
| `review_count` | 评论数 | composer-api | `webReviewProductScore.count` | 评论总数 |
| `seller_name` | 卖家名称 | composer-api | `webCurrentSeller.name` | 卖家店铺名称 |
| `seller_type` | 卖家类型 | composer-api | `webCurrentSeller.isOzon` + `deliverySchema` | Ozon自营 / FBO / FBS / 第三方 |
| `creation_date` | 商品创建时间 | composer-api | SEO `script` 中的 `datePublished` | JSON-LD中的发布日期 |
| `followers_count` | 被跟卖数量 | composer-api | `cellList.items` 数组长度 | 同一商品的其他卖家数量 |
| `follower_min_price` | 被跟最低价 | composer-api | `cellList.items` 中的最低价格 | 跟卖者中的最低售价 |
| `follower_min_url` | 被跟最低价链接 | composer-api | 最低价offer的 `action.link` | 最低价跟卖者的商品链接 |
| `length_cm` | 长度（cm） | composer-api | `webLongCharacteristics` 中 `Длина` 字段 | 商品或包装长度 |
| `width_cm` | 宽度（cm） | composer-api | `webLongCharacteristics` 中 `Ширина` 字段 | 商品或包装宽度 |
| `height_cm` | 高度（cm） | composer-api | `webLongCharacteristics` 中 `Высота/Толщина` 字段 | 商品或包装高度 |
| `weight_g` | 重量（g） | composer-api | `webLongCharacteristics` 中 `Вес` 字段 | 商品重量（自动转换单位） |
| `volume_liters` | 体积（L） | composer-api | `webLongCharacteristics` 中 `Объем` 字段 | 商品体积 |
| `delivery_info` | 配送信息 | OZON页面 | DOM提取 | 配送方式和时效 |
| `monthly_sales` | 月销量 | **不可直接获取** | — | OZON不提供竞品销量接口，需第三方服务 |
| `weekly_sales` | 周销量 | **不可直接获取** | — | 同上 |
| `paid_promo_days` | 付费推广天数 | **不可直接获取** | — | 需第三方数据服务 |
| `ad_cost_ratio` | 广告费用占比 | **不可直接获取** | — | 需第三方数据服务 |

### 关于无法直接获取的字段

以下字段OZON官方不提供公开接口查询竞品数据：

- **周销量 / 月销量**：第三方工具（如ozonbigsell、sellerstats）通过长期高频追踪商品库存余量（`freeRest`）的变化来估算。本系统预留了这些字段，可通过以下方式补充：
  1. 接入第三方API服务（如ozonbigsell.com的API）
  2. 自建库存追踪系统，定期记录库存变化
  3. 通过Chrome扩展批量推送数据到 `/api/products/batch` 接口

- **付费推广 / 广告费用占比**：仅卖家后台可见自己的数据，第三方通过搜索结果中的广告标记来推断。

---

## 核心模块说明

### 1. OzonScraper（爬虫引擎）

文件：`backend/app/scrapers/ozon_scraper.py`

核心类，负责浏览器控制和数据提取。

```python
# 使用示例
scraper = OzonScraper(headless=True)
await scraper.start()

# 搜索列表页批量采集
products = await scraper.scrape_products(
    keyword="смартфон",
    max_products=100
)

# 单品详情页深度采集
detail = await scraper.get_product_detail("1185261285")

# 批量详情采集
details = await scraper.scrape_product_details(
    sku_list=["1185261285", "1234567890"],
    delay_range=(3, 6)
)

await scraper.stop()
```

**关键技术点：**

- **请求拦截**：通过 `page.on("response", handler)` 拦截 `composer-api.bx/page/json/v2` 的响应，直接获取结构化JSON数据
- **双重提取**：同时从API响应和DOM中提取数据，API数据优先，DOM数据作为补充
- **分页加载**：商品详情页的完整特征数据在第二次API请求中返回（`layout_page_index=2`），通过滚动页面触发加载
- **反检测**：注入反检测脚本，隐藏 `navigator.webdriver` 等自动化特征

### 2. OzonScraperManager（爬虫管理器）

管理多关键词采集任务，支持三种切换模式：

| 模式 | 说明 |
|------|------|
| `sequential` | 顺序采集，每个关键词采集完成后切换到下一个 |
| `timer` | 定时切换，每隔N分钟切换到下一个关键词 |
| `quantity` | 定量切换，每采集N个商品后切换到下一个关键词 |

### 3. ScraperService（服务层）

文件：`backend/app/services/scraper_service.py`

负责任务生命周期管理和数据持久化。

### 4. ExportService（导出服务）

文件：`backend/app/services/export_service.py`

支持导出为Excel（带样式）、CSV、JSON三种格式，包含所有字段的中文列名映射。

---

## API接口文档

### 采集任务

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/tasks/start` | 启动采集任务 |
| `POST` | `/api/tasks/stop` | 停止当前任务 |
| `GET` | `/api/tasks` | 获取任务列表 |
| `GET` | `/api/tasks/status` | 获取实时采集状态 |

**启动任务请求示例：**

```json
{
    "keywords": ["смартфон", "наушники"],
    "max_products": 1000,
    "import_only": false,
    "switch_mode": "sequential",
    "fetch_details": true
}
```

> `fetch_details` 设为 `true` 时，会在列表页采集完成后，逐个访问商品详情页获取完整数据（类目、尺寸、重量等），但速度会显著降低。

### 商品数据

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/products` | 获取商品列表（支持筛选、排序、分页） |
| `GET` | `/api/products/{sku}` | 获取单个商品详情 |
| `POST` | `/api/products/batch` | 批量接收外部推送的商品数据 |

### 关键词管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/keywords` | 获取关键词列表 |
| `POST` | `/api/keywords` | 创建关键词 |
| `PUT` | `/api/keywords/{id}` | 更新关键词 |
| `DELETE` | `/api/keywords/{id}` | 删除关键词 |

### 数据导出

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/export` | 导出商品数据（xlsx/csv/json） |

### 利润计算

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/profit/calculate` | 计算单个商品利润 |

### 系统管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/dashboard` | 获取仪表板统计数据 |
| `GET` | `/api/config` | 获取系统配置 |
| `PUT` | `/api/config/{key}` | 更新系统配置 |
| `GET` | `/api/schedules` | 获取定时任务列表 |
| `POST` | `/api/schedules` | 创建定时任务 |
| `DELETE` | `/api/schedules/{id}` | 删除定时任务 |

---

## 数据采集流程

### 流程图

```
用户发起采集任务
       │
       ▼
┌─────────────────────┐
│  1. 列表页采集阶段    │
│  (快速批量)           │
│                      │
│  访问搜索页面         │
│       │              │
│       ▼              │
│  拦截 composer-api   │
│  响应数据             │
│       │              │
│       ▼              │
│  解析 widgetStates   │
│  提取商品基本信息      │
│       │              │
│       ▼              │
│  滚动加载更多         │
│  (循环直到达到目标数)  │
└─────────┬───────────┘
          │
          ▼
    fetch_details?
     ┌────┴────┐
     │ No      │ Yes
     ▼         ▼
  保存数据  ┌──────────────────────┐
            │  2. 详情页采集阶段    │
            │  (深度数据)           │
            │                      │
            │  逐个访问商品详情页    │
            │       │              │
            │       ▼              │
            │  拦截第1次API响应     │
            │  (标题/价格/卖家)     │
            │       │              │
            │       ▼              │
            │  滚动触发第2次加载    │
            │  (特征/尺寸/重量)     │
            │       │              │
            │       ▼              │
            │  合并所有数据         │
            └──────────┬───────────┘
                       │
                       ▼
                    保存数据
```

### composer-api 数据结构

OZON的 `composer-api.bx/page/json/v2` 返回的JSON结构：

```json
{
    "widgetStates": {
        "searchResultsV2-xxx": "{\"items\":[...]}",
        "webProductHeading-xxx": "{\"title\":\"...\"}",
        "webPrice-xxx": "{\"price\":\"1 234 ₽\"}",
        "webCurrentSeller-xxx": "{\"name\":\"...\",\"isOzon\":true}",
        "breadCrumbs-xxx": "{\"breadcrumbs\":[...]}",
        "webLongCharacteristics-xxx": "{\"characteristics\":[...]}"
    },
    "seo": {
        "script": [{"innerHTML": "{\"@type\":\"Product\",\"datePublished\":\"...\"}"}]
    }
}
```

每个 `widgetStates` 的值是一个JSON字符串，需要二次解析。

### 特征数据结构（尺寸/重量）

`webLongCharacteristics` widget中的特征数据结构：

```json
{
    "characteristics": [
        {
            "title": "Общие",
            "short": [
                {
                    "key": "Бренд",
                    "name": "Бренд",
                    "values": [{"text": "Apple"}]
                },
                {
                    "key": "Вес товара, г",
                    "name": "Вес товара, г",
                    "values": [{"text": "171"}]
                }
            ]
        },
        {
            "title": "Габариты",
            "short": [
                {
                    "key": "Длина упаковки",
                    "values": [{"text": "17.5 см"}]
                },
                {
                    "key": "Ширина упаковки",
                    "values": [{"text": "9 см"}]
                },
                {
                    "key": "Высота упаковки",
                    "values": [{"text": "5 см"}]
                }
            ]
        }
    ]
}
```

爬虫会自动识别俄语关键词（Вес=重量, Длина=长度, Ширина=宽度, Высота=高度）并提取数值，同时自动转换单位（мм→cm, кг→g）。

---

## 部署与配置

### 环境要求

- Python 3.9+
- Node.js 16+（如需前端开发）
- 系统内存 ≥ 2GB（Playwright浏览器需要）

### 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/572150592-cloud/pachong.git
cd pachong

# 2. 安装Python依赖
pip install -r requirements.txt

# 3. 安装Playwright浏览器
playwright install chromium

# 4. 创建必要目录
mkdir -p data/exports logs

# 5. 启动服务
cd backend
python -m app.main
```

服务启动后访问 `http://localhost:8000` 即可使用。

API文档：`http://localhost:8000/docs`（Swagger UI）

### 配置文件

主要配置在 `backend/app/core/config.py` 中：

```python
# 数据库配置（默认SQLite，可切换MySQL）
DATABASE_URL = "sqlite:///data/ozon_scraper.db"
# DATABASE_URL = "mysql://user:pass@host:3306/ozon_scraper"

# 爬虫配置
SCRAPER_CONFIG = {
    "max_products_per_keyword": 50000,
    "scroll_pause_time": 1.5,
    "request_delay_min": 0.5,
    "request_delay_max": 2.0,
    "max_retries": 3,
    "headless": True,
}

# 代理配置
PROXY_CONFIG = {
    "enabled": False,
    "proxy_list": [],
}
```

### 代理配置

由于OZON有地区限制和反爬机制，建议配置俄罗斯IP代理：

```python
# 在config.py中配置
PROXY_CONFIG = {
    "enabled": True,
    "proxy_list": [
        {"server": "http://proxy-ru:8080", "username": "user", "password": "pass"},
    ],
}
```

或在启动任务时通过API传入代理参数。

---

## 常见问题

### Q1: 为什么获取不到尺寸和重量数据？

尺寸和重量数据在商品详情页的第二次API加载中返回（`layout_page_index=2`）。需要：
1. 启动任务时设置 `fetch_details: true`
2. 确保页面有足够的滚动时间触发第二次加载
3. 并非所有商品都有尺寸/重量信息，取决于卖家是否填写

### Q2: 为什么没有销量数据？

OZON官方不提供查询竞品销量的公开接口。所有第三方工具的销量数据都是通过长期追踪库存变化估算的。如需销量数据，可以：
1. 接入第三方API（如ozonbigsell.com）
2. 自建库存追踪系统
3. 通过Chrome扩展推送数据

### Q3: 采集速度如何优化？

- 仅列表页采集（不开启详情页）：每分钟约50-100个商品
- 开启详情页采集：每分钟约10-15个商品（受限于页面加载和反爬延迟）
- 建议大批量采集时先不开启详情页，后续对重点商品单独获取详情

### Q4: 如何避免被OZON封禁？

1. 使用代理IP（建议俄罗斯住宅IP）
2. 控制采集速度（默认配置已包含随机延迟）
3. 定期更换User-Agent
4. 避免短时间内大量请求同一页面

### Q5: 类目数据的格式是什么？

类目数据以面包屑导航的形式存储，用 ` > ` 分隔，例如：
```
Электроника > Смартфоны и аксессуары > Смартфоны
```
