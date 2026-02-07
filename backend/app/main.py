"""
OZON爬虫系统 - FastAPI后端主应用
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
    Product, Keyword, ScrapeTask, TaskSchedule, SystemConfig
)
from app.services.scraper_service import ScraperService
from app.services.export_service import ExportService
from app.services.scheduler_service import SchedulerService

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
    description="OZON商品数据采集与分析平台",
    version="1.0.0",
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
    logger.info("OZON爬虫系统启动成功")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理"""
    scheduler_service.stop()
    await scraper_service.stop_all()
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
            },
            "recent_products": [
                {
                    "sku": p.sku, "title": p.title, "price": p.price,
                    "keyword": p.keyword, "scraped_at": str(p.last_scraped_at)
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
    """停止当前采集任务"""
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
    return scraper_service.get_status()


# ==================== 商品数据API ====================

@app.get("/api/products")
async def list_products(
    keyword: Optional[str] = None,
    task_id: Optional[int] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
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
                "gmv_rub": p.gmv_rub,
                "paid_promo_days": p.paid_promo_days,
                "ad_cost_ratio": p.ad_cost_ratio,
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
                "delivery_info": p.delivery_info,
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
            "seller_type": product.seller_type,
            "creation_date": str(product.creation_date) if product.creation_date else None,
            "followers_count": product.followers_count,
            "follower_min_price": product.follower_min_price,
            "length_cm": product.length_cm,
            "width_cm": product.width_cm,
            "height_cm": product.height_cm,
            "weight_g": product.weight_g,
            "pdd_purchase_price": product.pdd_purchase_price,
            "profit_rub": product.profit_rub,
            "profit_cny": product.profit_cny,
            "extra_data": product.extra_data,
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
