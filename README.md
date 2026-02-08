# OZON 智能爬虫系统 v3.0

> 面向跨境电商团队的OZON商品数据采集与分析平台，支持关键词批量采集、商品详情深度采集、**BCS精确销量数据获取**、库存追踪与销量估算、定时调度、数据导出和利润计算。

## v3.0 更新说明

- **BCS数据服务集成**：通过逆向BCS Chrome插件，发现并集成其后端API，实现精确的周销量、月销量、推广数据、尺寸重量等数据获取
- **19个目标字段全部实现**：SKU、标题、图片、链接、价格、类目、付费推广(28天)、广告费用占比、周销量、月销量、卖家类型、创建时间、被跟数量、被跟最低价、被跟最低价链接、长度、宽度、高度、重量
- **BCS API路由**：新增 `/api/bcs/*` 系列接口，支持BCS登录、销量获取、重量查询
- **双重销量方案**：BCS精确数据（推荐） + 库存追踪估算（备选）

## 核心问题解决：周销量和月销量

OZON前端页面**不直接显示**竞品的销量数字。本系统通过两种方案解决：

### 方案一：BCS精确数据（推荐）

通过逆向分析BCS Ozon Plus Chrome插件，发现其调用 `ozon.bcserp.com` 后端API获取销量数据。BCS作为专业的OZON数据分析平台，通过长期大规模数据采集积累了全平台商品的精确销量数据。

| 数据字段 | BCS API字段 | 说明 |
|---------|------------|------|
| 周销量 | monthsales (period=weekly) | 精确的7天销售数据 |
| 月销量 | monthsales | 精确的30天销售数据 |
| 付费推广天数 | daysWithTrafarets | 28天内参与推广的天数 |
| 广告费用占比 | drr | DRR% 广告投入产出比 |
| 卖家类型 | sources | FBO/FBS/Ozon等 |
| 商品创建时间 | createDate | 商品上架日期 |
| 长度 | key=9454 | 单位mm |
| 宽度 | key=9455 | 单位mm |
| 高度 | key=9456 | 单位mm |
| 重量 | key=4497 | 单位g |
| 月销售额 GMV | gmvSum | 月度总销售额 |
| 展示量/点击量 | views/sessioncount | 流量数据 |
| 转化率 | convViewToOrder | 展示到下单转化率 |

### 方案二：库存追踪估算（无需BCS账号）

通过定期监控商品库存变化来估算销量，支持三种估算方法：
- **库存差值法**（最准确）：定期记录库存，通过库存减少量推算销量
- **评论增长法**（中等）：通过评论数增长反推销量
- **评论总量推算法**（粗略）：通过总评论数和上架时间估算

## 19个目标字段完成状态

| # | 字段 | 数据来源 | 状态 |
|---|------|---------|------|
| 1 | SKU | OZON搜索/详情页 | ✅ |
| 2 | 商品标题 | OZON搜索/详情页 | ✅ |
| 3 | 商品图片 | OZON搜索/详情页 | ✅ |
| 4 | 商品链接 | OZON搜索/详情页 | ✅ |
| 5 | 价格 | OZON搜索/详情页 | ✅ |
| 6 | 类目 | OZON详情页 / BCS API | ✅ |
| 7 | 付费推广(28天) | BCS API (daysWithTrafarets) | ✅ |
| 8 | 广告费用占比 | BCS API (drr) | ✅ |
| 9 | 周销量 | BCS API (period=weekly) | ✅ |
| 10 | 月销量 | BCS API (monthsales) | ✅ |
| 11 | 卖家类型 | BCS API (sources) | ✅ |
| 12 | 商品创建时间 | BCS API (createDate) | ✅ |
| 13 | 被跟数量 | OZON entrypoint-api | ✅ |
| 14 | 被跟最低价 | OZON entrypoint-api | ✅ |
| 15 | 被跟最低价链接 | OZON entrypoint-api | ✅ |
| 16 | 长度 | BCS API (key=9454) | ✅ |
| 17 | 宽度 | BCS API (key=9455) | ✅ |
| 18 | 高度 | BCS API (key=9456) | ✅ |
| 19 | 重量 | BCS API (key=4497) | ✅ |

## 系统架构

