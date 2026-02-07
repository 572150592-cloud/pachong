"""
OZON爬虫服务层
管理爬虫任务的执行、状态跟踪和数据存储
"""
import asyncio
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
    ):
        """执行采集任务"""
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

            # 执行采集
            all_products = await self.manager.scrape_keywords(
                keywords=keywords,
                max_products_per_keyword=max_products,
                switch_mode=switch_mode,
                switch_interval_minutes=switch_interval,
                switch_quantity=switch_quantity,
                import_only=import_only,
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
                        for key, value in product_data.items():
                            if key not in ("sku", "keyword", "scraped_at") and value:
                                setattr(existing, key, value)
                        existing.last_scraped_at = datetime.utcnow()
                        existing.task_id = tid
                    else:
                        # 创建新记录
                        product = Product(
                            sku=int(product_data.get("sku", 0)),
                            title=product_data.get("title", ""),
                            product_url=product_data.get("product_url", ""),
                            image_url=product_data.get("image_url", ""),
                            price=product_data.get("price", 0),
                            original_price=product_data.get("original_price", 0),
                            discount_percent=product_data.get("discount_percent", 0),
                            brand=product_data.get("brand", ""),
                            rating=product_data.get("rating", 0),
                            review_count=product_data.get("review_count", 0),
                            delivery_info=product_data.get("delivery_info", ""),
                            seller_type=product_data.get("seller_type", ""),
                            keyword=kw,
                            task_id=tid,
                            last_scraped_at=datetime.utcnow(),
                        )
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
            # 更新任务状态为失败
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

    async def stop_all(self):
        """停止所有采集任务"""
        if self.manager:
            self.manager.cancel()
        self.is_running = False

        # 更新运行中的任务状态
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
