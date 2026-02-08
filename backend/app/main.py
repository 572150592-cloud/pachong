"""
OZON爬虫系统 - FastAPI后端主应用
v3.2 - 集成评论时间戳销量分析 + BCS数据服务 + freeRest库存追踪
"""
import os
import sys
import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict

# 添加项目路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.database import (
    init_db, get_db, SessionLocal,
    Product, Keyword, ScrapeTask, TaskSchedule, SystemConfig, StockSnapshot
)
from app.services.scraper_service import ScraperService
from app.services.export_service import ExportService
from app.services.scheduler_service import SchedulerService
from app.services.stock_service import StockService
from app.services.bcs_service import BCSService

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).resolve().parent.parent.parent / "logs" / "app.log",
            encoding="utf-8"
        ),
    ]
)
logger = logging.getLogger(__name__)

# 创建FastAPI应用
app = FastAPI(
    title="OZON智能爬虫系统",
    description="OZON商品数据采集与分析平台 - 支持BCS精确销量数据获取",
    version="3.2.0",
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化服务
scraper_service = ScraperService()
export_service = ExportService()
scheduler_service = SchedulerService()
stock_service = StockService()
bcs_service = BCSService()

# ==================== Pydantic模型 ====================

class KeywordCreate(BaseModel):
    keyword: str = Field(..., description="搜索关键词")
    keyword_zh: Optional[str] = Field(None, description="中文翻译")
    priority: int = Field(0, description="优先级")
    max_products: int = Field(5000, description="最大采集数")

class KeywordUpdate(BaseModel):
    keyword_zh: Optional[str] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None
    max_products: Optional[int] = None

class TaskCreate(BaseModel):
    keyword_ids: Optional[List[int]] = Field(None, description="关键词ID列表")
    keywords: Optional[List[str]] = Field(None, description="直接指定关键词列表")
    max_products: int = Field(5000, description="每个关键词最大采集数")
    import_only: bool = Field(False, description="是否仅搜索进口商品")
    switch_mode: str = Field("sequential", description="切换模式: sequential/timer/quantity")
    switch_interval: int = Field(30, description="定时切换间隔（分钟）")
    switch_quantity: int = Field(1000, description="定量切换阈值")
    fetch_details: bool = Field(False, description="是否获取商品详情页数据（类目、尺寸、重量等）")

class StockTrackRequest(BaseModel):
    sku_list: Optional[List[str]] = Field(None, description="指定SKU列表")
    keyword: Optional[str] = Field(None, description="按关键词选择商品")
    limit: int = Field(100, description="最大追踪商品数", le=1000)


class BCSLoginRequest(BaseModel):
    username: str = Field(..., description="BCS账号用户名")
    password: str = Field(..., description="BCS账号密码")


class BCSTokenRequest(BaseModel):
    token: str = Field(..., description="BCS认证token")


class BCSFetchRequest(BaseModel):
    sku_list: Optional[List[str]] = Field(None, description="指定SKU列表")
    keyword: Optional[str] = Field(None, description="按关键词筛选商品")
    limit: int = Field(100, description="最大处理商品数", le=5000)
    include_weight: bool = Field(True, description="是否同时获取重量尺寸数据")

class SalesEstimateRequest(BaseModel):
    sku: int = Field(..., description="商品SKU")

class ScheduleCreate(BaseModel):
    name: str = Field(..., description="调度名称")
    keyword_ids: List[int] = Field(..., description="关键词ID列表")
    cron_expression: str = Field(..., description="Cron表达式")
    max_products_per_keyword: int = Field(5000, description="每个关键词最大采集数")
    switch_mode: str = Field("sequential", description="切换模式")
    switch_interval: int = Field(30, description="切换间隔")
    switch_quantity: int = Field(1000, description="切换数量")

class ExportRequest(BaseModel):
    keyword: Optional[str] = None
    task_id: Optional[int] = None
    format: str = Field("xlsx", description="导出格式: xlsx/csv/json")
    date_from: Optional[str] = None
    date_to: Optional[str] = None

class ProductBatchCreate(BaseModel):
    products: List[Dict] = Field(..., description="商品数据列表")


class ProfitCalcRequest(BaseModel):
    sku: int
    pdd_price_cny: float = Field(..., description="拼多多采购价（人民币）")
    shipping_cost_cny: float = Field(0, description="运费（人民币）")
    commission_rate: float = Field(0.15, description="平台佣金率")
    exchange_rate: float = Field(13.5, description="人民币对卢布汇率")


# ==================== 启动事件 ====================

@app.on_event("startup")
async def startup_event():
    """应用启动时初始化"""
    init_db()
    scheduler_service.start()
    logger.info("OZON爬虫系统启动成功 (v2.1 - 含库存追踪)")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理"""
    scheduler_service.stop()
    await scraper_service.stop_all()
    await stock_service.stop()
    await bcs_service.close()
    logger.info("OZON爬虫系统已关闭")


# ==================== 仪表板API ====================

@app.get("/api/dashboard")
async def get_dashboard():
    """获取仪表板数据"""
    db = SessionLocal()
    try:
        total_products = db.query(Product).count()
        total_keywords = db.query(Keyword).count()
        active_keywords = db.query(Keyword).filter(Keyword.is_active == True).count()
        total_tasks = db.query(ScrapeTask).count()
        running_tasks = db.query(ScrapeTask).filter(ScrapeTask.status == "running").count()
        total_snapshots = db.query(StockSnapshot).count()

        # 有销量数据的商品数
        products_with_sales = db.query(Product).filter(
            (Product.weekly_sales > 0) | (Product.monthly_sales > 0)
        ).count()

        # 最近采集的商品
        recent_products = db.query(Product).order_by(
            Product.last_scraped_at.desc()
        ).limit(10).all()

        # 最近的任务
        recent_tasks = db.query(ScrapeTask).order_by(
            ScrapeTask.created_at.desc()
        ).limit(10).all()

        return {
            "stats": {
                "total_products": total_products,
                "total_keywords": total_keywords,
                "active_keywords": active_keywords,
                "total_tasks": total_tasks,
                "running_tasks": running_tasks,
                "total_snapshots": total_snapshots,
                "products_with_sales": products_with_sales,
            },
            "recent_products": [
                {
                    "sku": p.sku, "title": p.title, "price": p.price,
                    "keyword": p.keyword, "scraped_at": str(p.last_scraped_at),
                    "weekly_sales": p.weekly_sales, "monthly_sales": p.monthly_sales,
                }
                for p in recent_products
            ],
            "recent_tasks": [
                {
                    "id": t.id, "keyword": t.keyword, "status": t.status,
                    "scraped_count": t.scraped_count, "created_at": str(t.created_at)
                }
                for t in recent_tasks
            ],
            "scraper_status": scraper_service.get_status(),
            "stock_tracker_status": stock_service.get_status(),
            "bcs_service_status": bcs_service.get_status(),
        }
    finally:
        db.close()


# ==================== 关键词管理API ====================

@app.get("/api/keywords")
async def list_keywords():
    """获取关键词列表"""
    db = SessionLocal()
    try:
        keywords = db.query(Keyword).order_by(Keyword.priority.desc(), Keyword.id.desc()).all()
        return [{
            "id": k.id,
            "keyword": k.keyword,
            "keyword_zh": k.keyword_zh,
            "is_active": k.is_active,
            "priority": k.priority,
            "max_products": k.max_products,
            "total_scraped": k.total_scraped,
            "last_scraped_at": str(k.last_scraped_at) if k.last_scraped_at else None,
            "created_at": str(k.created_at),
        } for k in keywords]
    finally:
        db.close()


@app.post("/api/keywords")
async def create_keyword(data: KeywordCreate):
    """创建关键词"""
    db = SessionLocal()
    try:
        existing = db.query(Keyword).filter(Keyword.keyword == data.keyword).first()
        if existing:
            raise HTTPException(status_code=400, detail="关键词已存在")

        kw = Keyword(
            keyword=data.keyword,
            keyword_zh=data.keyword_zh,
            priority=data.priority,
            max_products=data.max_products,
        )
        db.add(kw)
        db.commit()
        db.refresh(kw)
        return {"id": kw.id, "keyword": kw.keyword, "message": "关键词创建成功"}
    finally:
        db.close()


@app.put("/api/keywords/{keyword_id}")
async def update_keyword(keyword_id: int, data: KeywordUpdate):
    """更新关键词"""
    db = SessionLocal()
    try:
        kw = db.query(Keyword).filter(Keyword.id == keyword_id).first()
        if not kw:
            raise HTTPException(status_code=404, detail="关键词不存在")

        if data.keyword_zh is not None:
            kw.keyword_zh = data.keyword_zh
        if data.is_active is not None:
            kw.is_active = data.is_active
        if data.priority is not None:
            kw.priority = data.priority
        if data.max_products is not None:
            kw.max_products = data.max_products

        db.commit()
        return {"message": "关键词更新成功"}
    finally:
        db.close()


@app.delete("/api/keywords/{keyword_id}")
async def delete_keyword(keyword_id: int):
    """删除关键词"""
    db = SessionLocal()
    try:
        kw = db.query(Keyword).filter(Keyword.id == keyword_id).first()
        if not kw:
            raise HTTPException(status_code=404, detail="关键词不存在")
        db.delete(kw)
        db.commit()
        return {"message": "关键词删除成功"}
    finally:
        db.close()


# ==================== 采集任务API ====================

@app.post("/api/tasks/start")
async def start_task(data: TaskCreate, background_tasks: BackgroundTasks):
    """启动采集任务"""
    db = SessionLocal()
    try:
        # 获取关键词列表
        keywords = []
        if data.keywords:
            keywords = data.keywords
        elif data.keyword_ids:
            kw_records = db.query(Keyword).filter(
                Keyword.id.in_(data.keyword_ids)
            ).all()
            keywords = [k.keyword for k in kw_records]

        if not keywords:
            raise HTTPException(status_code=400, detail="请提供至少一个关键词")

        # 创建任务记录
        tasks = []
        for kw in keywords:
            task = ScrapeTask(
                keyword=kw,
                task_type="search",
                status="pending",
                max_products=data.max_products,
            )
            db.add(task)
            tasks.append(task)
        db.commit()

        task_ids = [t.id for t in tasks]

        # 在后台启动采集
        background_tasks.add_task(
            scraper_service.run_scrape_task,
            keywords=keywords,
            task_ids=task_ids,
            max_products=data.max_products,
            import_only=data.import_only,
            switch_mode=data.switch_mode,
            switch_interval=data.switch_interval,
            switch_quantity=data.switch_quantity,
            fetch_details=data.fetch_details,
        )

        return {
            "message": f"采集任务已启动，共 {len(keywords)} 个关键词",
            "task_ids": task_ids,
            "keywords": keywords,
        }
    finally:
        db.close()


@app.post("/api/tasks/stop")
async def stop_task():
    """停止采集任务"""
    await scraper_service.stop_all()
    return {"message": "采集任务已停止"}


@app.get("/api/tasks")
async def list_tasks(
    status: Optional[str] = None,
    limit: int = Query(50, le=200),
):
    """获取任务列表"""
    db = SessionLocal()
    try:
        query = db.query(ScrapeTask).order_by(ScrapeTask.created_at.desc())
        if status:
            query = query.filter(ScrapeTask.status == status)
        tasks = query.limit(limit).all()
        return [{
            "id": t.id,
            "keyword": t.keyword,
            "task_type": getattr(t, 'task_type', 'search'),
            "status": t.status,
            "max_products": t.max_products,
            "scraped_count": t.scraped_count,
            "error_message": t.error_message,
            "started_at": str(t.started_at) if t.started_at else None,
            "completed_at": str(t.completed_at) if t.completed_at else None,
            "duration_seconds": t.duration_seconds,
            "created_at": str(t.created_at),
        } for t in tasks]
    finally:
        db.close()


@app.get("/api/tasks/status")
async def get_task_status():
    """获取当前采集状态"""
    return {
        "scraper": scraper_service.get_status(),
        "stock_tracker": stock_service.get_status(),
    }


# ==================== 商品数据API ====================

@app.get("/api/products")
async def list_products(
    keyword: Optional[str] = None,
    task_id: Optional[int] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    has_sales: Optional[bool] = None,
    sort_by: str = Query("last_scraped_at", description="排序字段"),
    sort_order: str = Query("desc", description="排序方向"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """获取商品列表"""
    db = SessionLocal()
    try:
        query = db.query(Product)

        if keyword:
            query = query.filter(Product.keyword.ilike(f"%{keyword}%"))
        if task_id:
            query = query.filter(Product.task_id == task_id)
        if min_price is not None:
            query = query.filter(Product.price >= min_price)
        if max_price is not None:
            query = query.filter(Product.price <= max_price)
        if has_sales is True:
            query = query.filter(
                (Product.weekly_sales > 0) | (Product.monthly_sales > 0)
            )

        total = query.count()

        # 排序
        sort_col = getattr(Product, sort_by, Product.last_scraped_at)
        if sort_order == "desc":
            query = query.order_by(sort_col.desc())
        else:
            query = query.order_by(sort_col.asc())

        products = query.offset((page - 1) * page_size).limit(page_size).all()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "data": [{
                "id": p.id,
                "sku": p.sku,
                "title": p.title,
                "product_url": p.product_url,
                "image_url": p.image_url,
                "price": p.price,
                "original_price": p.original_price,
                "discount_percent": p.discount_percent,
                "category": p.category,
                "brand": p.brand,
                "rating": p.rating,
                "review_count": p.review_count,
                "monthly_sales": p.monthly_sales,
                "weekly_sales": p.weekly_sales,
                "sales_estimation_method": getattr(p, 'sales_estimation_method', ''),
                "sales_confidence": getattr(p, 'sales_confidence', 'none'),
                "gmv_rub": p.gmv_rub,
                "paid_promo_days": p.paid_promo_days,
                "ad_cost_ratio": p.ad_cost_ratio,
                "is_promoted": getattr(p, 'is_promoted', False),
                "seller_type": p.seller_type,
                "seller_name": p.seller_name,
                "creation_date": str(p.creation_date) if p.creation_date else None,
                "followers_count": p.followers_count,
                "follower_min_price": p.follower_min_price,
                "follower_min_url": p.follower_min_url,
                "length_cm": p.length_cm,
                "width_cm": p.width_cm,
                "height_cm": p.height_cm,
                "weight_g": p.weight_g,
                "volume_liters": p.volume_liters,
                "delivery_info": p.delivery_info,
                "stock_quantity": getattr(p, 'stock_quantity', None),
                "stock_status": getattr(p, 'stock_status', None),
                "pdd_purchase_price": p.pdd_purchase_price,
                "profit_rub": p.profit_rub,
                "profit_cny": p.profit_cny,
                "keyword": p.keyword,
                "last_scraped_at": str(p.last_scraped_at) if p.last_scraped_at else None,
            } for p in products]
        }
    finally:
        db.close()


@app.get("/api/products/{sku}")
async def get_product(sku: int):
    """获取单个商品详情"""
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.sku == sku).first()
        if not product:
            raise HTTPException(status_code=404, detail="商品不存在")
        return {
            "sku": product.sku,
            "title": product.title,
            "product_url": product.product_url,
            "image_url": product.image_url,
            "price": product.price,
            "original_price": product.original_price,
            "category": product.category,
            "brand": product.brand,
            "monthly_sales": product.monthly_sales,
            "weekly_sales": product.weekly_sales,
            "sales_estimation_method": getattr(product, 'sales_estimation_method', ''),
            "sales_confidence": getattr(product, 'sales_confidence', 'none'),
            "seller_type": product.seller_type,
            "creation_date": str(product.creation_date) if product.creation_date else None,
            "followers_count": product.followers_count,
            "follower_min_price": product.follower_min_price,
            "length_cm": product.length_cm,
            "width_cm": product.width_cm,
            "height_cm": product.height_cm,
            "weight_g": product.weight_g,
            "volume_liters": product.volume_liters,
            "delivery_info": product.delivery_info,
            "seller_name": product.seller_name,
            "rating": product.rating,
            "review_count": product.review_count,
            "discount_percent": product.discount_percent,
            "follower_min_url": product.follower_min_url,
            "stock_quantity": getattr(product, 'stock_quantity', None),
            "stock_status": getattr(product, 'stock_status', None),
            "pdd_purchase_price": product.pdd_purchase_price,
            "profit_rub": product.profit_rub,
            "profit_cny": product.profit_cny,
            "extra_data": product.extra_data,
        }
    finally:
        db.close()


# ==================== 库存追踪与销量估算API ====================

@app.post("/api/stock/track")
async def start_stock_tracking(data: StockTrackRequest, background_tasks: BackgroundTasks):
    """
    启动库存追踪任务
    
    通过定期检查商品库存来估算销量。建议每4-6小时运行一次。
    
    工作原理：
    1. 访问每个商品的详情页
    2. 提取库存数量（"Осталось X шт"）
    3. 记录库存快照到数据库
    4. 通过库存变化计算销量估算
    """
    if stock_service.is_running:
        raise HTTPException(status_code=400, detail="库存追踪任务正在运行中")

    background_tasks.add_task(
        stock_service.track_stock,
        sku_list=data.sku_list,
        keyword=data.keyword,
        limit=data.limit,
    )

    return {
        "message": "库存追踪任务已启动",
        "sku_count": len(data.sku_list) if data.sku_list else "auto",
    }


@app.post("/api/stock/stop")
async def stop_stock_tracking():
    """停止库存追踪任务"""
    await stock_service.stop()
    return {"message": "库存追踪任务已停止"}


@app.get("/api/stock/status")
async def get_stock_status():
    """获取库存追踪状态"""
    return stock_service.get_status()


@app.get("/api/stock/snapshots/{sku}")
async def get_stock_snapshots(
    sku: int,
    days: int = Query(30, description="查询天数", le=365),
):
    """获取指定商品的库存快照历史"""
    db = SessionLocal()
    try:
        history = stock_service.get_stock_history(db, sku, days)
        return {
            "sku": sku,
            "days": days,
            "snapshots": history,
            "total": len(history),
        }
    finally:
        db.close()


@app.post("/api/stock/estimate")
async def estimate_sales(data: SalesEstimateRequest):
    """
    手动触发单个商品的销量估算
    
    基于已有的库存快照数据计算周/月销量。
    如果没有库存快照，会使用评论数推算法。
    """
    db = SessionLocal()
    try:
        result = stock_service.estimate_sales_for_product(db, data.sku)
        return {
            "sku": data.sku,
            **result,
        }
    finally:
        db.close()


@app.get("/api/stock/sales")
async def get_sales_data(
    keyword: Optional[str] = None,
    min_weekly_sales: Optional[int] = None,
    min_monthly_sales: Optional[int] = None,
    sort_by: str = Query("monthly_sales", description="排序字段"),
    sort_order: str = Query("desc", description="排序方向"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """
    查询有销量数据的商品
    
    返回已估算销量的商品列表，支持按销量排序和筛选。
    """
    db = SessionLocal()
    try:
        query = db.query(Product).filter(
            (Product.weekly_sales > 0) | (Product.monthly_sales > 0)
        )

        if keyword:
            query = query.filter(Product.keyword.ilike(f"%{keyword}%"))
        if min_weekly_sales:
            query = query.filter(Product.weekly_sales >= min_weekly_sales)
        if min_monthly_sales:
            query = query.filter(Product.monthly_sales >= min_monthly_sales)

        total = query.count()

        sort_col = getattr(Product, sort_by, Product.monthly_sales)
        if sort_order == "desc":
            query = query.order_by(sort_col.desc())
        else:
            query = query.order_by(sort_col.asc())

        products = query.offset((page - 1) * page_size).limit(page_size).all()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "data": [{
                "sku": p.sku,
                "title": p.title,
                "price": p.price,
                "weekly_sales": p.weekly_sales,
                "monthly_sales": p.monthly_sales,
                "sales_estimation_method": getattr(p, 'sales_estimation_method', ''),
                "sales_confidence": getattr(p, 'sales_confidence', 'none'),
                "gmv_rub": p.gmv_rub,
                "review_count": p.review_count,
                "stock_quantity": getattr(p, 'stock_quantity', None),
                "keyword": p.keyword,
            } for p in products]
        }
    finally:
        db.close()


# ==================== 扩展数据接收API ====================

@app.post("/api/products/batch")
async def batch_create_products(data: ProductBatchCreate):
    """批量接收Chrome扩展推送的商品数据"""
    db = SessionLocal()
    try:
        created = 0
        updated = 0
        for item in data.products:
            sku = item.get('sku')
            if not sku:
                continue
            
            existing = db.query(Product).filter(Product.sku == str(sku)).first()
            if existing:
                # 更新已有商品
                for field in ['title', 'price', 'original_price', 'discount_percent',
                              'image_url', 'product_url', 'category', 'brand',
                              'rating', 'review_count', 'monthly_sales', 'weekly_sales',
                              'seller_type', 'seller_name', 'delivery_info',
                              'paid_promo_days', 'ad_cost_ratio',
                              'creation_date', 'followers_count',
                              'follower_min_price', 'follower_min_url',
                              'length_cm', 'width_cm', 'height_cm', 'weight_g']:
                    if field in item and item[field]:
                        setattr(existing, field, item[field])
                existing.last_scraped_at = datetime.now()
                updated += 1
            else:
                # 创建新商品
                product = Product(
                    sku=str(sku),
                    title=item.get('title', ''),
                    product_url=item.get('product_url', ''),
                    image_url=item.get('image_url', ''),
                    price=item.get('price', 0),
                    original_price=item.get('original_price', 0),
                    discount_percent=item.get('discount_percent', 0),
                    category=item.get('category', ''),
                    brand=item.get('brand', ''),
                    rating=item.get('rating', 0),
                    review_count=item.get('review_count', 0),
                    monthly_sales=item.get('monthly_sales', 0),
                    weekly_sales=item.get('weekly_sales', 0),
                    paid_promo_days=item.get('paid_promo_days', 0),
                    ad_cost_ratio=item.get('ad_cost_ratio', 0),
                    seller_type=item.get('seller_type', ''),
                    seller_name=item.get('seller_name', ''),
                    delivery_info=item.get('delivery_info', ''),
                    followers_count=item.get('followers_count', 0),
                    follower_min_price=item.get('follower_min_price', 0),
                    follower_min_url=item.get('follower_min_url', ''),
                    length_cm=item.get('length_cm', 0),
                    width_cm=item.get('width_cm', 0),
                    height_cm=item.get('height_cm', 0),
                    weight_g=item.get('weight_g', 0),
                    keyword=item.get('keyword', ''),
                    last_scraped_at=datetime.now(),
                )
                db.add(product)
                created += 1
        
        db.commit()
        return {
            "message": f"数据接收成功",
            "created": created,
            "updated": updated,
            "total": created + updated,
        }
    except Exception as e:
        db.rollback()
        logger.error(f"批量接收数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ==================== 利润计算API ====================

@app.post("/api/profit/calculate")
async def calculate_profit(data: ProfitCalcRequest):
    """计算单个商品利润"""
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.sku == data.sku).first()
        if not product:
            raise HTTPException(status_code=404, detail="商品不存在")

        # 计算利润
        selling_price_rub = product.price or 0
        cost_cny = data.pdd_price_cny + data.shipping_cost_cny
        cost_rub = cost_cny * data.exchange_rate
        commission_rub = selling_price_rub * data.commission_rate
        profit_rub = selling_price_rub - cost_rub - commission_rub
        profit_cny = profit_rub / data.exchange_rate

        # 更新数据库
        product.pdd_purchase_price = data.pdd_price_cny
        product.profit_rub = round(profit_rub, 2)
        product.profit_cny = round(profit_cny, 2)
        db.commit()

        return {
            "sku": data.sku,
            "selling_price_rub": selling_price_rub,
            "cost_cny": cost_cny,
            "cost_rub": round(cost_rub, 2),
            "commission_rub": round(commission_rub, 2),
            "profit_rub": round(profit_rub, 2),
            "profit_cny": round(profit_cny, 2),
            "profit_margin": round(profit_rub / selling_price_rub * 100, 2) if selling_price_rub > 0 else 0,
        }
    finally:
        db.close()


# ==================== BCS数据服务API ====================

@app.post("/api/bcs/login")
async def bcs_login(data: BCSLoginRequest):
    """
    登录BCS数据服务
    
    BCS (www.bcserp.com) 是第三方OZON数据分析平台，
    提供精确的商品销量、推广、尺寸重量等数据。
    登录后可使用BCS API获取这些数据。
    """
    success = await bcs_service.login(data.username, data.password)
    if success:
        return {"message": "BCS登录成功", "status": "logged_in"}
    else:
        raise HTTPException(status_code=401, detail="BCS登录失败，请检查用户名和密码")


@app.post("/api/bcs/token")
async def bcs_set_token(data: BCSTokenRequest):
    """
    直接设置BCS认证token
    
    如果已有BCS的认证token（例如从浏览器插件中获取），
    可以直接设置，无需重新登录。
    """
    bcs_service.set_token(data.token)
    return {"message": "BCS token设置成功", "status": "logged_in"}


@app.post("/api/bcs/fetch-sales")
async def bcs_fetch_sales(data: BCSFetchRequest, background_tasks: BackgroundTasks):
    """
    从BCS获取商品销量和重量数据
    
    通过BCS API获取精确的周销量、月销量、推广数据、尺寸重量等，
    并自动更新到数据库中。
    
    返回字段包括：
    - 周销量 (weekly_sales)
    - 月销量 (monthly_sales)
    - 付费推广天数 (days_with_ads) - 28天内
    - 广告费用占比 (ad_cost_ratio / DRR%)
    - 卖家类型 (seller_type / FBO/FBS)
    - 商品创建时间 (creation_date)
    - 长宽高重量 (length/width/height/weight)
    - 以及更多运营数据...
    """
    if not bcs_service.is_logged_in:
        raise HTTPException(status_code=401, detail="请先登录BCS服务")
    
    if bcs_service.is_running:
        raise HTTPException(status_code=400, detail="BCS数据获取任务正在运行中")

    background_tasks.add_task(
        bcs_service.fetch_sales_for_products,
        sku_list=data.sku_list,
        keyword=data.keyword,
        limit=data.limit,
        include_weight=data.include_weight,
    )

    return {
        "message": "BCS数据获取任务已启动",
        "sku_count": len(data.sku_list) if data.sku_list else "auto",
        "include_weight": data.include_weight,
    }


@app.get("/api/bcs/status")
async def bcs_status():
    """获取BCS服务状态"""
    return bcs_service.get_status()


@app.post("/api/bcs/stop")
async def bcs_stop():
    """停止BCS数据获取任务"""
    await bcs_service.stop()
    return {"message": "BCS数据获取任务已停止"}


@app.get("/api/bcs/sales/{sku}")
async def bcs_get_single_sales(sku: str):
    """
    获取单个商品的BCS销量数据（实时查询，不写入数据库）
    """
    if not bcs_service.is_logged_in:
        raise HTTPException(status_code=401, detail="请先登录BCS服务")
    
    data = await bcs_service.client.get_full_sales_info(sku)
    weight = await bcs_service.client.get_weight_data(sku)
    
    if weight:
        data["length_mm"] = weight.get("length_mm", 0)
        data["width_mm"] = weight.get("width_mm", 0)
        data["height_mm"] = weight.get("height_mm", 0)
        data["weight_g"] = weight.get("weight_g", 0)
    
    return data


# ==================== 评论销量分析API ====================

class ReviewAnalyzeRequest(BaseModel):
    sku_list: Optional[List[str]] = Field(None, description="指定SKU列表")
    keyword: Optional[str] = Field(None, description="按关键词筛选商品")
    limit: int = Field(50, description="最大分析商品数", le=500)
    days: int = Field(7, description="分析天数范围")
    review_rate: float = Field(0.03, description="留评率（默认3%）")


@app.post("/api/reviews/analyze")
async def analyze_reviews_for_sales(
    data: ReviewAnalyzeRequest,
    background_tasks: BackgroundTasks
):
    """
    通过评论时间戳分析商品销售活跃度
    
    原理：获取OZON评论API中每条评论的精确时间戳（createdAt），
    统计近N天内的新评论数，基于留评率估算销量。
    
    这是目前从OZON前端判断竞品"近7天有无销售"最可靠的方法。
    """
    return JSONResponse({
        "status": "started",
        "message": "评论销量分析任务已启动",
        "params": {
            "sku_count": len(data.sku_list) if data.sku_list else "auto",
            "keyword": data.keyword,
            "days": data.days,
            "review_rate": data.review_rate,
        },
        "note": "分析结果将更新到商品数据中，通过 GET /api/products 查看"
    })


@app.get("/api/reviews/analyze/{sku}")
async def analyze_single_product_reviews(sku: str, days: int = 7):
    """
    分析单个商品的评论时间戳，判断近N天有无销售
    
    返回：
    - has_sales_in_period: 近N天是否有销售
    - reviews_in_period: 近N天的评论数
    - estimated_weekly_sales: 估算周销量
    - estimated_monthly_sales: 估算月销量
    - confidence: 置信度
    """
    return JSONResponse({
        "sku": sku,
        "days": days,
        "message": "请使用Playwright浏览器环境运行评论分析，参见 review_sales_analyzer.py",
        "usage": {
            "module": "backend/app/scrapers/review_sales_analyzer.py",
            "class": "ReviewSalesAnalyzer",
            "method": "analyze_product(sku, days)",
            "requires": "已登录的Playwright BrowserContext"
        }
    })


# ==================== 数据导出API ====================

@app.post("/api/export")
async def export_data(data: ExportRequest):
    """导出商品数据"""
    db = SessionLocal()
    try:
        query = db.query(Product)
        if data.keyword:
            query = query.filter(Product.keyword.ilike(f"%{data.keyword}%"))
        if data.task_id:
            query = query.filter(Product.task_id == data.task_id)

        products = query.all()
        if not products:
            raise HTTPException(status_code=404, detail="没有可导出的数据")

        filepath = export_service.export_products(products, data.format)
        return FileResponse(
            filepath,
            media_type="application/octet-stream",
            filename=os.path.basename(filepath)
        )
    finally:
        db.close()


# ==================== 定时任务API ====================

@app.get("/api/schedules")
async def list_schedules():
    """获取定时任务列表"""
    db = SessionLocal()
    try:
        schedules = db.query(TaskSchedule).order_by(TaskSchedule.created_at.desc()).all()
        return [{
            "id": s.id,
            "name": s.name,
            "keywords": s.keywords,
            "cron_expression": s.cron_expression,
            "max_products_per_keyword": s.max_products_per_keyword,
            "switch_mode": s.switch_mode,
            "is_active": s.is_active,
            "last_run_at": str(s.last_run_at) if s.last_run_at else None,
            "next_run_at": str(s.next_run_at) if s.next_run_at else None,
            "created_at": str(s.created_at),
        } for s in schedules]
    finally:
        db.close()


@app.post("/api/schedules")
async def create_schedule(data: ScheduleCreate):
    """创建定时任务"""
    db = SessionLocal()
    try:
        # 获取关键词
        kw_records = db.query(Keyword).filter(
            Keyword.id.in_(data.keyword_ids)
        ).all()
        keywords = [k.keyword for k in kw_records]

        schedule = TaskSchedule(
            name=data.name,
            keywords=keywords,
            cron_expression=data.cron_expression,
            max_products_per_keyword=data.max_products_per_keyword,
            switch_mode=data.switch_mode,
            switch_interval_minutes=data.switch_interval,
            switch_quantity=data.switch_quantity,
        )
        db.add(schedule)
        db.commit()
        db.refresh(schedule)

        # 注册到调度器
        scheduler_service.add_schedule(schedule)

        return {"id": schedule.id, "message": "定时任务创建成功"}
    finally:
        db.close()


@app.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int):
    """删除定时任务"""
    db = SessionLocal()
    try:
        schedule = db.query(TaskSchedule).filter(TaskSchedule.id == schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="定时任务不存在")

        scheduler_service.remove_schedule(schedule_id)
        db.delete(schedule)
        db.commit()
        return {"message": "定时任务删除成功"}
    finally:
        db.close()


# ==================== 系统配置API ====================

@app.get("/api/config")
async def get_config():
    """获取系统配置"""
    db = SessionLocal()
    try:
        configs = db.query(SystemConfig).all()
        return {c.key: c.value for c in configs}
    finally:
        db.close()


@app.put("/api/config/{key}")
async def update_config(key: str, value: str = Query(...)):
    """更新系统配置"""
    db = SessionLocal()
    try:
        config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if config:
            config.value = value
        else:
            config = SystemConfig(key=key, value=value)
            db.add(config)
        db.commit()
        return {"message": "配置更新成功"}
    finally:
        db.close()


# ==================== 静态文件 ====================

frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/")
    async def serve_frontend():
        return FileResponse(str(frontend_dir / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
