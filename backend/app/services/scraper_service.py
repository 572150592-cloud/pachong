"""
OZON爬虫服务层
管理爬虫任务的执行、状态跟踪和数据存储
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import List, Optional, Dict, Callable

from app.models.database import SessionLocal, Product, ScrapeTask, Keyword
from app.scrapers.ozon_scraper import OzonScraperManager

logger = logging.getLogger(__name__)


class ScraperService:
    """爬虫服务"""

    def __init__(self):
        self.manager: Optional[OzonScraperManager] = None
        self.is_running = False
        self.current_task_ids: List[int] = []
        self.current_keyword = ""
        self.progress_info: Dict = {}

    def get_status(self) -> Dict:
        """获取当前爬虫状态"""
        return {
            "is_running": self.is_running,
            "current_keyword": self.current_keyword,
            "task_ids": self.current_task_ids,
            "progress": self.progress_info,
        }

    def _on_progress(self, info: Dict):
        """进度回调"""
        self.progress_info = info
        self.current_keyword = info.get("keyword", "")

    async def run_scrape_task(
        self,
        keywords: List[str],
        task_ids: List[int],
        max_products: int = 5000,
        import_only: bool = False,
        switch_mode: str = "sequential",
        switch_interval: int = 30,
        switch_quantity: int = 1000,
        fetch_details: bool = False,
    ):
        """
        执行采集任务

        Args:
            keywords: 关键词列表
            task_ids: 任务ID列表
            max_products: 每个关键词最大采集数
            import_only: 是否仅搜索进口商品
            switch_mode: 切换模式
            switch_interval: 定时切换间隔（分钟）
            switch_quantity: 定量切换阈值
            fetch_details: 是否获取商品详情页数据（类目、尺寸、重量等）
        """
        self.is_running = True
        self.current_task_ids = task_ids

        db = SessionLocal()
        try:
            # 更新任务状态为运行中
            for tid in task_ids:
                task = db.query(ScrapeTask).filter(ScrapeTask.id == tid).first()
                if task:
                    task.status = "running"
                    task.started_at = datetime.utcnow()
            db.commit()

            # 创建爬虫管理器
            self.manager = OzonScraperManager(headless=True)

            # 执行采集（列表页 + 可选的详情页）
            all_products = await self.manager.scrape_keywords(
                keywords=keywords,
                max_products_per_keyword=max_products,
                switch_mode=switch_mode,
                switch_interval_minutes=switch_interval,
                switch_quantity=switch_quantity,
                import_only=import_only,
                fetch_details=fetch_details,
                on_progress=self._on_progress,
            )

            # 存储采集的数据
            keyword_task_map = dict(zip(keywords, task_ids))
            saved_count = 0

            for product_data in all_products:
                try:
                    kw = product_data.get("keyword", "")
                    tid = keyword_task_map.get(kw, task_ids[0] if task_ids else None)

                    # 检查是否已存在
                    existing = db.query(Product).filter(
                        Product.sku == int(product_data.get("sku", 0)),
                        Product.keyword == kw
                    ).first()

                    if existing:
                        # 更新现有记录
                        self._update_product(existing, product_data, tid)
                    else:
                        # 创建新记录
                        product = self._create_product(product_data, kw, tid)
                        db.add(product)

                    saved_count += 1

                    # 批量提交
                    if saved_count % 100 == 0:
                        db.commit()
                        logger.info(f"已保存 {saved_count} 件商品")

                except Exception as e:
                    logger.error(f"保存商品数据出错: {e}")
                    db.rollback()

            db.commit()

            # 更新任务状态为完成
            for i, tid in enumerate(task_ids):
                task = db.query(ScrapeTask).filter(ScrapeTask.id == tid).first()
                if task:
                    kw = keywords[i] if i < len(keywords) else ""
                    kw_count = sum(1 for p in all_products if p.get("keyword") == kw)
                    task.status = "completed"
                    task.scraped_count = kw_count
                    task.completed_at = datetime.utcnow()
                    if task.started_at:
                        task.duration_seconds = int(
                            (task.completed_at - task.started_at).total_seconds()
                        )

            # 更新关键词的采集统计
            for kw in keywords:
                kw_record = db.query(Keyword).filter(Keyword.keyword == kw).first()
                if kw_record:
                    kw_count = sum(1 for p in all_products if p.get("keyword") == kw)
                    kw_record.total_scraped = (kw_record.total_scraped or 0) + kw_count
                    kw_record.last_scraped_at = datetime.utcnow()

            db.commit()
            logger.info(f"采集任务完成，共保存 {saved_count} 件商品")

        except Exception as e:
            logger.error(f"采集任务执行出错: {e}", exc_info=True)
            for tid in task_ids:
                task = db.query(ScrapeTask).filter(ScrapeTask.id == tid).first()
                if task:
                    task.status = "failed"
                    task.error_message = str(e)
                    task.completed_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()
            self.is_running = False
            self.current_task_ids = []
            self.current_keyword = ""
            self.progress_info = {}

    def _create_product(self, data: Dict, keyword: str, task_id: Optional[int]) -> Product:
        """从采集数据创建Product对象"""
        # 处理characteristics字段 - 转为JSON字符串存储在extra_data中
        extra = {}
        if data.get("characteristics"):
            extra["characteristics"] = data["characteristics"]
        if data.get("short_characteristics"):
            extra["short_characteristics"] = data["short_characteristics"]
        if data.get("images"):
            extra["images"] = data["images"]
        if data.get("data_source"):
            extra["data_source"] = data["data_source"]

        # 解析创建时间
        creation_date = None
        if data.get("creation_date"):
            try:
                creation_date = datetime.fromisoformat(
                    data["creation_date"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        return Product(
            sku=int(data.get("sku", 0)),
            title=data.get("title", ""),
            product_url=data.get("product_url", ""),
            image_url=data.get("image_url", ""),
            price=data.get("price", 0),
            original_price=data.get("original_price", 0),
            discount_percent=data.get("discount_percent", 0),
            category=data.get("category", ""),
            brand=data.get("brand", ""),
            rating=data.get("rating", 0),
            review_count=data.get("review_count", 0),
            seller_type=data.get("seller_type", ""),
            seller_name=data.get("seller_name", ""),
            creation_date=creation_date,
            followers_count=data.get("followers_count", 0),
            follower_min_price=data.get("follower_min_price", 0),
            follower_min_url=data.get("follower_min_url", ""),
            length_cm=data.get("length_cm", 0),
            width_cm=data.get("width_cm", 0),
            height_cm=data.get("height_cm", 0),
            weight_g=data.get("weight_g", 0),
            volume_liters=data.get("volume_liters", 0),
            delivery_info=data.get("delivery_info", ""),
            keyword=keyword,
            task_id=task_id,
            extra_data=extra if extra else None,
            last_scraped_at=datetime.utcnow(),
        )

    def _update_product(self, product: Product, data: Dict, task_id: Optional[int]):
        """更新现有Product对象"""
        # 只更新非空值
        field_map = {
            "title": "title",
            "product_url": "product_url",
            "image_url": "image_url",
            "price": "price",
            "original_price": "original_price",
            "discount_percent": "discount_percent",
            "category": "category",
            "brand": "brand",
            "rating": "rating",
            "review_count": "review_count",
            "seller_type": "seller_type",
            "seller_name": "seller_name",
            "followers_count": "followers_count",
            "follower_min_price": "follower_min_price",
            "follower_min_url": "follower_min_url",
            "length_cm": "length_cm",
            "width_cm": "width_cm",
            "height_cm": "height_cm",
            "weight_g": "weight_g",
            "volume_liters": "volume_liters",
            "delivery_info": "delivery_info",
        }

        for data_key, model_key in field_map.items():
            value = data.get(data_key)
            if value:
                setattr(product, model_key, value)

        # 更新创建时间
        if data.get("creation_date") and not product.creation_date:
            try:
                product.creation_date = datetime.fromisoformat(
                    data["creation_date"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        # 更新extra_data
        extra = product.extra_data or {}
        if data.get("characteristics"):
            extra["characteristics"] = data["characteristics"]
        if data.get("images"):
            extra["images"] = data["images"]
        if extra:
            product.extra_data = extra

        product.task_id = task_id
        product.last_scraped_at = datetime.utcnow()

    async def stop_all(self):
        """停止所有采集任务"""
        if self.manager:
            self.manager.cancel()
        self.is_running = False

        db = SessionLocal()
        try:
            running_tasks = db.query(ScrapeTask).filter(
                ScrapeTask.status == "running"
            ).all()
            for task in running_tasks:
                task.status = "cancelled"
                task.completed_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()

        logger.info("所有采集任务已停止")
