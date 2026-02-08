"""
BCS数据服务业务层
管理BCS API的认证、销量数据获取、重量数据获取，并将数据写入数据库。
"""
import asyncio
import logging
from datetime import datetime
from typing import List, Optional, Dict

from app.models.database import SessionLocal, Product
from app.scrapers.bcs_data_service import BCSDataService

logger = logging.getLogger(__name__)


class BCSService:
    """BCS数据服务业务层"""

    def __init__(self):
        self.client = BCSDataService()
        self.is_running = False
        self.is_logged_in = False
        self.progress_info: Dict = {}

    def get_status(self) -> Dict:
        """获取BCS服务状态"""
        return {
            "is_running": self.is_running,
            "is_logged_in": self.is_logged_in,
            "has_token": bool(self.client.token),
            "progress": self.progress_info,
        }

    async def login(self, username: str, password: str) -> bool:
        """登录BCS"""
        success = await self.client.login(username, password)
        self.is_logged_in = success
        return success

    def set_token(self, token: str):
        """直接设置BCS token"""
        self.client.set_token(token)
        self.is_logged_in = True

    async def fetch_sales_for_products(
        self,
        sku_list: Optional[List[str]] = None,
        keyword: Optional[str] = None,
        limit: int = 100,
        include_weight: bool = True,
    ) -> Dict:
        """
        为商品批量获取BCS销量和重量数据，并更新数据库。

        Args:
            sku_list: 指定SKU列表
            keyword: 按关键词筛选商品
            limit: 最大处理商品数
            include_weight: 是否同时获取重量尺寸数据

        Returns:
            处理结果统计
        """
        if not self.client.token:
            return {"error": "BCS未登录，请先调用login或set_token"}

        self.is_running = True
        self.progress_info = {"status": "starting", "current": 0, "total": 0}

        db = SessionLocal()
        try:
            # 获取SKU列表
            if not sku_list:
                query = db.query(Product)
                if keyword:
                    query = query.filter(Product.keyword.ilike(f"%{keyword}%"))
                products = query.order_by(Product.last_scraped_at.desc()).limit(limit).all()
                sku_list = [str(p.sku) for p in products]

            if not sku_list:
                return {"error": "没有找到需要处理的商品"}

            total = len(sku_list)
            self.progress_info = {"status": "running", "current": 0, "total": total}

            updated_count = 0
            failed_count = 0
            results_summary = []

            for i, sku in enumerate(sku_list):
                try:
                    logger.info(f"BCS数据获取 [{i+1}/{total}]: SKU={sku}")
                    self.progress_info = {
                        "status": "running",
                        "current": i + 1,
                        "total": total,
                        "current_sku": sku,
                    }

                    # 获取销量数据
                    sales_data = await self.client.get_full_sales_info(sku)

                    # 获取重量数据（可选）
                    weight_data = None
                    if include_weight:
                        weight_data = await self.client.get_weight_data(sku)

                    # 更新数据库
                    product = db.query(Product).filter(
                        Product.sku == int(sku)
                    ).first()

                    if product and sales_data:
                        # 更新销量数据
                        monthly_sales_str = sales_data.get("monthly_sales", "")
                        weekly_sales_str = sales_data.get("weekly_sales", "")

                        if monthly_sales_str:
                            try:
                                product.monthly_sales = int(
                                    str(monthly_sales_str).replace(" ", "").replace(",", "")
                                )
                            except (ValueError, TypeError):
                                pass

                        if weekly_sales_str:
                            try:
                                product.weekly_sales = int(
                                    str(weekly_sales_str).replace(" ", "").replace(",", "")
                                )
                            except (ValueError, TypeError):
                                pass

                        # 更新推广数据
                        if sales_data.get("days_with_ads"):
                            product.paid_promo_days = sales_data["days_with_ads"]
                        if sales_data.get("ad_cost_ratio"):
                            product.ad_cost_ratio = sales_data["ad_cost_ratio"]

                        # 更新卖家类型
                        if sales_data.get("seller_type") and not product.seller_type:
                            product.seller_type = sales_data["seller_type"]

                        # 更新创建时间
                        if sales_data.get("creation_date") and not product.creation_date:
                            try:
                                product.creation_date = datetime.strptime(
                                    sales_data["creation_date"], "%Y-%m-%d"
                                )
                            except (ValueError, TypeError):
                                pass

                        # 更新类目
                        if sales_data.get("category_name") and not product.category:
                            product.category = sales_data["category_name"]

                        # 更新品牌
                        if sales_data.get("brand") and not product.brand:
                            product.brand = sales_data["brand"]

                        # 更新GMV
                        if sales_data.get("monthly_gmv"):
                            product.gmv_rub = sales_data["monthly_gmv"]

                        # 更新销量估算方法
                        product.sales_estimation_method = "bcs_api"
                        product.sales_confidence = "high"

                        # 更新重量尺寸数据
                        if weight_data:
                            if weight_data.get("length_mm"):
                                product.length_cm = weight_data["length_mm"] / 10.0
                            if weight_data.get("width_mm"):
                                product.width_cm = weight_data["width_mm"] / 10.0
                            if weight_data.get("height_mm"):
                                product.height_cm = weight_data["height_mm"] / 10.0
                            if weight_data.get("weight_g"):
                                product.weight_g = weight_data["weight_g"]

                        # 保存额外数据到extra_data
                        extra = product.extra_data or {}
                        extra["bcs_data"] = {
                            "days_in_promo": sales_data.get("days_in_promo", 0),
                            "days_with_ads": sales_data.get("days_with_ads", 0),
                            "monthly_gmv": sales_data.get("monthly_gmv", 0),
                            "ad_cost_ratio": sales_data.get("ad_cost_ratio", 0),
                            "sales_dynamics": sales_data.get("sales_dynamics", ""),
                            "conversion_rate": sales_data.get("conversion_rate", 0),
                            "total_views": sales_data.get("total_views", 0),
                            "view_to_order_rate": sales_data.get("view_to_order_rate", 0),
                            "click_count": sales_data.get("click_count", 0),
                            "cart_conversion_rate": sales_data.get("cart_conversion_rate", 0),
                            "search_views": sales_data.get("search_views", 0),
                            "fetched_at": datetime.utcnow().isoformat(),
                        }
                        product.extra_data = extra
                        product.last_scraped_at = datetime.utcnow()

                        updated_count += 1
                        results_summary.append({
                            "sku": sku,
                            "monthly_sales": monthly_sales_str,
                            "weekly_sales": weekly_sales_str,
                            "status": "updated",
                        })

                    # 每50个商品提交一次
                    if (i + 1) % 50 == 0:
                        db.commit()
                        logger.info(f"已更新 {updated_count} 件商品的BCS数据")

                except Exception as e:
                    logger.error(f"处理SKU {sku} 的BCS数据出错: {e}")
                    failed_count += 1
                    results_summary.append({
                        "sku": sku,
                        "status": "failed",
                        "error": str(e),
                    })

            db.commit()

            self.progress_info = {
                "status": "completed",
                "current": total,
                "total": total,
                "updated": updated_count,
                "failed": failed_count,
            }

            logger.info(
                f"BCS数据获取完成: 总计{total}, 更新{updated_count}, 失败{failed_count}"
            )

            return {
                "total": total,
                "updated": updated_count,
                "failed": failed_count,
                "results": results_summary[:20],  # 只返回前20个结果的摘要
            }

        except Exception as e:
            logger.error(f"BCS数据获取任务出错: {e}", exc_info=True)
            return {"error": str(e)}
        finally:
            db.close()
            self.is_running = False

    async def stop(self):
        """停止BCS数据获取"""
        self.is_running = False
        await self.client.close()

    async def close(self):
        """关闭BCS服务"""
        await self.client.close()