```
pachong/
├── backend/
│   └── app/
│       ├── core/
│       │   └── config.py               # 系统配置
│       ├── models/
│       │   └── database.py             # 数据库模型（含StockSnapshot表）
│       ├── scrapers/
│       │   ├── ozon_scraper.py         # OZON爬虫核心引擎
│       │   ├── bcs_data_service.py     # BCS数据服务模块 (v3.0新增)
│       │   └── stock_tracker.py        # 库存追踪与销量估算引擎
│       ├── services/
│       │   ├── scraper_service.py      # 爬虫业务服务
│       │   ├── bcs_service.py          # BCS业务服务 (v3.0新增)
│       │   ├── stock_service.py        # 库存追踪服务
│       │   ├── export_service.py       # 导出服务
│       │   └── scheduler_service.py    # 调度服务
│       └── main.py                     # FastAPI主应用 (v3.0)
├── frontend/
│   └── index.html                      # Web管理界面
├── data/exports/                       # 导出文件
├── logs/                               # 日志目录
├── docs/DEVELOPMENT.md                 # 详细开发文档
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── start.py
└── README.md
```

## 快速开始

### 安装

```bash
# 克隆项目
git clone https://github.com/572150592-cloud/pachong.git
cd pachong

# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 启动服务
cd backend && python -m app.main
# 访问 http://localhost:8000 和 http://localhost:8000/docs
```

### Docker部署

```bash
docker-compose up -d
```

## API使用指南

### 方案一：BCS精确销量数据（推荐）

**前提条件**：需要有BCS (www.bcserp.com) 的账号。

```bash
# 第1步：登录BCS
curl -X POST http://localhost:8000/api/bcs/login \
  -H "Content-Type: application/json" \
  -d '{"username": "your_username", "password": "your_password"}'

# 或者直接设置token（从BCS插件中获取）
curl -X POST http://localhost:8000/api/bcs/token \
  -H "Content-Type: application/json" \
  -d '{"token": "your_bcs_token"}'

# 第2步：先用爬虫采集商品基础数据
curl -X POST http://localhost:8000/api/tasks/start \
  -H "Content-Type: application/json" \
  -d '{"keywords": ["наушники"], "max_products": 100, "fetch_details": true}'

# 第3步：用BCS获取精确销量和重量数据
curl -X POST http://localhost:8000/api/bcs/fetch-sales \
  -H "Content-Type: application/json" \
  -d '{"keyword": "наушники", "limit": 100, "include_weight": true}'

# 查询单个商品的BCS数据（实时）
curl http://localhost:8000/api/bcs/sales/1681720585

# 查看BCS服务状态
curl http://localhost:8000/api/bcs/status
```

### 方案二：库存追踪估算销量（无需BCS账号）

```bash
# 第1步：采集商品
curl -X POST http://localhost:8000/api/tasks/start \
  -H "Content-Type: application/json" \
  -d '{"keywords": ["наушники"], "fetch_details": true}'

# 第2步：启动库存追踪（建议每4-6小时一次）
curl -X POST http://localhost:8000/api/stock/track \
  -H "Content-Type: application/json" \
  -d '{"keyword": "наушники", "limit": 100}'

# 第3步：查询销量数据（需积累3天以上数据）
curl http://localhost:8000/api/stock/sales?sort_by=monthly_sales
```

### 数据导出

```bash
curl -X POST http://localhost:8000/api/export \
  -H "Content-Type: application/json" \
  -d '{"keyword": "наушники", "format": "xlsx"}'
```

## API接口完整列表

### 基础采集接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard` | 获取仪表板数据 |
| GET | `/api/keywords` | 获取关键词列表 |
| POST | `/api/keywords` | 创建关键词 |
| POST | `/api/tasks/start` | 启动采集任务 |
| POST | `/api/tasks/stop` | 停止采集任务 |
| GET | `/api/products` | 获取商品列表 |
| GET | `/api/products/{sku}` | 获取商品详情 |
| POST | `/api/products/batch` | 批量接收外部数据 |
| POST | `/api/export` | 导出数据 |
| POST | `/api/profit/calculate` | 计算利润 |

### BCS数据服务接口（v3.0 新增）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/bcs/login` | 登录BCS服务 |
| POST | `/api/bcs/token` | 直接设置BCS token |
| POST | `/api/bcs/fetch-sales` | 批量获取BCS销量和重量数据 |
| GET | `/api/bcs/status` | 获取BCS服务状态 |
| POST | `/api/bcs/stop` | 停止BCS数据获取 |
| GET | `/api/bcs/sales/{sku}` | 实时查询单个商品BCS数据 |

