"""
OZON库存追踪与销量估算服务
============================
管理库存快照的采集、存储和销量估算计算。

使用方式：
1. 定时任务：每4-6小时运行一次库存检查，记录快照
2. 手动触发：通过API手动触发指定SKU的库存检查
3. 销量计算：基于库存快照自动计算周/月销量

API接口：
- POST /api/stock/track     - 启动库存追踪任务
- GET  /api/stock/snapshots - 查询库存快照历史
- POST /api/stock/estimate  - 手动触发销量估算
- GET  /api/stock/sales     - 查询销量估算结果
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict

from app.models.database import SessionLocal, Product, StockSnapshot
from app.scrapers.stock_tracker import StockTracker, SalesEstimator

logger = logging.getLogger(__name__)


class StockService:
    """库存追踪与销量估算服务"""

    def __init__(self):
        self.tracker: Optional[StockTracker] = None
        self.is_running = False
        self.progress_info: Dict = {}

    def get_status(self) -> Dict:
        """获取当前库存追踪状态"""
        return {
            "is_running": self.is_running,
            "progress": self.progress_info,
        }

    async def track_stock(
        self,
        sku_list: Optional[List[str]] = None,
        keyword: Optional[str] = None,
        limit: int = 100,
        delay_range: tuple = (2, 5),
    ) -> Dict:
        """
        执行库存追踪任务
        
        Args:
            sku_list: 指定SKU列表（优先）
            keyword: 按关键词选择商品（如果没有指定sku_list）
            limit: 最大追踪商品数
            delay_range: 请求间延迟
            
        Returns:
            追踪结果统计
        """
        self.is_running = True
        db = SessionLocal()
        results = {"total": 0, "success": 0, "failed": 0, "snapshots_created": 0}

        try:
            # 确定要追踪的SKU列表
            if not sku_list:
                query = db.query(Product.sku).distinct()
                if keyword:
                    query = query.filter(Product.keyword == keyword)
                sku_list = [str(row[0]) for row in query.limit(limit).all()]

            if not sku_list:
                logger.warning("没有找到需要追踪的商品")
                return results

            results["total"] = len(sku_list)
            logger.info(f"开始库存追踪，共 {len(sku_list)} 个商品")

            # 启动追踪器
            self.tracker = StockTracker(headless=True)
            await self.tracker.start()

            try:
                stock_infos = await self.tracker.batch_get_stock(
                    sku_list=sku_list,
                    delay_range=delay_range,
                )

                for info in stock_infos:
                    try:
                        sku = info.get("sku", "")
                        if not sku:
                            continue

                        # 保存库存快照
                        snapshot = StockSnapshot(
                            sku=int(sku),
                            stock_quantity=info.get("stock_quantity"),
                            stock_status=info.get("stock_status", "unknown"),
                            stock_text=info.get("stock_text", ""),
                            max_cart_quantity=info.get("max_cart_quantity"),
                            review_count=info.get("review_count", 0),
                            orders_text=info.get("orders_text", ""),
                            snapshot_time=datetime.now(),
                        )
                        db.add(snapshot)
                        results["snapshots_created"] += 1

                        # 更新商品表的库存信息
                        products = db.query(Product).filter(
                            Product.sku == int(sku)
                        ).all()
                        for product in products:
                            if info.get("stock_quantity") is not None:
                                product.stock_quantity = info["stock_quantity"]
                            product.stock_status = info.get("stock_status", "unknown")
                            product.last_stock_check_at = datetime.now()

                        results["success"] += 1

                        # 批量提交
                        if results["success"] % 20 == 0:
                            db.commit()
                            logger.info(f"库存追踪进度: {results['success']}/{results['total']}")

                    except Exception as e:
                        logger.error(f"保存库存快照出错 (SKU: {info.get('sku')}): {e}")
                        results["failed"] += 1
                        db.rollback()

                db.commit()

            finally:
                await self.tracker.stop()

            # 追踪完成后，自动计算销量估算
            logger.info("库存追踪完成，开始计算销量估算...")
            await self._update_sales_estimates(db, sku_list)
            db.commit()

        except Exception as e:
            logger.error(f"库存追踪任务出错: {e}", exc_info=True)
            db.rollback()
        finally:
            db.close()
            self.is_running = False
            self.progress_info = {}

        logger.info(f"库存追踪完成: {results}")
        return results

    async def _update_sales_estimates(self, db, sku_list: List[str]):
        """
        基于库存快照更新销量估算
        """
        estimator = SalesEstimator()

        for sku in sku_list:
            try:
                sku_int = int(sku)

                # 获取该SKU的所有库存快照
                snapshots = db.query(StockSnapshot).filter(
                    StockSnapshot.sku == sku_int
                ).order_by(StockSnapshot.snapshot_time.asc()).all()

                snapshot_dicts = [
                    {
                        "stock_quantity": s.stock_quantity,
                        "snapshot_time": s.snapshot_time.isoformat() if s.snapshot_time else "",
                        "review_count": s.review_count or 0,
                    }
                    for s in snapshots
                ]

                # 获取当前和历史评论数
                current_reviews = 0
                previous_reviews = 0
                if snapshot_dicts:
                    current_reviews = snapshot_dicts[-1].get("review_count", 0)
                    if len(snapshot_dicts) > 1:
                        # 找7天前的评论数
                        seven_days_ago = datetime.now() - timedelta(days=7)
                        for s in snapshot_dicts:
                            snap_time = datetime.fromisoformat(s["snapshot_time"])
                            if snap_time <= seven_days_ago:
                                previous_reviews = s.get("review_count", 0)

                # 计算销量
                sales_data = estimator.calculate_weekly_monthly_sales(
                    snapshots=snapshot_dicts,
                    current_reviews=current_reviews,
                    previous_reviews=previous_reviews,
                )

                # 更新商品表
                products = db.query(Product).filter(Product.sku == sku_int).all()
                for product in products:
                    product.weekly_sales = sales_data["weekly_sales"]
                    product.monthly_sales = sales_data["monthly_sales"]
                    product.sales_estimation_method = sales_data["monthly_method"]
                    product.sales_confidence = sales_data["monthly_confidence"]
                    # 计算月销售额
                    if product.price and sales_data["monthly_sales"]:
                        product.gmv_rub = product.price * sales_data["monthly_sales"]

            except Exception as e:
                logger.error(f"更新销量估算出错 (SKU: {sku}): {e}")

    def estimate_sales_for_product(self, db, sku: int) -> Dict:
        """
        为单个商品计算销量估算（不需要新的库存检查）
        """
        snapshots = db.query(StockSnapshot).filter(
            StockSnapshot.sku == sku
        ).order_by(StockSnapshot.snapshot_time.asc()).all()

        if not snapshots:
            # 没有库存快照，只能用评论数估算
            product = db.query(Product).filter(Product.sku == sku).first()
            if product and product.review_count:
                estimator = SalesEstimator()
                return estimator.calculate_weekly_monthly_sales(
                    snapshots=[],
                    current_reviews=product.review_count,
                )
            return {
                "weekly_sales": 0,
                "monthly_sales": 0,
                "weekly_method": "no_data",
                "monthly_method": "no_data",
                "weekly_confidence": "none",
                "monthly_confidence": "none",
                "message": "没有库存快照数据，请先运行库存追踪任务",
            }

        snapshot_dicts = [
            {
                "stock_quantity": s.stock_quantity,
                "snapshot_time": s.snapshot_time.isoformat() if s.snapshot_time else "",
                "review_count": s.review_count or 0,
            }
            for s in snapshots
        ]

        current_reviews = snapshot_dicts[-1].get("review_count", 0) if snapshot_dicts else 0
        previous_reviews = 0
        if len(snapshot_dicts) > 1:
            seven_days_ago = datetime.now() - timedelta(days=7)
            for s in snapshot_dicts:
                snap_time = datetime.fromisoformat(s["snapshot_time"])
                if snap_time <= seven_days_ago:
                    previous_reviews = s.get("review_count", 0)

        estimator = SalesEstimator()
        return estimator.calculate_weekly_monthly_sales(
            snapshots=snapshot_dicts,
            current_reviews=current_reviews,
            previous_reviews=previous_reviews,
        )

    def get_stock_history(self, db, sku: int, days: int = 30) -> List[Dict]:
        """获取指定SKU的库存历史"""
        cutoff = datetime.now() - timedelta(days=days)
        snapshots = db.query(StockSnapshot).filter(
            StockSnapshot.sku == sku,
            StockSnapshot.snapshot_time >= cutoff,
        ).order_by(StockSnapshot.snapshot_time.asc()).all()

        return [
            {
                "id": s.id,
                "sku": s.sku,
                "stock_quantity": s.stock_quantity,
                "stock_status": s.stock_status,
                "stock_text": s.stock_text,
                "review_count": s.review_count,
                "price": s.price,
                "snapshot_time": s.snapshot_time.isoformat() if s.snapshot_time else "",
            }
            for s in snapshots
        ]

    async def stop(self):
        """停止库存追踪"""
        self.is_running = False
        if self.tracker:
            await self.tracker.stop()
