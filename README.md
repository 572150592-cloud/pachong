# OZON 智能爬虫系统 v2.1

> 面向跨境电商团队的OZON商品数据采集与分析平台，支持关键词批量采集、商品详情深度采集、**库存追踪与销量估算**、定时调度、数据导出和利润计算。

## v2.1 更新说明

- **库存追踪模块**：新增 `StockTracker`，通过定期监控商品库存变化来估算周/月销量
- **销量估算引擎**：新增 `SalesEstimator`，支持库存差值法、评论增长法、评论总量推算法三种估算方式
- **库存快照表**：新增 `stock_snapshots` 数据库表，记录每次库存检查的结果
- **推广标记检测**：搜索结果中自动识别付费推广（Реклама）商品
- **库存追踪API**：新增 `/api/stock/*` 系列接口，支持库存追踪任务管理和销量查询
- **仪表板增强**：新增库存快照统计和有销量数据的商品统计

## 系统架构

```
pachong/
├── backend/                    # 后端服务
│   └── app/
│       ├── core/               # 核心配置
│       │   └── config.py       # 系统配置
│       ├── models/             # 数据模型
│       │   └── database.py     # SQLAlchemy模型（含StockSnapshot表）
│       ├── scrapers/           # 爬虫引擎
│       │   ├── ozon_scraper.py # OZON爬虫核心（v2.0 重写）
│       │   └── stock_tracker.py # 库存追踪与销量估算引擎（v2.1 新增）
│       ├── services/           # 业务服务
│       │   ├── scraper_service.py   # 爬虫服务
│       │   ├── stock_service.py     # 库存追踪服务（v2.1 新增）
│       │   ├── export_service.py    # 导出服务
│       │   └── scheduler_service.py # 调度服务
│       └── main.py             # FastAPI主应用
├── frontend/                   # 前端界面
│   └── index.html              # Web管理界面
├── data/                       # 数据目录
│   └── exports/                # 导出文件
├── logs/                       # 日志目录
├── docs/                       # 开发文档
│   └── DEVELOPMENT.md          # 详细开发文档
├── Dockerfile                  # Docker镜像
├── docker-compose.yml          # Docker编排
├── requirements.txt            # Python依赖
├── start.py                    # 启动脚本
└── README.md                   # 项目文档
```

## 核心功能

### 1. OZON商品数据采集

- **关键词搜索采集**：输入俄语关键词，自动搜索并采集商品数据
- **composer-api拦截**：直接获取OZON内部结构化JSON数据，数据准确性高
- **详情页深度采集**：可选开启，逐个访问商品详情页获取完整特征数据
- **无限滚动加载**：自动滚动页面，持续加载新商品，单次最多采集50,000件
- **智能反爬策略**：随机UA、请求延迟、平滑滚动、反检测脚本注入
- **推广标记检测**：自动识别搜索结果中的付费推广（Реклама）商品

### 2. 库存追踪与销量估算（v2.1 新增）

OZON官方不提供查询竞品销量的公开接口。本系统通过**库存追踪法**来估算竞品的周销量和月销量。

#### 工作原理

```
┌─────────────────────────────────────────────────────────┐
│                    销量估算流程                           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. 定时库存检查（建议每4-6小时）                         │
│     │                                                   │
│     ├─→ 访问商品详情页                                   │
│     ├─→ 提取"Осталось X шт"（剩余X件）                  │
│     ├─→ 提取加购按钮的maxQuantity限制                    │
│     ├─→ 记录评论数变化                                   │
│     └─→ 保存库存快照到 stock_snapshots 表                │
│                                                         │
│  2. 销量估算计算                                         │
│     │                                                   │
│     ├─→ 方法A: 库存差值法（最准确）                       │
│     │   库存减少量 = 前一次库存 - 当前库存                 │
│     │   自动检测补货（库存突然增加）并排除                  │
│     │                                                   │
│     ├─→ 方法B: 评论增长法（中等准确）                     │
│     │   周销量 ≈ 评论增长数 / 评论转化率(2%)              │
│     │                                                   │
│     └─→ 方法C: 评论总量推算法（粗略估算）                 │
│         总销量 ≈ 总评论数 / 2%                           │
│         月销量 ≈ 总销量 / 上架天数 × 30                  │
│                                                         │
│  3. 优先级：A > B > C                                    │
│     库存数据充足时用A，不足时用B补充，都没有时用C          │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

#### 销量估算置信度

| 置信度 | 条件 | 说明 |
|--------|------|------|
| **high** | ≥10个库存快照 | 数据点充足，估算较准确 |
| **medium** | 5-9个库存快照 | 数据点一般，估算可参考 |
| **low** | <5个快照或仅用评论法 | 数据不足，仅供参考 |
| **none** | 无任何数据 | 无法估算 |

#### 使用方式

```bash
# 1. 先采集商品数据
curl -X POST http://localhost:8000/api/tasks/start \
  -H "Content-Type: application/json" \
  -d '{"keywords": ["наушники"], "max_products": 100, "fetch_details": true}'

