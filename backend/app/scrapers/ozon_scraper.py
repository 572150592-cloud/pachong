"""
OZON爬虫核心引擎
使用Playwright模拟浏览器，从OZON搜索页面采集商品数据。
支持无限滚动加载、网络请求拦截、数据解析。
"""
import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime
from typing import List, Dict, Optional, Callable
from urllib.parse import quote, urlencode

from playwright.async_api import async_playwright, Page, Browser, BrowserContext

logger = logging.getLogger(__name__)


class OzonScraper:
    """OZON商品数据爬虫引擎"""

    def __init__(
        self,
        headless: bool = True,
        proxy: Optional[Dict] = None,
        user_agent: Optional[str] = None,
        viewport_width: int = 1920,
        viewport_height: int = 1080,
        on_progress: Optional[Callable] = None,
    ):
        self.headless = headless
        self.proxy = proxy
        self.user_agent = user_agent or self._get_random_ua()
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.on_progress = on_progress

        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None

        # 数据收集
        self.products: List[Dict] = []
        self.seen_skus = set()
        self.intercepted_api_data = []

        # 状态
        self.is_running = False
        self.should_stop = False
        self.total_scraped = 0
        self.errors = []

    @staticmethod
    def _get_random_ua() -> str:
        """获取随机User-Agent"""
        uas = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        ]
        return random.choice(uas)

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
                "--disable-gpu",
                "--lang=zh-CN,zh",
            ],
        }
        if self.proxy:
            launch_args["proxy"] = self.proxy

        self.browser = await self.playwright.chromium.launch(**launch_args)

        self.context = await self.browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            user_agent=self.user_agent,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

        # 注入反检测脚本
        await self.context.add_init_script("""
            // 隐藏webdriver标识
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            // 修改navigator.plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            // 修改navigator.languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en']
            });
            // 覆盖chrome对象
            window.chrome = { runtime: {} };
        """)

        self.page = await self.context.new_page()
        self.page.set_default_timeout(60000)

        # 设置网络请求拦截
        await self._setup_request_interception()

        logger.info("浏览器启动成功")

    async def _setup_request_interception(self):
        """设置网络请求拦截，捕获OZON的API响应"""
        async def handle_response(response):
            try:
                url = response.url
                # 捕获OZON的搜索API响应
                if any(pattern in url for pattern in [
                    "/api/composer-api.bx/page/json/v2",
                    "/api/entrypoint-api.bx/page/json/v2",
                    "searchResultsV2",
                    "/api/composer-api.bx/_action/searchResultsV2",
                ]):
                    if response.status == 200:
                        try:
                            body = await response.json()
                            self.intercepted_api_data.append({
                                "url": url,
                                "data": body,
                                "timestamp": datetime.now().isoformat()
                            })
                            logger.debug(f"拦截到API响应: {url[:100]}...")
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"处理响应时出错: {e}")

        self.page.on("response", handle_response)

    async def stop(self):
        """关闭浏览器"""
        self.should_stop = True
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("浏览器已关闭")

    async def navigate_to_search(self, keyword: str, import_only: bool = False):
        """
        导航到OZON搜索页面
        
        Args:
            keyword: 搜索关键词
            import_only: 是否仅搜索进口商品
        """
        logger.info(f"开始搜索关键词: {keyword}")

        if import_only:
            # 先访问进口商品页面，再搜索
            url = f"https://www.ozon.ru/search/?text={quote(keyword)}&from_global=true"
        else:
            url = f"https://www.ozon.ru/search/?text={quote(keyword)}"

        await self.page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(2, 4))

        # 等待商品列表加载
        try:
            await self.page.wait_for_selector(
                '[data-widget="searchResultsV2"], [data-widget="searchResultsError"]',
                timeout=15000
            )
        except Exception:
            logger.warning("等待搜索结果超时，尝试继续...")

        # 关闭可能的弹窗
        await self._close_popups()

        logger.info(f"搜索页面加载完成: {keyword}")

    async def _close_popups(self):
        """关闭弹窗和Cookie提示"""
        try:
            # 关闭Cookie提示
            cookie_btn = await self.page.query_selector('button:has-text("好的")')
            if cookie_btn:
                await cookie_btn.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass

        try:
            # 关闭其他弹窗
            close_btns = await self.page.query_selector_all('[class*="modal"] button[class*="close"]')
            for btn in close_btns:
                try:
                    await btn.click()
                    await asyncio.sleep(0.3)
                except Exception:
                    pass
        except Exception:
            pass

    async def scrape_products(
        self,
        keyword: str,
        max_products: int = 5000,
        import_only: bool = False,
    ) -> List[Dict]:
        """
        采集商品数据的主函数
        
        Args:
            keyword: 搜索关键词
            max_products: 最大采集商品数
            import_only: 是否仅搜索进口商品
            
        Returns:
            商品数据列表
        """
        self.is_running = True
        self.should_stop = False
        self.products = []
        self.seen_skus = set()
        self.total_scraped = 0

        try:
            await self.navigate_to_search(keyword, import_only)

            # 持续滚动并采集数据
            no_new_data_count = 0
            max_no_new_data = 10  # 连续10次没有新数据则停止

            while self.total_scraped < max_products and not self.should_stop:
                # 从页面提取当前可见的商品数据
                prev_count = self.total_scraped
                new_products = await self._extract_products_from_page(keyword)

                if new_products:
                    self.products.extend(new_products)
                    self.total_scraped = len(self.products)
                    no_new_data_count = 0

                    if self.on_progress:
                        self.on_progress({
                            "keyword": keyword,
                            "scraped": self.total_scraped,
                            "target": max_products,
                            "status": "running"
                        })

                    logger.info(
                        f"[{keyword}] 已采集 {self.total_scraped}/{max_products} 件商品"
                    )
                else:
                    no_new_data_count += 1
                    if no_new_data_count >= max_no_new_data:
                        logger.info(f"[{keyword}] 连续{max_no_new_data}次无新数据，停止采集")
                        break

                # 滚动页面加载更多
                has_more = await self._scroll_page()
                if not has_more:
                    logger.info(f"[{keyword}] 页面已到底部，停止采集")
                    break

                # 随机延迟，模拟人类行为
                await asyncio.sleep(random.uniform(1.0, 2.5))

        except Exception as e:
            logger.error(f"采集过程出错: {e}", exc_info=True)
            self.errors.append(str(e))
        finally:
            self.is_running = False

        logger.info(f"[{keyword}] 采集完成，共 {len(self.products)} 件商品")
        return self.products

    async def _extract_products_from_page(self, keyword: str) -> List[Dict]:
        """从当前页面DOM中提取商品数据"""
        new_products = []

        try:
            # 使用JavaScript在页面中提取商品数据
            products_data = await self.page.evaluate("""
                () => {
                    const products = [];
                    
                    // 方法1: 从搜索结果的商品卡片中提取
                    const productCards = document.querySelectorAll(
                        '[data-widget="searchResultsV2"] [class*="tile-root"], ' +
                        '[data-widget="searchResultsV2"] [class*="tsBody500Medium"], ' +
                        'div[class*="widget-search-result"] a[href*="/product/"], ' +
                        'div[class*="search-page"] a[href*="/product/"]'
                    );
                    
                    // 方法2: 从所有商品链接中提取
                    const allProductLinks = document.querySelectorAll('a[href*="/product/"]');
                    const processedUrls = new Set();
                    
                    for (const link of allProductLinks) {
                        const href = link.getAttribute('href') || '';
                        if (!href.includes('/product/') || processedUrls.has(href)) continue;
                        processedUrls.add(href);
                        
                        // 提取SKU（从URL中）
                        const skuMatch = href.match(/-(\\d{5,})(?:\\/|\\?|$)/);
                        if (!skuMatch) continue;
                        const sku = skuMatch[1];
                        
                        // 找到商品卡片容器
                        let card = link.closest('[class*="tile"], [class*="card"], [class*="product"]');
                        if (!card) card = link.parentElement?.parentElement?.parentElement;
                        if (!card) continue;
                        
                        const cardText = card.innerText || '';
                        const cardHtml = card.innerHTML || '';
                        
                        // 提取标题
                        let title = '';
                        const titleEl = card.querySelector('span[class*="tsBody500Medium"], a[class*="tile-hover-target"]');
                        if (titleEl) {
                            title = titleEl.textContent?.trim() || '';
                        }
                        if (!title) {
                            title = link.textContent?.trim() || '';
                        }
                        
                        // 提取图片
                        let imageUrl = '';
                        const img = card.querySelector('img[src*="cdn"], img[src*="ozon"]');
                        if (img) {
                            imageUrl = img.src || img.getAttribute('srcset')?.split(' ')[0] || '';
                        }
                        
                        // 提取价格
                        let price = 0;
                        let originalPrice = 0;
                        let discount = 0;
                        
                        // 查找价格元素
                        const priceTexts = cardText.match(/([\\d\\s]+[,.]?\\d*)\\s*[₽¥]/g);
                        if (priceTexts && priceTexts.length > 0) {
                            const parsePrice = (text) => {
                                return parseFloat(text.replace(/[^\\d.,]/g, '').replace(/\\s/g, '').replace(',', '.')) || 0;
                            };
                            price = parsePrice(priceTexts[0]);
                            if (priceTexts.length > 1) {
                                originalPrice = parsePrice(priceTexts[1]);
                            }
                        }
                        
                        // 提取折扣
                        const discountMatch = cardText.match(/[−-](\\d+)%/);
                        if (discountMatch) {
                            discount = parseInt(discountMatch[1]);
                        }
                        
                        // 提取评分和评论数
                        let rating = 0;
                        let reviewCount = 0;
                        const ratingMatch = cardText.match(/(\\d+[.,]\\d+)\\s*[•·]?\\s*([\\d,]+)\\s*(?:评|отзыв|оценк)/i);
                        if (ratingMatch) {
                            rating = parseFloat(ratingMatch[1].replace(',', '.'));
                            reviewCount = parseInt(ratingMatch[2].replace(/[,\\s]/g, ''));
                        } else {
                            const simpleRating = cardText.match(/(\\d+[.,]\\d)\\s*(?:★|⭐)/);
                            if (simpleRating) {
                                rating = parseFloat(simpleRating[1].replace(',', '.'));
                            }
                        }
                        
                        // 提取品牌
                        let brand = '';
                        const brandEl = card.querySelector('[class*="brand"], [class*="tsBodyControl"]');
                        if (brandEl) {
                            brand = brandEl.textContent?.trim() || '';
                        }
                        
                        // 提取配送信息
                        let delivery = '';
                        const deliveryEl = card.querySelector('button[class*="delivery"], [class*="tsBodyControl400Small"]');
                        if (deliveryEl) {
                            delivery = deliveryEl.textContent?.trim() || '';
                        }
                        
                        // 检查是否Ozon自营
                        const isOzon = cardText.includes('Ozon') && 
                                      (cardText.includes('Express') || cardHtml.includes('ozon-badge'));
                        
                        products.push({
                            sku: sku,
                            title: title.substring(0, 500),
                            product_url: href.startsWith('http') ? href : 'https://www.ozon.ru' + href,
                            image_url: imageUrl,
                            price: price,
                            original_price: originalPrice,
                            discount_percent: discount,
                            brand: brand.substring(0, 255),
                            rating: rating,
                            review_count: reviewCount,
                            delivery_info: delivery,
                            seller_type: isOzon ? 'Ozon' : '',
                        });
                    }
                    
                    return products;
                }
            """)

            # 过滤已采集的SKU
            for product in products_data:
                sku = str(product.get("sku", ""))
                if sku and sku not in self.seen_skus:
                    self.seen_skus.add(sku)
                    product["keyword"] = keyword
                    product["scraped_at"] = datetime.now().isoformat()
                    new_products.append(product)

        except Exception as e:
            logger.error(f"提取商品数据出错: {e}")

        return new_products

    async def _scroll_page(self) -> bool:
        """
        向下滚动页面以加载更多商品
        
        Returns:
            是否还有更多内容可加载
        """
        try:
            # 获取当前滚动位置
            prev_height = await self.page.evaluate("document.body.scrollHeight")

            # 平滑滚动
            await self.page.evaluate("""
                () => {
                    return new Promise((resolve) => {
                        const distance = window.innerHeight * 0.8;
                        const scrollStep = distance / 10;
                        let scrolled = 0;
                        const timer = setInterval(() => {
                            window.scrollBy(0, scrollStep);
                            scrolled += scrollStep;
                            if (scrolled >= distance) {
                                clearInterval(timer);
                                resolve();
                            }
                        }, 50);
                    });
                }
            """)

            # 等待新内容加载
            await asyncio.sleep(random.uniform(1.5, 3.0))

            # 检查是否有新内容
            new_height = await self.page.evaluate("document.body.scrollHeight")

            # 检查是否有"加载更多"按钮
            try:
                load_more = await self.page.query_selector(
                    'button:has-text("Показать ещё"), button:has-text("显示更多"), '
                    'button:has-text("加载更多"), div[class*="paginator"] button'
                )
                if load_more:
                    await load_more.click()
                    await asyncio.sleep(random.uniform(2, 4))
                    return True
            except Exception:
                pass

            return new_height > prev_height

        except Exception as e:
            logger.error(f"滚动页面出错: {e}")
            return False

    async def get_product_detail(self, sku: str) -> Optional[Dict]:
        """
        获取单个商品的详细信息（进入商品详情页）
        
        Args:
            sku: 商品SKU
            
        Returns:
            商品详细数据
        """
        detail_url = f"https://www.ozon.ru/product/{sku}/"
        try:
            detail_page = await self.context.new_page()
            await detail_page.goto(detail_url, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2, 4))

            detail = await detail_page.evaluate("""
                () => {
                    const data = {};
                    const text = document.body.innerText;
                    
                    // 提取货号
                    const skuMatch = text.match(/货号[：:]\\s*(\\d+)/);
                    if (skuMatch) data.sku = skuMatch[1];
                    
                    // 提取类目（面包屑导航）
                    const breadcrumbs = document.querySelectorAll('ol[class*="breadcrumb"] a, nav a[href*="/category/"]');
                    if (breadcrumbs.length > 0) {
                        data.category = Array.from(breadcrumbs).map(a => a.textContent.trim()).join(' > ');
                    }
                    
                    // 提取卖家信息
                    const sellerEl = document.querySelector('[data-widget="webCurrentSeller"] a, [class*="seller"] a');
                    if (sellerEl) {
                        data.seller_name = sellerEl.textContent.trim();
                    }
                    
                    // 提取跟卖信息
                    const followMatch = text.match(/来自其他卖家[\\s\\S]*?从\\s*([\\d,.]+)\\s*[₽¥]\\s*(\\d+)/);
                    if (followMatch) {
                        data.follower_min_price = parseFloat(followMatch[1].replace(/[,\\s]/g, ''));
                        data.followers_count = parseInt(followMatch[2]);
                    }
                    
                    // 提取特征信息
                    const specs = {};
                    const specRows = document.querySelectorAll('[data-widget="webCharacteristics"] dl, [class*="characteristics"] tr');
                    specRows.forEach(row => {
                        const key = row.querySelector('dt, td:first-child');
                        const value = row.querySelector('dd, td:last-child');
                        if (key && value) {
                            specs[key.textContent.trim()] = value.textContent.trim();
                        }
                    });
                    
                    // 从特征中提取重量
                    for (const [key, value] of Object.entries(specs)) {
                        if (key.includes('重量') || key.includes('вес')) {
                            const weightMatch = value.match(/(\\d+[.,]?\\d*)/);
                            if (weightMatch) data.weight_g = parseFloat(weightMatch[1].replace(',', '.'));
                        }
                    }
                    
                    data.specs = specs;
                    return data;
                }
            """)

            await detail_page.close()
            return detail

        except Exception as e:
            logger.error(f"获取商品详情出错 (SKU: {sku}): {e}")
            return None

    def cancel(self):
        """取消当前采集任务"""
        self.should_stop = True
        logger.info("采集任务已取消")


