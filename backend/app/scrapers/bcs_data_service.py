"""
BCS数据服务模块 v2.0（伪装加固版）
===================================
通过模拟BCS Ozon Plus Chrome插件的真实请求行为，获取商品销量、重量等数据。

伪装策略：
1. 完全模拟Chrome插件的请求特征（Headers、Origin、Referer）
2. 模拟插件的认证流程（pluginLogin → inspectCookie → 数据请求）
3. 模拟插件的请求频率（10s/1min计数器，自然间隔）
4. 请求通过浏览器上下文发起（使用已登录的OZON页面作为Origin）
5. 随机化请求间隔，模拟真实用户浏览行为

风险评估：
- BCS服务端能看到的信息：IP地址、请求Headers、请求频率、token
- BCS服务端无法看到的信息：User-Agent是否来自真实Chrome扩展
- 关键伪装点：Origin必须是ozon.ru页面、请求频率不能过快、
  必须先调inspectCookie、token使用方式与插件一致
"""

import asyncio
import logging
import random
import time
from typing import Dict, List, Optional, Any

import aiohttp

logger = logging.getLogger(__name__)


class BCSDataService:
    """
    BCS数据服务 v2.0 - 完全模拟Chrome插件行为的伪装版本。

    核心伪装措施：
    1. Headers完全复刻插件的jQuery.ajax默认行为
    2. 登录后自动调用inspectCookie（与插件行为一致）
    3. 请求间隔随机化（1.5~3.5秒），模拟用户浏览商品的自然节奏
    4. 批量请求时模拟10s/1min的请求计数器行为
    5. Origin和Referer设置为ozon.ru（插件从OZON页面发起请求）
    """

    # BCS后端API基础URL（与插件完全一致）
    BASE_URL = "https://ozon.bcserp.com/prod-api"
    AUTH_URL = "https://www.bcserp.com/prod-api"

    # 重量尺寸数据的属性key映射（逆向确认）
    DIMENSION_KEYS = {
        "9454": "length_mm",   # 长度
        "9455": "width_mm",    # 宽度
        "9456": "height_mm",   # 高度
        "4497": "weight_g",    # 重量
    }

    # 请求频率控制参数（模拟插件的自然行为）
    MIN_INTERVAL = 1.5    # 最小请求间隔（秒）
    MAX_INTERVAL = 3.5    # 最大请求间隔（秒）
    BATCH_PAUSE_MIN = 5   # 批量请求中每10个SKU后的暂停（秒）
    BATCH_PAUSE_MAX = 12  # 批量请求中每10个SKU后的暂停（秒）

    def __init__(self, token: Optional[str] = None):
        self.token = token
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0
        self._request_count_10s = 0
        self._request_count_1min = 0
        self._10s_start = time.time()
        self._1min_start = time.time()

    def _build_browser_headers(self) -> Dict[str, str]:
        """
        构建模拟Chrome浏览器+插件环境的请求头。
        
        BCS插件是作为Chrome Content Script运行在ozon.ru页面上的，
        它通过jQuery.ajax发起跨域请求到bcserp.com。
        
        Chrome会自动为跨域XHR添加以下Headers：
        - Origin: 发起请求的页面域名
        - Referer: 当前页面URL
        - sec-ch-ua: Chrome版本信息
        - sec-fetch-*: 请求上下文信息
        
        jQuery.ajax默认会添加：
        - X-Requested-With: XMLHttpRequest
        - Content-Type: application/json (对于POST请求)
        """
        return {
            # Chrome浏览器标识（与插件运行环境一致）
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            # 跨域请求的来源（插件从ozon.ru页面发起）
            "Origin": "https://www.ozon.ru",
            "Referer": "https://www.ozon.ru/",
            # jQuery.ajax默认添加的标识
            "X-Requested-With": "XMLHttpRequest",
            # Chrome的安全标识（sec-ch-ua）
            "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            # Chrome的Fetch Metadata（跨域XHR的标准值）
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            # 标准HTTP头
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ru;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

    async def _ensure_session(self):
        """创建模拟Chrome插件环境的HTTP会话"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._build_browser_headers(),
                # 模拟Chrome的cookie处理
                cookie_jar=aiohttp.CookieJar(unsafe=True),
            )

    async def _smart_delay(self):
        """
        智能请求延迟 - 模拟真实用户浏览商品时的自然节奏。
        
        BCS插件的实际行为：
        - 用户在OZON页面浏览商品时，每查看一个商品会触发一次API请求
        - 正常浏览速度约2~5秒看一个商品
        - 插件内部有10s和1min的请求计数器（仅用于日志，无限流）
        
        我们的策略：
        - 基础间隔1.5~3.5秒（模拟正常浏览速度）
        - 每10个请求后额外暂停5~12秒（模拟翻页或思考）
        - 每50个请求后暂停15~30秒（模拟休息或切换类目）
        """
        now = time.time()
        elapsed = now - self._last_request_time
        
        # 基础随机间隔
        delay = random.uniform(self.MIN_INTERVAL, self.MAX_INTERVAL)
        
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        
        self._last_request_time = time.time()
        
        # 更新请求计数器（模拟插件行为）
        self._request_count_10s += 1
        self._request_count_1min += 1
        
        # 重置10s计数器
        if time.time() - self._10s_start >= 10:
            self._request_count_10s = 0
            self._10s_start = time.time()
        
        # 重置1min计数器
        if time.time() - self._1min_start >= 60:
            self._request_count_1min = 0
            self._1min_start = time.time()

    async def close(self):
        """关闭HTTP会话"""
        if self._session and not self._session.closed:
            await self._session.close()

    # ==================== 认证（完全模拟插件流程） ====================

    async def login(self, username: str, password: str) -> bool:
        """
        登录BCS获取认证token。
        
        完全模拟插件的登录流程：
        1. POST /pluginLogin 获取token
        2. 登录成功后自动调用 GET /system/ozonShop/inspectCookie（与插件一致）
        
        Args:
            username: BCS账号用户名
            password: BCS账号密码

        Returns:
            登录是否成功
        """
        await self._ensure_session()
        
        try:
            # Step 1: 登录获取token（与插件完全一致）
            login_headers = {
                "Content-Type": "application/json",
            }
            
            async with self._session.post(
                f"{self.AUTH_URL}/pluginLogin",
                json={"username": username, "password": password},
                headers=login_headers,
            ) as resp:
                data = await resp.json()
                if data.get("token"):
                    self.token = data["token"]
                    logger.info("BCS登录成功，token已获取")
                    
                    # Step 2: 登录后立即调用inspectCookie（模拟插件行为）
                    # 插件在登录成功后会立即调用这个接口
                    await self._inspect_cookie()
                    
                    return True
                else:
                    msg = data.get("msg", "登录失败")
                    logger.error(f"BCS登录失败: {msg}")
                    return False
                    
        except Exception as e:
            logger.error(f"BCS登录请求出错: {e}")
            return False

    async def _inspect_cookie(self):
        """
        模拟插件登录后的inspectCookie调用。
        
        BCS插件在登录成功后会立即调用此接口，
        可能用于服务端记录登录状态或验证cookie。
        不调用此接口可能导致后续请求被标记为异常。
        """
        try:
            await asyncio.sleep(random.uniform(0.3, 0.8))  # 模拟自然延迟
            async with self._session.get(
                f"{self.BASE_URL}/system/ozonShop/inspectCookie",
                headers=self._get_auth_headers(),
            ) as resp:
                # 不关心返回值，只需要调用
                await resp.read()
                logger.debug("inspectCookie调用完成")
        except Exception as e:
            logger.debug(f"inspectCookie调用失败（不影响功能）: {e}")

    def set_token(self, token: str):
        """直接设置认证token（如果已有token可跳过login）"""
        self.token = token

    def _get_auth_headers(self) -> Dict[str, str]:
        """
        获取带认证的请求头。
        
        BCS插件的认证方式：
        - 直接在Authorization头中放置token值（不带Bearer前缀）
        - 这与标准的Bearer token不同，是BCS的自定义实现
        """
        headers = {}
        if self.token:
            # 插件直接使用 Authorization: <token>，不带Bearer前缀
            headers["Authorization"] = self.token
        return headers

    # ==================== 销量数据 ====================

    async def get_sales_data(self, sku: str, period: Optional[str] = None) -> Optional[Dict]:
        """
        获取商品销量数据。
        
        模拟插件的getSkuData函数行为：
        - GET /system/sku/skuss/new?sku=<SKU>
        - 可选参数 &period=weekly 获取周数据
        - 请求头只有 Authorization

        Args:
            sku: 商品SKU
            period: 时间周期，None表示月度数据，"weekly"表示周度数据

        Returns:
            包含销量等数据的字典，失败返回None
        """
        if not self.token:
            logger.warning("BCS未登录，无法获取销量数据")
            return None

        await self._ensure_session()
        await self._smart_delay()

        url = f"{self.BASE_URL}/system/sku/skuss/new?sku={sku}"
        if period:
            url += f"&period={period}"

        try:
            async with self._session.get(
                url, 
                headers=self._get_auth_headers(),
            ) as resp:
                data = await resp.json()

                code = data.get("code")
                if code == 401:
                    logger.error("BCS token已过期(401)，请重新登录")
                    self.token = None
                    return None
                elif code == 403:
                    logger.error("BCS访问被拒绝(403)，账号可能被限制")
                    return None
                elif code == 200:
                    return data.get("data")
                else:
                    logger.debug(f"BCS返回: code={code}, msg={data.get('msg')}")
                    return data.get("data")

        except Exception as e:
            logger.error(f"获取SKU {sku} 销量数据出错: {e}")
            return None

    async def get_weekly_sales(self, sku: str) -> Optional[str]:
        """获取商品周销量"""
        data = await self.get_sales_data(sku, period="weekly")
        if data and data.get("monthsales"):
            return data["monthsales"]
        return None

    async def get_monthly_sales(self, sku: str) -> Optional[str]:
        """获取商品月销量"""
        data = await self.get_sales_data(sku)
        if data and data.get("monthsales"):
            return data["monthsales"]
        return None

    async def get_full_sales_info(self, sku: str) -> Dict:
        """
        获取商品的完整销量和运营数据（月度+周度）。
        
        模拟插件的processSalesData行为：
        先获取月度数据，如果有数据再获取周度数据。
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

            # 如果有月销量数据，再获取周销量（与插件行为一致）
            if monthly_data.get("monthsales"):
                weekly_data = await self.get_sales_data(sku, period="weekly")
                if weekly_data and weekly_data.get("monthsales"):
                    result["weekly_sales"] = str(weekly_data["monthsales"])

        return result

    # ==================== 重量尺寸数据 ====================

    async def get_weight_data(self, sku: str) -> Optional[Dict]:
        """
        获取商品的重量和尺寸数据。
        
        模拟插件的getSkuPackaging函数：
        - POST /system/ozonRecord/shops
        - Body: {"sku": "<SKU>"}
        - Content-Type: application/json; charset=utf-8
        - 超时8秒（与插件一致）
        """
        if not self.token:
            logger.warning("BCS未登录，无法获取重量数据")
            return None

        await self._ensure_session()
        await self._smart_delay()

        try:
            post_headers = self._get_auth_headers()
            post_headers["Content-Type"] = "application/json; charset=utf-8"
            
            async with self._session.post(
                f"{self.BASE_URL}/system/ozonRecord/shops",
                json={"sku": sku},
                headers=post_headers,
                timeout=aiohttp.ClientTimeout(total=8),  # 与插件超时一致
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

        except asyncio.TimeoutError:
            logger.debug(f"SKU {sku} 重量数据请求超时（8s）")
        except Exception as e:
            logger.error(f"获取SKU {sku} 重量数据出错: {e}")

        return None

    # ==================== 批量获取（模拟自然浏览行为） ====================

    async def batch_get_sales_data(
        self,
        sku_list: List[str],
        include_weekly: bool = True,
        on_progress: Optional[callable] = None,
    ) -> List[Dict]:
        """
        批量获取商品销量数据。
        
        模拟用户在OZON页面逐个浏览商品的行为：
        - 每个SKU之间有随机间隔（1.5~3.5秒）
        - 每10个SKU后有较长暂停（5~12秒，模拟翻页）
        - 每50个SKU后有更长暂停（15~30秒，模拟休息）
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

            # 模拟自然浏览节奏
            if i > 0 and (i + 1) % 50 == 0:
                # 每50个SKU暂停较长时间（模拟休息/切换类目）
                pause = random.uniform(15, 30)
                logger.info(f"批量请求暂停 {pause:.1f}s（已处理{i+1}个）")
                await asyncio.sleep(pause)
            elif i > 0 and (i + 1) % 10 == 0:
                # 每10个SKU暂停（模拟翻页）
                pause = random.uniform(self.BATCH_PAUSE_MIN, self.BATCH_PAUSE_MAX)
                logger.info(f"翻页暂停 {pause:.1f}s（已处理{i+1}个）")
                await asyncio.sleep(pause)

        logger.info(f"批量销量数据获取完成: {len(results)}/{total}")
        return results

    async def batch_get_weight_data(
        self,
        sku_list: List[str],
        on_progress: Optional[callable] = None,
    ) -> List[Dict]:
        """批量获取商品重量尺寸数据"""
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

            # 模拟自然浏览节奏
            if i > 0 and (i + 1) % 10 == 0:
                pause = random.uniform(self.BATCH_PAUSE_MIN, self.BATCH_PAUSE_MAX)
                await asyncio.sleep(pause)

        logger.info(f"批量重量数据获取完成: {len(results)}/{total}")
        return results

    async def batch_get_all_data(
        self,
        sku_list: List[str],
        on_progress: Optional[callable] = None,
    ) -> List[Dict]:
        """
        批量获取商品的完整数据（销量 + 重量尺寸）。
        
        注意：每个SKU会发起2~3个请求（月销量+周销量+重量），
        所以实际请求量是SKU数量的2~3倍。
        """
        results = []
        total = len(sku_list)

        for i, sku in enumerate(sku_list):
            logger.info(f"获取完整数据 [{i+1}/{total}]: SKU={sku}")

            # 先获取销量数据
            sales_data = await self.get_full_sales_info(sku)
            
            # 再获取重量数据（模拟插件的顺序调用）
            weight_data = await self.get_weight_data(sku)

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

            # 模拟自然浏览节奏
            if i > 0 and (i + 1) % 50 == 0:
                pause = random.uniform(15, 30)
                logger.info(f"批量请求暂停 {pause:.1f}s（已处理{i+1}个）")
                await asyncio.sleep(pause)
            elif i > 0 and (i + 1) % 10 == 0:
                pause = random.uniform(self.BATCH_PAUSE_MIN, self.BATCH_PAUSE_MAX)
                logger.info(f"翻页暂停 {pause:.1f}s（已处理{i+1}个）")
                await asyncio.sleep(pause)

        logger.info(f"批量完整数据获取完成: {len(results)}/{total}")
        return results