# 2. 启动库存追踪（建议每4-6小时运行一次）
curl -X POST http://localhost:8000/api/stock/track \
  -H "Content-Type: application/json" \
  -d '{"keyword": "наушники", "limit": 100}'

# 3. 查询销量数据
curl http://localhost:8000/api/stock/sales?keyword=наушники&sort_by=monthly_sales

# 4. 查看单个商品的库存历史
curl http://localhost:8000/api/stock/snapshots/12345678?days=30

# 5. 手动触发单个商品的销量估算
curl -X POST http://localhost:8000/api/stock/estimate \
  -H "Content-Type: application/json" \
  -d '{"sku": 12345678}'
```

> **重要提示**：库存追踪法需要**持续运行一段时间**才能积累足够的数据点。建议至少运行3天以上（每4-6小时一次），才能获得较准确的周销量估算。月销量需要运行更长时间。

### 3. 采集数据字段

#### 可直接获取的字段（来自OZON公开页面）

| 字段 | 说明 | 数据来源 | 采集阶段 |
|------|------|----------|----------|
| SKU | 商品唯一标识 | 商品URL | 列表页 |
| 商品标题 | 完整商品名称 | `webProductHeading` widget | 列表页/详情页 |
| 商品图片 | 主图URL | `tileImage` / `webGallery` widget | 列表页/详情页 |
| 商品链接 | 商品详情页URL | `action.link` | 列表页 |
| 价格 | 当前售价（₽） | `priceAtom` / `webPrice` widget | 列表页/详情页 |
| 原价 | 折扣前价格（₽） | `priceAtom.originalPrice` | 列表页/详情页 |
| 折扣 | 折扣百分比 | `tagAtom` | 列表页 |
| 类目 | 完整分类路径 | `breadCrumbs` widget | 详情页 |
| 品牌 | 品牌名称 | `webLongCharacteristics` / SEO JSON-LD | 详情页 |
| 评分 | 商品评分（1-5） | `webReviewProductScore` widget | 列表页/详情页 |
| 评论数 | 评论数量 | `webReviewProductScore` widget | 列表页/详情页 |
| 卖家类型 | Ozon自营/FBO/FBS/第三方 | `webCurrentSeller` widget | 列表页/详情页 |
| 卖家名称 | 卖家店铺名 | `webCurrentSeller` widget | 详情页 |
| 商品创建时间 | 上架日期 | SEO `datePublished` | 详情页 |
| 被跟卖数量 | 跟卖卖家数 | `cellList` widget | 详情页 |
| 被跟最低价 | 跟卖最低价格（₽） | `cellList` widget | 详情页 |
| 被跟最低价链接 | 最低价商品链接 | `cellList` widget | 详情页 |
| 长度 | 商品长度（cm） | `webLongCharacteristics` - `Длина` | 详情页 |
| 宽度 | 商品宽度（cm） | `webLongCharacteristics` - `Ширина` | 详情页 |
| 高度 | 商品高度（cm） | `webLongCharacteristics` - `Высота` | 详情页 |
| 重量 | 商品重量（g） | `webLongCharacteristics` - `Вес` | 详情页 |
| 体积 | 商品体积（L） | `webLongCharacteristics` - `Объем` | 详情页 |
| 付费推广标记 | 是否为推广商品 | `label` / `topLabel` 中的"Реклама" | 列表页 |
| 库存数量 | 当前库存 | `webAddToCart` / 页面文本 | 详情页/库存追踪 |

#### 通过库存追踪估算的字段（v2.1 新增）

| 字段 | 说明 | 获取方式 |
|------|------|----------|
| 周销量 | 近7天估算销量 | 库存差值法 / 评论增长法 |
| 月销量 | 近30天估算销量 | 库存差值法 / 评论增长法 |
| 月销售额 | 月销量 × 价格 | 自动计算 |
| 销量估算方法 | stock_diff / review_growth / review_total_estimate | 自动标注 |
| 销量置信度 | high / medium / low / none | 根据数据点数量判断 |

#### 仍需第三方服务的字段

| 字段 | 说明 | 获取方式 |
|------|------|----------|
| 付费推广（28天参与天数） | 精确的推广天数 | 需第三方数据服务（如MPStats、SellerStats） |
| 广告费用占比 | 广告成本比例 | 需第三方数据服务 |

> **说明**：付费推广的**是否参与**可以通过搜索结果中的"Реклама"标记检测到，但精确的28天参与天数和广告费用占比仍需第三方数据服务。本系统已预留 `paid_promo_days` 和 `ad_cost_ratio` 字段，可通过 `/api/products/batch` 接口接收外部数据补充。

### 4. 任务调度

- **顺序模式**：按关键词顺序逐个采集，每个采集到目标数量后切换
- **定时切换**：每个关键词采集指定时间后自动切换到下一个
- **定量切换**：每个关键词采集指定数量后自动切换
- **Cron定时**：支持Cron表达式配置定时自动采集

### 5. 数据导出

- **Excel导出**：带格式的.xlsx文件，含表头样式和冻结首行
- **CSV导出**：UTF-8编码，兼容Excel打开
- **JSON导出**：结构化JSON数据

### 6. 利润计算

- 输入拼多多采购价，自动计算利润
- 支持自定义运费、佣金率、汇率
- 计算结果自动保存到数据库

## 快速开始

### 方式一：Docker部署（推荐）

```bash
# 克隆项目
git clone https://github.com/572150592-cloud/pachong.git
cd pachong

