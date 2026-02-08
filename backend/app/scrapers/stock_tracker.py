"""
OZON库存追踪与销量估算模块
============================
通过定期监控商品库存变化来估算周销量和月销量。

原理说明：
OZON商品页面在库存较少时会显示"Осталось X шт"（剩余X件），
通过定期记录这个数值的变化，可以推算出商品的实际销量。

对于不显示库存数量的商品，使用以下辅助方法估算：
1. 评论数推算法：通过评论数量和评论转化率（通常1-3%）来估算总销量
2. 加购按钮状态：通过"添加到购物车"按钮的可用性判断是否有库存
3. 搜索排名变化：排名上升通常意味着销量增加

数据存储：
- stock_snapshots表：记录每次库存快照
- 通过时间窗口内的库存变化计算周/月销量
"""

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Response

logger = logging.getLogger(__name__)


class StockTracker:
    """
    OZON库存追踪器
    
    通过以下方式获取库存/销量数据：
    1. 商品详情页的"Осталось X шт"提示
    2. composer-api中的库存相关字段
    3. 加购测试法（将商品加入购物车查看最大可购数量）
    4. 评论增长率推算
    """

    # 加购测试的最大数量
    MAX_CART_TEST_QTY = 999

    def __init__(
        self,
        headless: bool = True,
        proxy: Optional[Dict] = None,
    ):
        self.headless = headless
        self.proxy = proxy
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.playwright = None

    async def start(self):
        """启动浏览器"""
        self.playwright = await async_playwright().start()
        launch_args = {
            "headless": self.headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--lang=ru-RU,ru",
            ],
        }
        if self.proxy:
            launch_args["proxy"] = self.proxy

        self.browser = await self.playwright.chromium.launch(**launch_args)
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """)
        logger.info("StockTracker 浏览器启动成功")

    async def stop(self):
        """关闭浏览器"""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("StockTracker 浏览器已关闭")

    async def get_stock_info(self, sku: str) -> Dict:
        """
        获取单个商品的库存信息
        
        返回数据结构：
        {
            "sku": "12345",
            "stock_quantity": 15,          # 库存数量（如果页面显示）
            "stock_status": "in_stock",    # 库存状态：in_stock/low_stock/out_of_stock
            "stock_text": "Осталось 15 шт", # 原始库存文本
            "max_cart_quantity": 10,        # 最大可加购数量（通过加购测试）
            "review_count": 500,           # 评论数
            "orders_text": "",             # 订单相关文本（如果有）
            "estimated_total_sales": 0,    # 估算总销量
            "snapshot_time": "2025-01-01T00:00:00",
        }
        """
        page = await self.context.new_page()
        stock_info = {
            "sku": sku,
            "stock_quantity": None,
            "stock_status": "unknown",
            "stock_text": "",
            "max_cart_quantity": None,
            "review_count": 0,
            "orders_text": "",
            "estimated_total_sales": 0,
            "snapshot_time": datetime.now().isoformat(),
        }

        api_data_list = []

        async def handle_response(response: Response):
            try:
                url = response.url
                if "/api/composer-api.bx/page/json/v2" in url or "/api/entrypoint-api.bx/page/json/v2" in url:
                    if response.status == 200:
                        try:
                            body = await response.json()
                            api_data_list.append(body)
                        except Exception:
                            pass
            except Exception:
                pass

        page.on("response", handle_response)

        try:
            url = f"https://www.ozon.ru/product/{sku}/"
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(3, 5))

            # 滚动页面触发更多数据加载
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
            await asyncio.sleep(1.5)

            # === 方法1：从页面DOM提取库存信息 ===
            dom_stock = await self._extract_stock_from_dom(page)
            stock_info.update({k: v for k, v in dom_stock.items() if v is not None and v != ""})

            # === 方法2：从composer-api响应中提取 ===
            api_stock = self._extract_stock_from_api(api_data_list)
            # API数据补充（不覆盖已有的DOM数据）
            for k, v in api_stock.items():
                if (stock_info.get(k) is None or stock_info.get(k) == "" or stock_info.get(k) == 0) and v:
                    stock_info[k] = v

            # === 方法3：加购测试法（获取最大可购数量）===
            if stock_info["stock_quantity"] is None:
                max_qty = await self._test_cart_quantity(page, sku)
                if max_qty is not None:
                    stock_info["max_cart_quantity"] = max_qty
                    stock_info["stock_quantity"] = max_qty

            # 确定库存状态
            qty = stock_info.get("stock_quantity")
            if qty is not None:
                if qty == 0:
                    stock_info["stock_status"] = "out_of_stock"
                elif qty <= 10:
                    stock_info["stock_status"] = "low_stock"
                else:
                    stock_info["stock_status"] = "in_stock"

            # 估算总销量（基于评论数）
            review_count = stock_info.get("review_count", 0)
            if review_count > 0:
                # 通常评论率在1-3%之间，取2%作为中值
                stock_info["estimated_total_sales"] = int(review_count / 0.02)

        except Exception as e:
            logger.error(f"获取库存信息失败 (SKU: {sku}): {e}")
        finally:
            await page.close()

        return stock_info

    async def _extract_stock_from_dom(self, page: Page) -> Dict:
        """从页面DOM中提取库存相关信息"""
        try:
            return await page.evaluate("""
                () => {
                    const result = {
                        stock_quantity: null,
                        stock_text: "",
                        review_count: 0,
                        orders_text: "",
                    };
                    
                    const bodyText = document.body.innerText;
                    
                    // 1. 查找"Осталось X шт"（剩余X件）
                    const stockPatterns = [
                        /[Оо]сталось\s+(\d+)\s*шт/i,
                        /[Оо]сталось\s+(\d+)\s*товар/i,
                        /[Кк]оличество\s+ограничено/i,
                        /[Пп]оследн[ие][ей]\s+(\d+)/i,
                    ];
                    
                    for (const pattern of stockPatterns) {
                        const match = bodyText.match(pattern);
                        if (match) {
                            result.stock_text = match[0];
                            if (match[1]) {
                                result.stock_quantity = parseInt(match[1]);
                            }
                            break;
                        }
                    }
                    
                    // 2. 检查是否缺货
                    if (/[Нн]ет в наличии|[Зз]акончился|[Рр]аспродан/i.test(bodyText)) {
                        result.stock_quantity = 0;
                        result.stock_text = "Нет в наличии";
                    }
                    
                    // 3. 提取评论数
                    const reviewPatterns = [
                        /(\d[\d\s]*)\s*отзыв/i,
                        /(\d[\d\s]*)\s*оценк/i,
                    ];
                    for (const pattern of reviewPatterns) {
                        const match = bodyText.match(pattern);
                        if (match) {
                            result.review_count = parseInt(match[1].replace(/\s/g, ''));
                            break;
                        }
                    }
                    
                    // 4. 查找订单/购买数量相关文本
                    const orderPatterns = [
                        /(\d[\d\s]*)\s*(?:заказ|покуп|куплен|продан)/i,
                        /[Кк]упили\s+(\d[\d\s]*)\s*раз/i,
                        /(\d[\d\s]*)\s*(?:раз|человек)\s*(?:купили|заказали)/i,
                    ];
                    for (const pattern of orderPatterns) {
                        const match = bodyText.match(pattern);
                        if (match) {
                            result.orders_text = match[0];
                            break;
                        }
                    }
                    
                    // 5. 检查"热销"/"畅销"标记
                    if (/[Хх]ит\s*продаж|[Бб]естселлер|[Пп]опулярн/i.test(bodyText)) {
                        result.orders_text = result.orders_text || "Хит продаж";
                    }
                    
                    return result;
                }
            """)
        except Exception as e:
            logger.error(f"DOM库存提取出错: {e}")
            return {}

    def _extract_stock_from_api(self, api_data_list: List[Dict]) -> Dict:
        """从composer-api响应中提取库存相关数据"""
        result = {
            "stock_quantity": None,
            "review_count": 0,
            "orders_text": "",
        }

        for api_data in api_data_list:
            widget_states = api_data.get("widgetStates", {})

            for key, value_str in widget_states.items():
                try:
                    if isinstance(value_str, str):
                        value = json.loads(value_str)
                    else:
                        value = value_str
                except (json.JSONDecodeError, TypeError):
                    continue

                # 从webPrice/webSale widget中查找库存信息
                if "webPrice" in key or "webSale" in key:
                    # 有时库存信息在价格widget中
                    stock_text = str(value)
                    stock_match = re.search(r'[Оо]сталось\s+(\d+)', stock_text)
                    if stock_match:
                        result["stock_quantity"] = int(stock_match.group(1))

                    # 查找"已售出"信息
                    sold_match = re.search(r'(\d+)\s*(?:продан|куплен|заказ)', stock_text)
                    if sold_match:
                        result["orders_text"] = sold_match.group(0)

                # 从评论widget中获取评论数
                elif "webReviewProductScore" in key:
                    count = value.get("count", 0) or value.get("totalCount", 0)
                    if count:
                        result["review_count"] = int(count)

                # 查找addToCart widget中的库存限制
                elif "addToCart" in key.lower() or "webAddToCart" in key:
                    max_qty = value.get("maxQuantity", None) or value.get("limit", None)
                    if max_qty:
                        result["stock_quantity"] = int(max_qty)

                # 查找商品状态widget
                elif "webStatus" in key or "webAvailability" in key:
                    status = value.get("status", "") or value.get("state", "")
                    if "out_of_stock" in str(status).lower() or "unavailable" in str(status).lower():
                        result["stock_quantity"] = 0

        return result

    async def _test_cart_quantity(self, page: Page, sku: str) -> Optional[int]:
        """
        加购测试法：通过尝试将商品加入购物车来获取最大可购数量
        
        OZON在加购时会限制最大数量，这个限制值通常等于当前库存量。
        通过OZON的内部API可以直接获取这个限制值。
        """
        try:
            # 尝试通过API获取加购限制
            max_qty = await page.evaluate("""
                async (sku) => {
                    try {
                        // 尝试查找页面上的加购按钮相关数据
                        const addToCartBtn = document.querySelector(
                            '[data-widget="webAddToCart"] button, ' +
                            'button[class*="addToCart"], ' +
                            'button:has(span:contains("В корзину"))'
                        );
                        
                        // 查找页面中的quantity相关数据
                        const allText = document.body.innerText;
                        
                        // 查找数量选择器的最大值
                        const qtyInput = document.querySelector(
                            'input[type="number"][max], ' +
                            '[data-widget*="quantity"] input'
                        );
                        if (qtyInput) {
                            const max = qtyInput.getAttribute('max');
                            if (max) return parseInt(max);
                        }
                        
                        return null;
                    } catch (e) {
                        return null;
                    }
                }
            """, sku)

            return max_qty

        except Exception as e:
            logger.debug(f"加购测试失败 (SKU: {sku}): {e}")
            return None

    async def batch_get_stock(
        self,
        sku_list: List[str],
        delay_range: Tuple[float, float] = (2, 5),
    ) -> List[Dict]:
        """
        批量获取库存信息
        
        Args:
            sku_list: SKU列表
            delay_range: 请求间延迟范围（秒）
            
        Returns:
            库存信息列表
        """
        results = []
        total = len(sku_list)

        for i, sku in enumerate(sku_list):
            logger.info(f"获取库存信息 [{i+1}/{total}]: SKU={sku}")
            info = await self.get_stock_info(str(sku))
            results.append(info)

            if i < total - 1:
                await asyncio.sleep(random.uniform(*delay_range))

        return results


class SalesEstimator:
    """
    销量估算器
    
    基于库存快照数据计算周/月销量的估算值。
    
    估算方法：
    1. 库存差值法（最准确）：
       - 如果有多个时间点的库存快照，通过库存减少量直接计算销量
       - 需要考虑补货情况（库存突然增加）
       
    2. 评论增长法：
       - 通过评论数量的增长速度来推算销量
       - 评论率通常在1-3%之间
       
    3. 排名变化法：
       - 搜索排名上升通常意味着销量增加
       - 可以结合排名变化和评论数来综合估算
    """

    # 评论转化率假设值
    REVIEW_RATE_LOW = 0.01    # 1% - 保守估计
    REVIEW_RATE_MID = 0.02    # 2% - 中等估计
    REVIEW_RATE_HIGH = 0.03   # 3% - 乐观估计

    @staticmethod
    def estimate_from_stock_changes(
        snapshots: List[Dict],
        period_days: int = 7,
    ) -> Dict:
        """
        基于库存变化估算销量
        
        Args:
            snapshots: 库存快照列表，按时间排序
                       [{"stock_quantity": 100, "snapshot_time": "2025-01-01T00:00:00"}, ...]
            period_days: 统计周期（天）
            
        Returns:
            {
                "estimated_sales": 50,        # 估算销量
                "confidence": "high",          # 置信度
                "method": "stock_diff",        # 估算方法
                "data_points": 10,             # 数据点数量
                "period_days": 7,              # 统计周期
                "restock_detected": False,     # 是否检测到补货
            }
        """
        if not snapshots or len(snapshots) < 2:
            return {
                "estimated_sales": 0,
                "confidence": "none",
                "method": "insufficient_data",
                "data_points": len(snapshots),
                "period_days": period_days,
                "restock_detected": False,
            }

        # 按时间排序
        sorted_snapshots = sorted(
            snapshots,
            key=lambda x: x.get("snapshot_time", "")
        )

        # 过滤出指定周期内的快照
        now = datetime.now()
        cutoff = now - timedelta(days=period_days)
        period_snapshots = [
            s for s in sorted_snapshots
            if datetime.fromisoformat(s["snapshot_time"]) >= cutoff
        ]

        if len(period_snapshots) < 2:
            # 数据不足，使用所有可用数据并按比例缩放
            period_snapshots = sorted_snapshots[-10:]  # 取最近10个

        total_sales = 0
        restock_detected = False

        for i in range(1, len(period_snapshots)):
            prev_qty = period_snapshots[i - 1].get("stock_quantity", 0) or 0
            curr_qty = period_snapshots[i].get("stock_quantity", 0) or 0

            diff = prev_qty - curr_qty

            if diff > 0:
                # 库存减少 = 有销量
                total_sales += diff
            elif diff < -5:
                # 库存大幅增加 = 补货
                restock_detected = True
                # 补货后的库存不计入销量计算

        # 计算置信度
        data_points = len(period_snapshots)
        if data_points >= 10:
            confidence = "high"
        elif data_points >= 5:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "estimated_sales": total_sales,
            "confidence": confidence,
            "method": "stock_diff",
            "data_points": data_points,
            "period_days": period_days,
            "restock_detected": restock_detected,
        }

    @staticmethod
    def estimate_from_reviews(
        current_reviews: int,
        previous_reviews: int = 0,
        period_days: int = 30,
        review_rate: float = 0.02,
    ) -> Dict:
        """
        基于评论数量估算销量
        
        Args:
            current_reviews: 当前评论数
            previous_reviews: 之前的评论数（如果有历史数据）
            period_days: 统计周期
            review_rate: 评论转化率假设
            
        Returns:
            估算结果字典
        """
        if previous_reviews > 0:
            # 有历史数据，通过评论增长来估算
            review_growth = current_reviews - previous_reviews
            if review_growth > 0:
                estimated_sales = int(review_growth / review_rate)
                return {
                    "estimated_sales": estimated_sales,
                    "confidence": "medium",
                    "method": "review_growth",
                    "review_growth": review_growth,
                    "review_rate_assumed": review_rate,
                    "period_days": period_days,
                }

        # 没有历史数据，通过总评论数估算总销量，再按时间分配
        if current_reviews > 0:
            total_estimated = int(current_reviews / review_rate)
            # 假设商品平均上架180天，按比例计算周期销量
            avg_listing_days = 180
            daily_sales = total_estimated / avg_listing_days
            period_sales = int(daily_sales * period_days)

            return {
                "estimated_sales": period_sales,
                "estimated_total_sales": total_estimated,
                "confidence": "low",
                "method": "review_total_estimate",
                "review_count": current_reviews,
                "review_rate_assumed": review_rate,
                "period_days": period_days,
            }

        return {
            "estimated_sales": 0,
            "confidence": "none",
            "method": "no_data",
            "period_days": period_days,
        }

    @staticmethod
    def calculate_weekly_monthly_sales(
        snapshots: List[Dict],
        current_reviews: int = 0,
        previous_reviews: int = 0,
    ) -> Dict:
        """
        综合计算周销量和月销量
        
        优先使用库存变化法，不足时用评论法补充
        
        Returns:
            {
                "weekly_sales": 50,
                "monthly_sales": 200,
                "weekly_method": "stock_diff",
                "monthly_method": "stock_diff",
                "weekly_confidence": "high",
                "monthly_confidence": "medium",
            }
        """
        result = {
            "weekly_sales": 0,
            "monthly_sales": 0,
            "weekly_method": "none",
            "monthly_method": "none",
            "weekly_confidence": "none",
            "monthly_confidence": "none",
        }

        # 尝试用库存变化法
        if snapshots and len(snapshots) >= 2:
            weekly = SalesEstimator.estimate_from_stock_changes(snapshots, period_days=7)
            monthly = SalesEstimator.estimate_from_stock_changes(snapshots, period_days=30)

            if weekly["confidence"] != "none":
                result["weekly_sales"] = weekly["estimated_sales"]
                result["weekly_method"] = weekly["method"]
                result["weekly_confidence"] = weekly["confidence"]

            if monthly["confidence"] != "none":
                result["monthly_sales"] = monthly["estimated_sales"]
                result["monthly_method"] = monthly["method"]
                result["monthly_confidence"] = monthly["confidence"]

        # 如果库存法数据不足，用评论法补充
        if result["weekly_confidence"] == "none" and current_reviews > 0:
            weekly_est = SalesEstimator.estimate_from_reviews(
                current_reviews, previous_reviews, period_days=7
            )
            result["weekly_sales"] = weekly_est["estimated_sales"]
            result["weekly_method"] = weekly_est["method"]
            result["weekly_confidence"] = weekly_est["confidence"]

        if result["monthly_confidence"] == "none" and current_reviews > 0:
            monthly_est = SalesEstimator.estimate_from_reviews(
                current_reviews, previous_reviews, period_days=30
            )
            result["monthly_sales"] = monthly_est["estimated_sales"]
            result["monthly_method"] = monthly_est["method"]
            result["monthly_confidence"] = monthly_est["confidence"]

        return result