class OzonScraperManager:
    """
    OZON爬虫管理器
    管理多个关键词的采集任务，支持定时/定量切换关键词
    """

    def __init__(self, headless: bool = True, proxy: Optional[Dict] = None):
        self.headless = headless
        self.proxy = proxy
        self.scraper: Optional[OzonScraper] = None
        self.all_products: List[Dict] = []
        self.is_running = False
        self.current_keyword = ""
        self.progress_callback: Optional[Callable] = None

    async def scrape_keywords(
        self,
        keywords: List[str],
        max_products_per_keyword: int = 5000,
        switch_mode: str = "sequential",
        switch_interval_minutes: int = 30,
        switch_quantity: int = 1000,
        import_only: bool = False,
        on_progress: Optional[Callable] = None,
    ) -> List[Dict]:
        """
        按照关键词列表进行采集
        
        Args:
            keywords: 关键词列表
            max_products_per_keyword: 每个关键词最大采集数
            switch_mode: 切换模式 (sequential/timer/quantity)
            switch_interval_minutes: 定时切换间隔（分钟）
            switch_quantity: 定量切换阈值
            import_only: 是否仅搜索进口商品
            on_progress: 进度回调函数
        """
        self.is_running = True
        self.all_products = []
        self.progress_callback = on_progress

        self.scraper = OzonScraper(
            headless=self.headless,
            proxy=self.proxy,
            on_progress=on_progress,
        )

        try:
            await self.scraper.start()

            for i, keyword in enumerate(keywords):
                if not self.is_running:
                    break

                self.current_keyword = keyword
                logger.info(f"开始采集关键词 [{i+1}/{len(keywords)}]: {keyword}")

                if switch_mode == "sequential":
                    # 顺序模式：每个关键词采集到max_products_per_keyword后切换
                    products = await self.scraper.scrape_products(
                        keyword=keyword,
                        max_products=max_products_per_keyword,
                        import_only=import_only,
                    )
                    self.all_products.extend(products)

                elif switch_mode == "timer":
                    # 定时模式：每个关键词采集指定时间后切换
                    start_time = time.time()
                    timeout = switch_interval_minutes * 60

                    products = await self.scraper.scrape_products(
                        keyword=keyword,
                        max_products=max_products_per_keyword,
                        import_only=import_only,
                    )
                    self.all_products.extend(products)

                    elapsed = time.time() - start_time
                    if elapsed < timeout:
                        await asyncio.sleep(timeout - elapsed)

                elif switch_mode == "quantity":
                    # 定量模式：每个关键词采集指定数量后切换
                    products = await self.scraper.scrape_products(
                        keyword=keyword,
                        max_products=min(switch_quantity, max_products_per_keyword),
                        import_only=import_only,
                    )
                    self.all_products.extend(products)

                logger.info(
                    f"关键词 '{keyword}' 采集完成，"
                    f"本次采集 {len(self.scraper.products)} 件，"
                    f"总计 {len(self.all_products)} 件"
                )

                # 关键词切换间隔
                if i < len(keywords) - 1:
                    delay = random.uniform(5, 15)
                    logger.info(f"等待 {delay:.1f} 秒后切换到下一个关键词...")
                    await asyncio.sleep(delay)

        except Exception as e:
            logger.error(f"采集管理器出错: {e}", exc_info=True)
        finally:
            if self.scraper:
                await self.scraper.stop()
            self.is_running = False

        return self.all_products

    def cancel(self):
        """取消所有采集任务"""
        self.is_running = False
        if self.scraper:
            self.scraper.cancel()