# 启动服务
docker-compose up -d

# 访问管理界面
# http://localhost:8000
```

### 方式二：本地运行

```bash
# 1. 安装Python依赖
pip install -r requirements.txt

# 2. 安装Playwright浏览器
playwright install chromium
playwright install-deps chromium

# 3. 创建必要目录
mkdir -p data/exports logs

# 4. 启动服务
python start.py
# 或
cd backend && python -m app.main

# 5. 访问管理界面
# http://localhost:8000
# API文档: http://localhost:8000/docs
```

## 使用指南

### 快速采集（仅列表页，速度快）

```bash
curl -X POST http://localhost:8000/api/tasks/start \
  -H "Content-Type: application/json" \
  -d '{
    "keywords": ["наушники", "смартфон"],
    "max_products": 1000,
    "fetch_details": false
  }'
```

### 深度采集（含详情页，数据完整）

```bash
curl -X POST http://localhost:8000/api/tasks/start \
  -H "Content-Type: application/json" \
  -d '{
    "keywords": ["наушники"],
    "max_products": 100,
    "fetch_details": true
  }'
```

> 开启 `fetch_details` 后，系统会在列表页采集完成后，逐个访问每个商品的详情页，获取完整的类目、尺寸、重量、跟卖等信息。速度约为每分钟10-15个商品。

### 库存追踪与销量估算（v2.1 新增）

```bash
# 启动库存追踪（追踪已采集商品的库存变化）
curl -X POST http://localhost:8000/api/stock/track \
  -H "Content-Type: application/json" \
  -d '{"keyword": "наушники", "limit": 50}'

# 查询有销量数据的商品（按月销量排序）
curl "http://localhost:8000/api/stock/sales?sort_by=monthly_sales&sort_order=desc"

# 查看单个商品的库存变化历史
curl "http://localhost:8000/api/stock/snapshots/12345678?days=30"
```

### 使用步骤

1. **添加关键词**：进入「关键词管理」，添加俄语搜索关键词
2. **创建采集任务**：进入「采集任务」，选择关键词并配置参数
3. **启动库存追踪**：采集完成后，启动库存追踪任务（建议每4-6小时一次）
4. **查看数据**：进入「商品数据」页面查看采集结果和销量估算
5. **导出数据**：点击「导出」按钮下载Excel/CSV/JSON文件
6. **计算利润**：输入采购价，自动计算利润空间

## API接口文档

启动服务后访问 `http://localhost:8000/docs` 查看完整的Swagger API文档。

### 主要接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard` | 获取仪表板数据 |
| GET | `/api/keywords` | 获取关键词列表 |
| POST | `/api/keywords` | 创建关键词 |
| PUT | `/api/keywords/{id}` | 更新关键词 |
| DELETE | `/api/keywords/{id}` | 删除关键词 |
| POST | `/api/tasks/start` | 启动采集任务 |
| POST | `/api/tasks/stop` | 停止采集任务 |
| GET | `/api/tasks` | 获取任务列表 |
| GET | `/api/tasks/status` | 获取实时采集状态 |
| GET | `/api/products` | 获取商品列表（支持筛选、排序、分页） |
| GET | `/api/products/{sku}` | 获取商品详情 |
| POST | `/api/products/batch` | 批量接收外部推送的商品数据 |
| POST | `/api/export` | 导出数据（xlsx/csv/json） |
| POST | `/api/profit/calculate` | 计算利润 |
| GET | `/api/schedules` | 获取定时任务列表 |
| POST | `/api/schedules` | 创建定时任务 |
| DELETE | `/api/schedules/{id}` | 删除定时任务 |

