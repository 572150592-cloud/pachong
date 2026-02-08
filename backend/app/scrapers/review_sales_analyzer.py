"""
OZON 评论时间戳销量分析器 v2.0
================================
通过OZON评论API获取评论时间戳，判断商品近7天内有无销售，
并基于评论数和留评率估算周/月销量。

核心原理（已验证）：
- OZON的评论API（entrypoint-api）返回每条评论的精确Unix时间戳（createdAt字段）
- 通过按时间倒序获取评论，统计7天/30天内的新评论数
- 乘以留评率系数（20~50倍），估算周/月销量
- 支持翻页：每页30条，通过paging.nextButton中的page_key翻页

已验证的API端点：
    GET /api/entrypoint-api.bx/page/json/v2?url=/reviews/{sku}?sort=date_desc
    
返回数据结构：
    widgetStates -> webListReviews-xxx -> {
        reviews: [{createdAt: 1738998240, score: 5, ...}, ...],
        paging: {total: 2704, page: 1, perPage: 30, nextButton: "?page=2&page_key=xxx&sort=published_at_desc"}
    }

翻页方式：
    GET /api/entrypoint-api.bx/page/json/v2?url=/reviews/{sku}{paging.nextButton}

实测数据（iPhone 15, SKU 1681720585, 2026-02-08）：
    - 第1页(30条): 2月4日~2月8日（4天）
    - 第2页(30条): 1月27日~2月3日（7天）
    - 第3页(30条): 1月17日~1月27日（10天）
    - 近7天评论42条 → 估算周销量1400单（3%留评率）
    - 近30天评论90条 → 估算月销量3000单（3%留评率）
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
from playwright.async_api import Page, BrowserContext

logger = logging.getLogger(__name__)


class ReviewSalesAnalyzer:
    """
    通过评论时间戳分析商品销售活跃度（v2.0 - 支持翻页）
    
    使用方法：
    1. 传入已登录的Playwright BrowserContext（必须有ozon.ru页面打开）
    2. 调用 analyze_product(sku) 获取单个商品的销售分析
    3. 调用 batch_analyze(sku_list) 批量分析
    
    关键参数：
    - review_rate: 留评率，默认0.03（3%），即每100个购买者约3人留评
    - max_pages: 最大翻页数，默认5页（150条评论），足够覆盖30天
    """

    # 留评率参考值
    REVIEW_RATE_LOW = 0.02    # 保守（高单价商品）
    REVIEW_RATE_MID = 0.03    # 中等（大多数品类）
    REVIEW_RATE_HIGH = 0.05   # 激进（低单价快消品）

    REVIEWS_PER_PAGE = 30

    def __init__(self, context: BrowserContext, review_rate: float = 0.03, max_pages: int = 5):
        self.context = context
        self.review_rate = review_rate
        self.max_pages = max_pages
        self._ozon_page = None

    async def _get_ozon_page(self) -> Optional[Page]:
        """获取已打开的OZON页面"""
        if self._ozon_page and not self._ozon_page.is_closed():
            return self._ozon_page
        
        for page in self.context.pages:
            if 'ozon.ru' in page.url:
                self._ozon_page = page
                return page
        
        # 如果没有已打开的OZON页面，新建一个
        page = await self.context.new_page()
        await page.goto("https://www.ozon.ru/", wait_until="domcontentloaded")
        await asyncio.sleep(5)
        self._ozon_page = page
        return page

    async def analyze_product(self, sku: str, days: int = 7) -> Dict:
        """
        分析单个商品的销售活跃度
        
        通过评论API获取最近的评论列表（支持翻页），统计指定天数内的新评论数，
        并基于留评率估算销量。
        
        Args:
            sku: 商品SKU
            days: 分析的天数范围（默认7天）
            
        Returns:
            分析结果字典
        """
        result = {
            "sku": sku,
            "analysis_date": datetime.now(timezone.utc).isoformat(),
            "period_days": days,
            "has_sales_in_period": False,
            "reviews_in_7d": 0,
            "reviews_in_30d": 0,
            "total_reviews": 0,
            "total_reviews_fetched": 0,
            "estimated_weekly_sales": 0,
            "estimated_monthly_sales": 0,
            "estimated_sales_range": {"low": 0, "mid": 0, "high": 0},
            "latest_review_date": None,
            "days_since_last_review": None,
            "pages_fetched": 0,
            "confidence": "none",
            "data_source": "ozon_review_api",
        }

        try:
            page = await self._get_ozon_page()
            if not page:
                logger.error(f"无法获取OZON页面")
                return result

            # 获取评论数据（支持翻页）
            reviews_data = await self._fetch_reviews_with_paging(page, sku)
            
            all_timestamps = reviews_data.get("timestamps", [])
            result["total_reviews"] = reviews_data.get("total_reviews", 0)
            result["total_reviews_fetched"] = len(all_timestamps)
            result["pages_fetched"] = reviews_data.get("pages_fetched", 0)

            if not all_timestamps:
                logger.info(f"SKU {sku}: 未获取到评论时间戳")
                return result

            # 分析时间戳
            now = datetime.now(timezone.utc)
            cutoff_7d = now - timedelta(days=7)
            cutoff_30d = now - timedelta(days=30)
            cutoff_custom = now - timedelta(days=days)

            reviews_7d = 0
            reviews_30d = 0
            reviews_custom = 0
            latest_ts = None

            for ts in all_timestamps:
                try:
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    if latest_ts is None or ts > latest_ts:
                        latest_ts = ts
                    if dt >= cutoff_7d:
                        reviews_7d += 1
                    if dt >= cutoff_30d:
                        reviews_30d += 1
                    if dt >= cutoff_custom:
                        reviews_custom += 1
                except (ValueError, OSError):
                    continue

            result["reviews_in_7d"] = reviews_7d
            result["reviews_in_30d"] = reviews_30d

            # 最新评论日期
            if latest_ts:
                latest_dt = datetime.fromtimestamp(latest_ts, tz=timezone.utc)
                result["latest_review_date"] = latest_dt.isoformat()
                result["days_since_last_review"] = round(
                    (now - latest_dt).total_seconds() / 86400, 1
                )

            # 判断是否有销售
            result["has_sales_in_period"] = reviews_custom > 0

            # 估算销量
            if reviews_7d > 0:
                result["estimated_weekly_sales"] = int(reviews_7d / self.review_rate)
                result["estimated_sales_range"] = {
                    "low": int(reviews_7d / self.REVIEW_RATE_HIGH),
                    "mid": int(reviews_7d / self.REVIEW_RATE_MID),
                    "high": int(reviews_7d / self.REVIEW_RATE_LOW),
                }

            if reviews_30d > 0:
                result["estimated_monthly_sales"] = int(reviews_30d / self.review_rate)
            elif reviews_7d > 0:
                result["estimated_monthly_sales"] = int(
                    result["estimated_weekly_sales"] * 4.3
                )

            # 评估置信度
            result["confidence"] = self._assess_confidence(reviews_7d, reviews_30d)

            logger.info(
                f"SKU {sku}: 7d评论={reviews_7d}, 30d评论={reviews_30d}, "
                f"估算周销量={result['estimated_weekly_sales']}, "
                f"估算月销量={result['estimated_monthly_sales']}, "
                f"置信度={result['confidence']}"
            )

        except Exception as e:
            logger.error(f"分析SKU {sku} 失败: {e}", exc_info=True)
            result["error"] = str(e)

        return result

    async def _fetch_reviews_with_paging(self, page: Page, sku: str) -> Dict:
        """
        通过OZON评论API获取评论时间戳（支持翻页）
        
        翻页策略：
        - 每页30条评论，按时间倒序排列
        - 使用paging.nextButton中的page_key进行翻页
        - 最多翻max_pages页（默认5页=150条）
        - 如果评论时间已超过30天前，提前停止翻页
        
        Args:
            page: OZON页面
            sku: 商品SKU
            
        Returns:
            {timestamps: [int], total_reviews: int, pages_fetched: int}
        """
        js_code = """
        async ({sku, maxPages}) => {
            const result = {timestamps: [], totalReviews: 0, pagesFetched: 0, errors: []};
            
            try {
                let nextPath = '/reviews/' + sku + '?sort=date_desc';
                
                for (let pageNum = 1; pageNum <= maxPages; pageNum++) {
                    const url = '/api/entrypoint-api.bx/page/json/v2?url=' + 
                        encodeURIComponent(nextPath);
                    
                    const resp = await fetch(url, {credentials: 'include'});
                    if (!resp.ok) {
                        result.errors.push('Page ' + pageNum + ': HTTP ' + resp.status);
                        break;
                    }
                    
                    const data = await resp.json();
                    let foundReviews = false;
                    let nextButton = null;
                    
                    for (const [key, val] of Object.entries(data.widgetStates || {})) {
                        if (!key.includes('webListReviews')) continue;
                        
                        let parsed;
                        try {
                            parsed = typeof val === 'string' ? JSON.parse(val) : val;
                        } catch(e) { continue; }
                        
                        // 获取总评论数
                        if (parsed.paging && parsed.paging.total) {
                            result.totalReviews = parsed.paging.total;
                        }
                        
                        // 获取下一页URL
                        if (parsed.paging && parsed.paging.nextButton) {
                            nextButton = parsed.paging.nextButton;
                        }
                        
                        // 提取评论时间戳
                        const reviews = parsed.reviews || [];
                        for (const review of reviews) {
                            if (review.createdAt) {
                                result.timestamps.push(review.createdAt);
                                foundReviews = true;
                            }
                        }
                    }
                    
                    result.pagesFetched = pageNum;
                    
                    if (!foundReviews) break;
                    
                    // 检查最后一条评论是否已超过30天
                    if (result.timestamps.length > 0) {
                        const oldestTs = result.timestamps[result.timestamps.length - 1];
                        const thirtyDaysAgo = Date.now() / 1000 - 30 * 86400;
                        if (oldestTs < thirtyDaysAgo) {
                            break;  // 已经获取到30天前的评论，停止翻页
                        }
                    }
                    
                    // 翻页
                    if (!nextButton || pageNum >= maxPages) break;
                    nextPath = '/reviews/' + sku + nextButton;
                    
                    // 翻页间隔
                    await new Promise(r => setTimeout(r, 500));
                }
            } catch(e) {
                result.errors.push(e.message);
            }
            
            return result;
        }
        """

        try:
            data = await page.evaluate(
                js_code, {"sku": sku, "maxPages": self.max_pages}
            )
            
            if data.get("errors"):
                for err in data["errors"]:
                    logger.warning(f"SKU {sku} 评论获取警告: {err}")
            
            return {
                "timestamps": data.get("timestamps", []),
                "total_reviews": data.get("totalReviews", 0),
                "pages_fetched": data.get("pagesFetched", 0),
            }
        except Exception as e:
            logger.error(f"获取SKU {sku} 评论失败: {e}")
            return {"timestamps": [], "total_reviews": 0, "pages_fetched": 0}

    def _assess_confidence(self, reviews_7d: int, reviews_30d: int) -> str:
        """评估销量估算的置信度"""
        if reviews_7d >= 10:
            return "high"       # 7天内10+条评论
        elif reviews_7d >= 3:
            return "medium"     # 7天内3~9条评论
        elif reviews_7d >= 1:
            return "low"        # 7天内1~2条评论
        elif reviews_30d >= 1:
            return "very_low"   # 7天内无评论但30天内有
        else:
            return "none"       # 无评论

    async def batch_analyze(
        self,
        sku_list: List[str],
        days: int = 7,
        delay: float = 2.0,
        on_progress: Optional[callable] = None,
    ) -> List[Dict]:
        """
        批量分析多个商品的销售活跃度
        
        Args:
            sku_list: SKU列表
            days: 分析天数范围
            delay: 每个商品之间的延迟（秒）
            on_progress: 进度回调
            
        Returns:
            分析结果列表
        """
        results = []
        total = len(sku_list)

        for i, sku in enumerate(sku_list):
            logger.info(f"分析商品 [{i+1}/{total}]: SKU={sku}")
            
            result = await self.analyze_product(sku, days)
            results.append(result)

            if on_progress:
                on_progress({
                    "current": i + 1,
                    "total": total,
                    "sku": sku,
                    "has_sales": result.get("has_sales_in_period", False),
                    "weekly_sales": result.get("estimated_weekly_sales", 0),
                })

            if i < total - 1:
                await asyncio.sleep(delay)

        active_count = sum(1 for r in results if r.get("has_sales_in_period"))
        logger.info(
            f"批量分析完成: {total}个商品中 {active_count}个近{days}天有销售"
        )

        return results

    def get_sales_summary(self, results: List[Dict]) -> Dict:
        """生成批量分析的汇总报告"""
        total = len(results)
        active = [r for r in results if r.get("has_sales_in_period")]
        
        total_weekly = sum(r.get("estimated_weekly_sales", 0) for r in results)
        total_monthly = sum(r.get("estimated_monthly_sales", 0) for r in results)

        return {
            "total_products": total,
            "active_products": len(active),
            "inactive_products": total - len(active),
            "active_rate": f"{len(active)/total*100:.1f}%" if total > 0 else "0%",
            "total_estimated_weekly_sales": total_weekly,
            "total_estimated_monthly_sales": total_monthly,
            "avg_weekly_sales": round(total_weekly / total, 1) if total > 0 else 0,
            "top_sellers": sorted(
                active,
                key=lambda x: x.get("estimated_weekly_sales", 0),
                reverse=True
            )[:20],
        }


class OzonNativeSalesDetector:
    """
    OZON原生数据销售活跃度检测器
    
    结合多个OZON前端可获取的信号判断商品近7天是否有销售：
    
    信号源及权重：
    1. 评论时间戳（40分）— 最可靠，直接证明有购买
    2. 库存变化freeRest（30分）— 需要历史数据对比
    3. 搜索排名位置（15分）— 排名靠前说明活跃
    4. 配送速度标记（10分）— 支持次日达说明有FBO库存
    5. 促销/推广标记（5分）— 有推广说明卖家在投入
    
    评分标准：
    - >= 40分：确定有销售
    - 20~39分：可能有销售
    - 10~19分：不确定
    - < 10分：可能无销售
    """

    SIGNAL_WEIGHTS = {
        "review_timestamp": 40,
        "stock_change": 30,
        "search_rank": 15,
        "delivery_speed": 10,
        "promotion_badge": 5,
    }

    def calculate_activity_score(
        self,
        reviews_in_7d: int = 0,
        stock_decreased: bool = False,
        stock_decrease_amount: int = 0,
        search_rank: int = 999,
        delivery_tomorrow: bool = False,
        has_promotion: bool = False,
        stock_quantity: int = None,
        total_reviews: int = 0,
    ) -> Dict:
        """
        计算商品的销售活跃度评分
        
        Returns:
            活跃度评分和判断结果
        """
        score = 0
        signals = {}

        # 信号1：评论时间戳
        if reviews_in_7d > 0:
            review_score = min(self.SIGNAL_WEIGHTS["review_timestamp"], 
                             reviews_in_7d * 4)
            score += review_score
            signals["review_timestamp"] = {
                "score": review_score,
                "max": self.SIGNAL_WEIGHTS["review_timestamp"],
                "detail": f"近7天{reviews_in_7d}条新评论",
                "is_definitive": True,
            }

        # 信号2：库存变化
        if stock_decreased:
            stock_score = self.SIGNAL_WEIGHTS["stock_change"]
            score += stock_score
            signals["stock_change"] = {
                "score": stock_score,
                "max": self.SIGNAL_WEIGHTS["stock_change"],
                "detail": f"库存减少{stock_decrease_amount}件",
                "is_definitive": True,
            }

        # 信号3：搜索排名
        if search_rank <= 36:
            if search_rank <= 12:
                rank_score = 15
            elif search_rank <= 24:
                rank_score = 10
            else:
                rank_score = 5
            score += rank_score
            signals["search_rank"] = {
                "score": rank_score,
                "max": self.SIGNAL_WEIGHTS["search_rank"],
                "detail": f"搜索排名第{search_rank}位",
                "is_definitive": False,
            }

        # 信号4：配送速度
        if delivery_tomorrow:
            score += self.SIGNAL_WEIGHTS["delivery_speed"]
            signals["delivery_speed"] = {
                "score": self.SIGNAL_WEIGHTS["delivery_speed"],
                "max": self.SIGNAL_WEIGHTS["delivery_speed"],
                "detail": "支持明天送达（FBO库存）",
                "is_definitive": False,
            }

        # 信号5：促销标记
        if has_promotion:
            score += self.SIGNAL_WEIGHTS["promotion_badge"]
            signals["promotion_badge"] = {
                "score": self.SIGNAL_WEIGHTS["promotion_badge"],
                "max": self.SIGNAL_WEIGHTS["promotion_badge"],
                "detail": "有促销/推广标记",
                "is_definitive": False,
            }

        # 判断结论
        if score >= 40:
            verdict = "active"
            verdict_text = "近7天确定有销售"
        elif score >= 20:
            verdict = "likely_active"
            verdict_text = "近7天可能有销售"
        elif score >= 10:
            verdict = "uncertain"
            verdict_text = "无法确定是否有销售"
        else:
            verdict = "likely_inactive"
            verdict_text = "近7天可能无销售"

        return {
            "activity_score": score,
            "max_score": 100,
            "verdict": verdict,
            "verdict_text": verdict_text,
            "signals": signals,
            "stock_quantity": stock_quantity,
            "total_reviews": total_reviews,
        }


# ==================== 便捷函数 ====================

async def quick_check_sales(context: BrowserContext, sku: str) -> Dict:
    """
    快速检查单个商品近7天是否有销售
    
    用法：
        from playwright.async_api import async_playwright
        
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            ctx = browser.contexts[0]
            result = await quick_check_sales(ctx, "1681720585")
            print(f"有销售: {result['has_sales_in_period']}")
            print(f"周销量: {result['estimated_weekly_sales']}")
    """
    analyzer = ReviewSalesAnalyzer(context, max_pages=3)
    return await analyzer.analyze_product(sku)


async def batch_check_sales(
    context: BrowserContext, 
    sku_list: List[str],
    review_rate: float = 0.03,
    delay: float = 2.0,
) -> List[Dict]:
    """
    批量检查多个商品的销售活跃度
    
    用法：
        results = await batch_check_sales(ctx, ["1681720585", "1234567890"])
        for r in results:
            print(f"SKU {r['sku']}: 周销量={r['estimated_weekly_sales']}")
    """
    analyzer = ReviewSalesAnalyzer(context, review_rate=review_rate, max_pages=3)
    return await analyzer.batch_analyze(sku_list, delay=delay)
