"""
BCS数据服务模块 v1.0
====================
通过逆向BCS Ozon Plus插件发现的API接口，获取商品销量、重量等数据。

BCS (www.bcserp.com) 是一个第三方OZON数据分析平台，其Chrome插件通过
自有后端API提供销量、推广、尺寸重量等OZON前端不直接展示的数据。

API端点（通过逆向分析获得）：
- 登录: POST https://www.bcserp.com/prod-api/pluginLogin
- 销量数据(月): GET https://ozon.bcserp.com/prod-api/system/sku/skuss/new?sku=<SKU>
- 销量数据(周): GET https://ozon.bcserp.com/prod-api/system/sku/skuss/new?sku=<SKU>&period=weekly
- 重量尺寸: POST https://ozon.bcserp.com/prod-api/system/ozonRecord/shops
- 用户信息: GET https://ozon.bcserp.com/prod-api/getInfo

返回字段说明：
- monthsales: 月销量
- daysInPromo: 促销活动参与天数(28天内)
- daysWithTrafarets: 付费推广参与天数(28天内)
- gmvSum: 月销售额(GMV)
- drr: 广告费用占比(DRR%)
- salesDynamics: 周转动态
- nullableRedemptionRate: 成交率
- views: 商品展示总量
- convViewToOrder: 展示转化率
- sessioncount: 商品点击量
- convTocartPdp: 购物车转化率
- discount: 促销活动折扣
- promoRevenueShare: 促销活动转化率
- volume: 体积/公升
- avgprice: 平均价格
- sources: 卖家类型
- sessionCountSearch: 搜索中的浏览量
- createDate: 商品创建时间
- article: 货号
- brand: 品牌
- catname: 类目名称

重量尺寸数据key:
- 9454: 长度(mm)
- 9455: 宽度(mm)
- 9456: 高度(mm)
- 4497: 重量(g)
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Any

import aiohttp

logger = logging.getLogger(__name__)


class BCSDataService:
    """
    BCS数据服务 - 通过BCS后端API获取OZON商品的销量、推广、尺寸重量等数据。

    使用方式:
        service = BCSDataService()
        await service.login("username", "password")
        sales_data = await service.get_sales_data("1681720585")
        weight_data = await service.get_weight_data("1681720585")
    """

    # BCS后端API基础URL
    BASE_URL = "https://ozon.bcserp.com/prod-api"
    AUTH_URL = "https://www.bcserp.com/prod-api"

    # 请求间隔（秒），避免触发BCS的频率限制
    REQUEST_INTERVAL = 0.5

    # 重量尺寸数据的属性key映射
    DIMENSION_KEYS = {
        "9454": "length_mm",
        "9455": "width_mm",
        "9456": "height_mm",
        "4497": "weight_g",
    }

    def __init__(self, token: Optional[str] = None):
        self.token = token
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0

    async def _ensure_session(self):
        """确保HTTP会话已创建"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/131.0.0.0 Safari/537.36",
                }
            )

    async def _rate_limit(self):
        """简单的请求频率限制"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.REQUEST_INTERVAL:
            await asyncio.sleep(self.REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()

    async def close(self):
        """关闭HTTP会话"""
        if self._session and not self._session.closed:
            await self._session.close()

    # ==================== 认证 ====================

    async def login(self, username: str, password: str) -> bool:
        """
        登录BCS获取认证token。

        Args:
            username: BCS账号用户名
            password: BCS账号密码

        Returns:
            登录是否成功
        """
        await self._ensure_session()
        try:
            async with self._session.post(
                f"{self.AUTH_URL}/pluginLogin",
                json={"username": username, "password": password},
            ) as resp:
                data = await resp.json()
                if data.get("token"):
                    self.token = data["token"]
                    logger.info("BCS登录成功")
                    return True
                else:
                    msg = data.get("msg", "登录失败")
                    logger.error(f"BCS登录失败: {msg}")
                    return False
        except Exception as e:
            logger.error(f"BCS登录请求出错: {e}")
            return False

    def set_token(self, token: str):
        """直接设置认证token（如果已有token可跳过login）"""
        self.token = token

    def _get_headers(self) -> Dict:
        """获取带认证的请求头"""
        headers = {}
        if self.token:
            headers["Authorization"] = self.token
        return headers

    # ==================== 销量数据 ====================

    async def get_sales_data(self, sku: str, period: Optional[str] = None) -> Optional[Dict]:
        """
        获取商品销量数据。

        Args:
            sku: 商品SKU
            period: 时间周期，None表示月度数据，"weekly"表示周度数据

        Returns:
            包含销量等数据的字典，失败返回None

        返回数据示例:
            {
                "monthsales": "1234",
                "article": "ARTXXX",
                "brand": "Apple",
                "catname": "Смартфоны",
                "daysInPromo": 15,
                "daysWithTrafarets": 8,
                "gmvSum": 5678900,
                "drr": 12.5,
                "salesDynamics": "↑",
                "nullableRedemptionRate": 3.2,
                "views": 45000,
                "convViewToOrder": 2.1,
                "sessioncount": 12000,
                "convTocartPdp": 8.5,
                "discount": 15,
                "promoRevenueShare": 25,
                "volume": "0.5",
                "avgprice": 45000,
                "sources": "FBO",
                "sessionCountSearch": 8000,
                "createDate": "2024-01-15"
            }
        """
        if not self.token:
            logger.warning("BCS未登录，无法获取销量数据")
            return None

        await self._ensure_session()
        await self._rate_limit()

        url = f"{self.BASE_URL}/system/sku/skuss/new?sku={sku}"
        if period:
            url += f"&period={period}"

        try:
            async with self._session.get(url, headers=self._get_headers()) as resp:
                data = await resp.json()

                if data.get("code") == 401:
                    logger.error("BCS token已过期，请重新登录")
                    return None
                elif data.get("code") == 200:
                    return data.get("data")
                else:
                    logger.debug(f"BCS销量数据请求返回: code={data.get('code')}, msg={data.get('msg')}")
                    return data.get("data")

        except Exception as e:
            logger.error(f"获取SKU {sku} 销量数据出错: {e}")
            return None

    async def get_weekly_sales(self, sku: str) -> Optional[str]:
        """
        获取商品周销量。

        Args:
            sku: 商品SKU

        Returns:
            周销量字符串，失败返回None
        """
        data = await self.get_sales_data(sku, period="weekly")
        if data and data.get("monthsales"):
            return data["monthsales"]
        return None

    async def get_monthly_sales(self, sku: str) -> Optional[str]:
        """
        获取商品月销量。

        Args:
            sku: 商品SKU

        Returns:
            月销量字符串，失败返回None
        """
        data = await self.get_sales_data(sku)
        if data and data.get("monthsales"):
            return data["monthsales"]
        return None

    async def get_full_sales_info(self, sku: str) -> Dict:
        """
        获取商品的完整销量和运营数据（月度+周度）。

        Args:
            sku: 商品SKU

        Returns:
            包含月销量、周销量、推广数据等的完整字典
        """
        result = {
            "sku": sku,
            "weekly_sales": "",
            "monthly_sales": "",
            "article": "",
            "brand": "",
            "category_name": "",
            "days_in_promo": 0,
            "days_with_ads": 0,
            "monthly_gmv": 0,
            "ad_cost_ratio": 0,
            "sales_dynamics": "",
            "conversion_rate": 0,
            "total_views": 0,
            "view_to_order_rate": 0,
            "click_count": 0,
            "cart_conversion_rate": 0,
            "promo_discount": 0,
            "promo_revenue_share": 0,
            "volume_liters": "",
            "avg_price": 0,
            "seller_type": "",
            "search_views": 0,
            "creation_date": "",
            "data_source": "bcs_api",
        }

        # 获取月度数据
        monthly_data = await self.get_sales_data(sku)
        if monthly_data:
            result["monthly_sales"] = str(monthly_data.get("monthsales", ""))
            result["article"] = str(monthly_data.get("article", ""))
            result["brand"] = str(monthly_data.get("brand", ""))
            result["category_name"] = str(monthly_data.get("catname", ""))
            result["days_in_promo"] = int(monthly_data.get("daysInPromo", 0) or 0)
            result["days_with_ads"] = int(monthly_data.get("daysWithTrafarets", 0) or 0)
            result["monthly_gmv"] = float(monthly_data.get("gmvSum", 0) or 0)
            result["ad_cost_ratio"] = float(monthly_data.get("drr", 0) or 0)
            result["sales_dynamics"] = str(monthly_data.get("salesDynamics", ""))
            result["conversion_rate"] = float(monthly_data.get("nullableRedemptionRate", 0) or 0)
            result["total_views"] = int(monthly_data.get("views", 0) or 0)
            result["view_to_order_rate"] = float(monthly_data.get("convViewToOrder", 0) or 0)
            result["click_count"] = int(monthly_data.get("sessioncount", 0) or 0)
            result["cart_conversion_rate"] = float(monthly_data.get("convTocartPdp", 0) or 0)
            result["promo_discount"] = float(monthly_data.get("discount", 0) or 0)
            result["promo_revenue_share"] = float(monthly_data.get("promoRevenueShare", 0) or 0)
            result["volume_liters"] = str(monthly_data.get("volume", ""))
            result["avg_price"] = float(monthly_data.get("avgprice", 0) or 0)
            result["seller_type"] = str(monthly_data.get("sources", ""))
            result["search_views"] = int(monthly_data.get("sessionCountSearch", 0) or 0)
            result["creation_date"] = str(monthly_data.get("createDate", ""))

            # 如果有月销量数据，再获取周销量
            if monthly_data.get("monthsales"):
                weekly_data = await self.get_sales_data(sku, period="weekly")
                if weekly_data and weekly_data.get("monthsales"):
                    result["weekly_sales"] = str(weekly_data["monthsales"])

        return result

    # ==================== 重量尺寸数据 ====================

    async def get_weight_data(self, sku: str) -> Optional[Dict]:
        """
        获取商品的重量和尺寸数据。

        Args:
            sku: 商品SKU

        Returns:
            包含长度、宽度、高度、重量的字典

        返回数据示例:
            {
                "length_mm": 160,
                "width_mm": 78,
                "height_mm": 8,
                "weight_g": 171
            }
        """
        if not self.token:
            logger.warning("BCS未登录，无法获取重量数据")
            return None

        await self._ensure_session()
        await self._rate_limit()

        try:
            async with self._session.post(
                f"{self.BASE_URL}/system/ozonRecord/shops",
                json={"sku": sku},
                headers=self._get_headers(),
            ) as resp:
                data = await resp.json()

                if data.get("data") and len(data["data"]) > 0:
                    attributes = data["data"][0].get("attributes", [])
                    result = {
                        "length_mm": 0,
                        "width_mm": 0,
                        "height_mm": 0,
                        "weight_g": 0,
                    }
                    for attr in attributes:
                        key = str(attr.get("key", ""))
                        value = attr.get("value", 0)
                        if key in self.DIMENSION_KEYS:
                            field_name = self.DIMENSION_KEYS[key]
                            try:
                                result[field_name] = float(value) if value else 0
                            except (ValueError, TypeError):
                                result[field_name] = 0
                    return result

        except Exception as e:
            logger.error(f"获取SKU {sku} 重量数据出错: {e}")

        return None

    # ==================== 批量获取 ====================

    async def batch_get_sales_data(
        self,
        sku_list: List[str],
        include_weekly: bool = True,
        on_progress: Optional[callable] = None,
    ) -> List[Dict]:
        """
        批量获取商品销量数据。

        Args:
            sku_list: SKU列表
            include_weekly: 是否同时获取周销量
            on_progress: 进度回调函数

        Returns:
            销量数据列表
        """
        results = []
        total = len(sku_list)

        for i, sku in enumerate(sku_list):
            logger.info(f"获取销量数据 [{i+1}/{total}]: SKU={sku}")

            if include_weekly:
                data = await self.get_full_sales_info(sku)
            else:
                monthly = await self.get_sales_data(sku)
                data = {
                    "sku": sku,
                    "monthly_sales": str(monthly.get("monthsales", "")) if monthly else "",
                    "data_source": "bcs_api",
                }

            results.append(data)

            if on_progress:
                on_progress({
                    "current": i + 1,
                    "total": total,
                    "sku": sku,
                    "status": "running",
                })

        logger.info(f"批量销量数据获取完成: {len(results)}/{total}")
        return results

    async def batch_get_weight_data(
        self,
        sku_list: List[str],
        on_progress: Optional[callable] = None,
    ) -> List[Dict]:
        """
        批量获取商品重量尺寸数据。

        Args:
            sku_list: SKU列表
            on_progress: 进度回调函数

        Returns:
            重量尺寸数据列表
        """
        results = []
        total = len(sku_list)

        for i, sku in enumerate(sku_list):
            logger.info(f"获取重量数据 [{i+1}/{total}]: SKU={sku}")

            data = await self.get_weight_data(sku)
            if data:
                data["sku"] = sku
            else:
                data = {
                    "sku": sku,
                    "length_mm": 0,
                    "width_mm": 0,
                    "height_mm": 0,
                    "weight_g": 0,
                }

            results.append(data)

            if on_progress:
                on_progress({
                    "current": i + 1,
                    "total": total,
                    "sku": sku,
                    "status": "running",
                })

        logger.info(f"批量重量数据获取完成: {len(results)}/{total}")
        return results

    async def batch_get_all_data(
        self,
        sku_list: List[str],
        on_progress: Optional[callable] = None,
    ) -> List[Dict]:
        """
        批量获取商品的完整数据（销量 + 重量尺寸）。

        Args:
            sku_list: SKU列表
            on_progress: 进度回调函数

        Returns:
            完整数据列表，每个元素包含销量和重量尺寸数据
        """
        results = []
        total = len(sku_list)

        for i, sku in enumerate(sku_list):
            logger.info(f"获取完整数据 [{i+1}/{total}]: SKU={sku}")

            # 并发获取销量和重量数据
            sales_task = self.get_full_sales_info(sku)
            weight_task = self.get_weight_data(sku)
            sales_data, weight_data = await asyncio.gather(sales_task, weight_task)

            # 合并数据
            combined = sales_data.copy() if sales_data else {"sku": sku}
            if weight_data:
                combined["length_mm"] = weight_data.get("length_mm", 0)
                combined["width_mm"] = weight_data.get("width_mm", 0)
                combined["height_mm"] = weight_data.get("height_mm", 0)
                combined["weight_g"] = weight_data.get("weight_g", 0)

            results.append(combined)

            if on_progress:
                on_progress({
                    "current": i + 1,
                    "total": total,
                    "sku": sku,
                    "status": "running",
                })

        logger.info(f"批量完整数据获取完成: {len(results)}/{total}")
        return results