### 库存追踪与销量估算接口（v2.1 新增）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/stock/track` | 启动库存追踪任务 |
| POST | `/api/stock/stop` | 停止库存追踪任务 |
| GET | `/api/stock/status` | 获取库存追踪状态 |
| GET | `/api/stock/snapshots/{sku}` | 获取商品库存快照历史 |
| POST | `/api/stock/estimate` | 手动触发销量估算 |
| GET | `/api/stock/sales` | 查询有销量数据的商品 |

## 技术实现原理

### composer-api 拦截方案

本爬虫的核心技术是拦截OZON前端与后端之间的 `composer-api.bx/page/json/v2` 请求响应。这是OZON内部使用的API，返回页面所有组件（widget）的结构化JSON数据。

```
浏览器访问OZON页面
       │
       ▼
OZON前端发起 composer-api 请求
       │
       ▼
Playwright拦截响应 ──→ 提取 widgetStates JSON
       │
       ▼
解析各widget数据 ──→ 商品信息、价格、特征、卖家等
```

**优势**：
- 直接获取结构化JSON，无需解析DOM
- 数据准确性高，不受页面样式变化影响
- 可获取DOM中不可见的隐藏数据

### 库存追踪与销量估算原理（v2.1 新增）

```
定时任务（每4-6小时）
       │
       ▼
访问商品详情页 ──→ 提取库存数量
       │              ├─ "Осталось X шт"（页面文本）
       │              ├─ webAddToCart.maxQuantity（API数据）
       │              └─ 加购测试法（最大可购数量）
       │
       ▼
保存库存快照 ──→ stock_snapshots 表
       │
       ▼
销量估算计算
       ├─→ 库存差值法：Σ(前次库存 - 本次库存)，排除补货
       ├─→ 评论增长法：评论增长数 / 评论转化率(2%)
       └─→ 评论总量法：总评论数 / 2% / 上架天数 × 统计天数
```

### 详情页分页加载

商品详情页的数据分两次加载：
- **第一次**：基本信息（标题、价格、图片、卖家）
- **第二次**（`layout_page_index=2`）：完整特征（尺寸、重量、品牌等）

爬虫通过滚动页面触发第二次加载，从而获取完整的商品特征数据。

## 反爬策略说明

1. **浏览器指纹伪装**：隐藏WebDriver标识、修改navigator属性、注入Chrome对象
2. **随机User-Agent**：每次启动随机选择UA，模拟不同浏览器
3. **请求延迟**：每次操作间随机延迟1-4秒，模拟人类行为
4. **平滑滚动**：分步滚动页面，避免瞬间跳转
5. **代理IP支持**：可配置代理IP池，分散请求来源
6. **俄语环境**：浏览器locale设置为ru-RU，时区设为莫斯科

### 代理配置

建议使用俄罗斯地区的住宅代理：

```python
# 在 config.py 中配置
PROXY_CONFIG = {
    "enabled": True,
    "proxy_list": [
        {"server": "http://proxy-ru:8080", "username": "user", "password": "pass"},
    ],
}
```

## 生产环境部署建议

1. **数据库**：将SQLite替换为MySQL 8.0，修改 `DATABASE_URL`
2. **代理IP**：配置5-10个俄罗斯住宅代理IP，轮换使用
3. **并发控制**：建议同时运行不超过3个采集任务
4. **定时策略**：错峰采集，避开OZON高峰时段（莫斯科时间10:00-22:00）
5. **库存追踪频率**：建议每4-6小时运行一次，太频繁容易触发反爬
6. **数据备份**：配置MySQL自动备份
7. **监控告警**：对接飞书/钉钉WebHook，采集失败时自动通知

## 详细开发文档

请查看 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) 获取完整的开发文档，包括：
- 数据来源与字段的详细技术说明
- composer-api 数据结构解析
- 特征数据（尺寸/重量）的提取逻辑
- 库存追踪与销量估算的技术细节
- API接口完整文档
- 常见问题解答

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 后端框架 | FastAPI | 高性能异步Web框架 |
| 爬虫引擎 | Playwright | 无头浏览器自动化 |
| 库存追踪 | Playwright + StockTracker | 库存监控与销量估算 |
| 数据库 | SQLite/MySQL | 数据持久化（SQLAlchemy ORM） |
| 任务调度 | APScheduler | 定时任务管理 |
| 数据导出 | openpyxl | Excel/CSV/JSON导出 |
| 前端 | 原生HTML/CSS/JS | 轻量级管理界面 |
| 部署 | Docker | 容器化部署 |

## 许可证

本项目仅供学习和内部使用，请遵守OZON平台的使用条款和robots.txt规则。