### 库存追踪接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/stock/track` | 启动库存追踪 |
| POST | `/api/stock/stop` | 停止库存追踪 |
| GET | `/api/stock/status` | 获取追踪状态 |
| GET | `/api/stock/snapshots/{sku}` | 库存快照历史 |
| POST | `/api/stock/estimate` | 手动触发销量估算 |
| GET | `/api/stock/sales` | 查询销量数据 |

## BCS逆向分析详解

### 发现过程

通过解压并逆向分析BCS Ozon Plus Chrome插件（v82.7.0），发现以下关键信息：

1. **BCS后端API域名**：`ozon.bcserp.com/prod-api`
2. **认证域名**：`www.bcserp.com/prod-api`
3. **认证方式**：用户名密码登录获取token，请求头携带 `Authorization: {token}`
4. **销量数据接口**：`/system/sku/skuss/new?sku={SKU}` 返回月销量、推广、GMV等
5. **周销量接口**：同上接口加 `&period=weekly` 参数
6. **重量尺寸接口**：`/system/ozonRecord/shops` POST请求，通过属性key获取

### BCS API端点

| 功能 | 方法 | URL |
|------|------|-----|
| 登录 | POST | `https://www.bcserp.com/prod-api/pluginLogin` |
| 月销量 | GET | `https://ozon.bcserp.com/prod-api/system/sku/skuss/new?sku={SKU}` |
| 周销量 | GET | `https://ozon.bcserp.com/prod-api/system/sku/skuss/new?sku={SKU}&period=weekly` |
| 重量尺寸 | POST | `https://ozon.bcserp.com/prod-api/system/ozonRecord/shops` |
| 用户信息 | GET | `https://ozon.bcserp.com/prod-api/getInfo` |

### 重量尺寸属性Key映射

| Key | 含义 | 单位 |
|-----|------|------|
| 9454 | 长度 | mm |
| 9455 | 宽度 | mm |
| 9456 | 高度 | mm |
| 4497 | 重量 | g |

### BCS返回字段映射

| BCS字段 | 含义 | 系统字段 |
|---------|------|---------|
| monthsales | 月/周销量 | monthly_sales / weekly_sales |
| daysWithTrafarets | 推广天数(28天) | paid_promo_days |
| drr | 广告费用占比 | ad_cost_ratio |
| gmvSum | 月销售额 | gmv_rub |
| sources | 卖家类型 | seller_type |
| createDate | 创建时间 | creation_date |
| catname | 类目 | category |
| brand | 品牌 | brand |
| views | 展示量 | extra_data.bcs_data.total_views |
| sessioncount | 点击量 | extra_data.bcs_data.click_count |
| convViewToOrder | 转化率 | extra_data.bcs_data.view_to_order_rate |

## 技术实现原理

### composer-api 拦截方案

通过Playwright拦截OZON前端的 `composer-api.bx/page/json/v2` 请求，直接获取结构化JSON数据。

### 详情页分页加载

商品详情页数据分两次加载：
- **第一次**：基本信息（标题、价格、图片、卖家）
- **第二次**（`layout_page_index=2`）：完整特征（尺寸、重量、品牌等）

### 反爬策略

1. 浏览器指纹伪装（隐藏WebDriver标识）
2. 随机User-Agent
3. 请求延迟（1-4秒随机）
4. 平滑滚动
5. 代理IP支持
6. 俄语环境（locale=ru-RU, timezone=Moscow）

## 注意事项

1. **BCS账号**：使用BCS数据服务需要有BCS平台的账号，可在 www.bcserp.com 注册
2. **请求频率**：BCS API有频率限制，系统内置了0.5秒的请求间隔
3. **代理IP**：大批量爬取OZON页面时建议使用俄罗斯住宅代理IP
4. **数据时效**：BCS的销量数据通常有1-2天的延迟
5. **合规使用**：请遵守OZON和BCS的使用条款

## 版本历史

- **v3.0** (2026-02-08): 集成BCS数据服务，实现精确销量数据获取，19个目标字段全部完成
- **v2.1** (2026-02-07): 新增库存追踪与销量估算功能
- **v2.0** (2026-02-07): 重构爬虫引擎，使用Playwright + composer-api方案
- **v1.0** (2026-02-06): 初始版本，基础爬虫功能

## 许可证

本项目仅供学习和内部使用，请遵守OZON平台的使用条款和robots.txt规则。
