"""
OZON爬虫核心引擎 v2.0
======================
使用Playwright模拟浏览器，通过OZON内部composer-api获取结构化JSON数据。
支持：搜索列表页批量采集 + 商品详情页深度采集。

数据来源说明：
- 搜索列表页：通过拦截 composer-api.bx/page/json/v2 获取 widgetStates 中的商品列表数据
- 商品详情页：通过 composer-api 的分页加载（layout_page_index=2）获取完整特征数据
- 可直接获取的字段：SKU、标题、图片、链接、价格、类目、卖家信息、尺寸、重量、评分、评论数等
- 需要第三方服务的字段：周销量、月销量、广告数据（OZON官方不提供竞品销量接口）
"""
import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime
from typing import List, Dict, Optional, Callable, Any
from urllib.parse import quote, urlencode

from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Response

logger = logging.getLogger(__name__)


class OzonScraper:
    """OZON商品数据爬虫引擎"""

    # OZON内部API的URL模式
    COMPOSER_API_PATTERNS = [
        "/api/composer-api.bx/page/json/v2",
        "/api/entrypoint-api.bx/page/json/v2",
    ]

    # widgetStates中包含商品列表数据的key前缀
    SEARCH_WIDGET_PREFIXES = [
        "searchResultsV2",
        "catalog",
    ]

    # widgetStates中包含商品详情数据的key前缀
    DETAIL_WIDGET_PREFIXES = [
        "webProductHeading",
        "webGallery",
        "webPrice",
        "webSale",
        "webCurrentSeller",
        "webShortCharacteristicsValue",
        "webLongCharacteristics",
        "webCharacteristics",
        "webCategory",
        "breadCrumbs",
        "webReviewProductScore",
        "webSingleProductPage",
        "cellList",
        "bigPromoPDP",
    ]

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
        self.intercepted_api_data: List[Dict] = []

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
                "--lang=ru-RU,ru",
            ],
        }
        if self.proxy:
            launch_args["proxy"] = self.proxy

        self.browser = await self.playwright.chromium.launch(**launch_args)

        self.context = await self.browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            user_agent=self.user_agent,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            extra_http_headers={
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
        )

        # 注入反检测脚本
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['ru-RU', 'ru', 'en']
            });
            window.chrome = { runtime: {} };
        """)

        self.page = await self.context.new_page()
        self.page.set_default_timeout(60000)

        # 设置网络请求拦截
        await self._setup_request_interception()

        logger.info("浏览器启动成功")

    async def _setup_request_interception(self):
        """设置网络请求拦截，捕获OZON的composer-api响应"""

        async def handle_response(response: Response):
            try:
                url = response.url
                # 捕获OZON的composer-api响应
                if any(pattern in url for pattern in self.COMPOSER_API_PATTERNS):
                    if response.status == 200:
                        try:
                            body = await response.json()
                            self.intercepted_api_data.append({
                                "url": url,
                                "data": body,
                                "timestamp": datetime.now().isoformat()
                            })
                            logger.debug(f"拦截到composer-api响应: {url[:100]}...")
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

    async def _close_popups(self):
        """关闭弹窗和Cookie提示"""
        try:
            cookie_btn = await self.page.query_selector('button:has-text("Хорошо"), button:has-text("OK")')
            if cookie_btn:
                await cookie_btn.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass

        try:
            close_btns = await self.page.query_selector_all('[class*="modal"] button[class*="close"]')
            for btn in close_btns:
                try:
                    await btn.click()
                    await asyncio.sleep(0.3)
                except Exception:
                    pass
        except Exception:
            pass

    # ==================== 搜索列表页采集 ====================

    async def navigate_to_search(self, keyword: str, import_only: bool = False):
        """
        导航到OZON搜索页面

        Args:
            keyword: 搜索关键词
            import_only: 是否仅搜索进口商品
        """
        logger.info(f"开始搜索关键词: {keyword}")
        self.intercepted_api_data.clear()

        url = f"https://www.ozon.ru/search/?text={quote(keyword)}&from_global=true"
        await self.page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(3, 5))

        try:
            await self.page.wait_for_selector(
                '[data-widget="searchResultsV2"], [data-widget="searchResultsError"]',
                timeout=15000
            )
        except Exception:
            logger.warning("等待搜索结果超时，尝试继续...")

        await self._close_popups()
        logger.info(f"搜索页面加载完成: {keyword}")

    async def scrape_products(
        self,
        keyword: str,
        max_products: int = 5000,
        import_only: bool = False,
    ) -> List[Dict]:
        """
        采集商品数据的主函数（搜索列表页）

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

            no_new_data_count = 0
            max_no_new_data = 10

            while self.total_scraped < max_products and not self.should_stop:
                prev_count = self.total_scraped

                # 优先从拦截到的API数据中提取
                new_from_api = self._extract_products_from_api_data(keyword)

                # 同时从DOM中提取作为补充
                new_from_dom = await self._extract_products_from_dom(keyword)

                # 合并去重
                new_products = self._merge_products(new_from_api, new_from_dom)

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
                        f"[{keyword}] 已采集 {self.total_scraped}/{max_products} 件商品 "
                        f"(API: {len(new_from_api)}, DOM: {len(new_from_dom)})"
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

                await asyncio.sleep(random.uniform(1.0, 2.5))

        except Exception as e:
            logger.error(f"采集过程出错: {e}", exc_info=True)
            self.errors.append(str(e))
        finally:
            self.is_running = False

        logger.info(f"[{keyword}] 采集完成，共 {len(self.products)} 件商品")
        return self.products

    def _extract_products_from_api_data(self, keyword: str) -> List[Dict]:
        """从拦截到的composer-api响应中提取商品数据"""
        new_products = []

        for api_item in self.intercepted_api_data:
            data = api_item.get("data", {})
            widget_states = data.get("widgetStates", {})

            for key, value_str in widget_states.items():
                # 查找包含搜索结果的widget
                if not any(prefix in key for prefix in self.SEARCH_WIDGET_PREFIXES):
                    continue

                try:
                    if isinstance(value_str, str):
                        value = json.loads(value_str)
                    else:
                        value = value_str

                    # 提取商品列表 - OZON的搜索结果通常在items数组中
                    items = value.get("items", [])
                    if not items:
                        items = value.get("products", [])
                    if not items:
                        # 有时数据在嵌套结构中
                        for v in value.values() if isinstance(value, dict) else []:
                            if isinstance(v, list) and len(v) > 0:
                                items = v
                                break

                    for item in items:
                        product = self._parse_search_item(item, keyword)
                        if product and product["sku"] not in self.seen_skus:
                            self.seen_skus.add(product["sku"])
                            new_products.append(product)

                except (json.JSONDecodeError, TypeError, AttributeError) as e:
                    logger.debug(f"解析widgetState失败: {e}")
                    continue

        # 清空已处理的API数据
        self.intercepted_api_data.clear()
        return new_products

    def _parse_search_item(self, item: Dict, keyword: str) -> Optional[Dict]:
        """
        解析搜索结果中的单个商品数据

        OZON搜索结果中每个商品item的典型结构：
        {
            "action": {"link": "/product/xxx-12345/"},
            "mainState": [
                {"atom": {"textAtom": {"text": "商品标题"}}},
                {"atom": {"priceAtom": {"price": "1 234 ₽", "originalPrice": "2 345 ₽"}}},
                ...
            ],
            "tileImage": {"items": [{"image": {"link": "https://..."}}]},
            "multiButton": {"ozonSubtitle": {"textAtom": {"text": "Ozon"}}},
            ...
        }
        """
        try:
            # 提取SKU和链接
            action = item.get("action", {})
            link = action.get("link", "") or ""
            sku_match = re.search(r'-(\d{5,})(?:/|\?|$)', link)
            if not sku_match:
                # 尝试从其他位置获取SKU
                sku_str = str(item.get("id", "")) or str(item.get("sku", ""))
                if sku_str and sku_str.isdigit() and len(sku_str) >= 5:
                    sku = sku_str
                else:
                    return None
            else:
                sku = sku_match.group(1)

            product_url = f"https://www.ozon.ru{link}" if link and not link.startswith("http") else link

            # 提取标题、价格等 - 从mainState中解析
            title = ""
            price = 0
            original_price = 0
            discount = 0
            rating = 0
            review_count = 0
            brand = ""
            delivery_info = ""
            seller_type = ""

            main_state = item.get("mainState", [])
            for state in main_state:
                atom = state.get("atom", {})

                # 标题
                text_atom = atom.get("textAtom", {})
                if text_atom and not title:
                    text = text_atom.get("text", "")
                    if len(text) > 10:  # 标题通常较长
                        title = text

                # 价格
                price_atom = atom.get("priceAtom", {})
                if price_atom:
                    price_str = price_atom.get("price", "")
                    orig_str = price_atom.get("originalPrice", "")
                    if price_str:
                        price = self._parse_price(price_str)
                    if orig_str:
                        original_price = self._parse_price(orig_str)

                # 标签（可能包含折扣信息）
                tag_atom = atom.get("tagAtom", {})
                if tag_atom:
                    tag_text = tag_atom.get("text", "")
                    discount_match = re.search(r'[-−](\d+)%', tag_text)
                    if discount_match:
                        discount = int(discount_match.group(1))

            # 提取图片
            image_url = ""
            tile_image = item.get("tileImage", {})
            image_items = tile_image.get("items", [])
            if image_items:
                first_img = image_items[0]
                image_url = (first_img.get("image", {}).get("link", "") or
                             first_img.get("link", "") or "")

            # 提取评分和评论数
            atom_list = item.get("atom", {})
            if isinstance(atom_list, dict):
                rating_text = atom_list.get("textAtom", {}).get("text", "")
                rating_match = re.search(r'(\d+[.,]\d+)', rating_text)
                if rating_match:
                    rating = float(rating_match.group(1).replace(',', '.'))
                review_match = re.search(r'(\d[\d\s]*)\s*(?:отзыв|оценк)', rating_text)
                if review_match:
                    review_count = int(review_match.group(1).replace(' ', ''))

            # 提取卖家类型
            multi_button = item.get("multiButton", {})
            ozon_subtitle = multi_button.get("ozonSubtitle", {})
            if ozon_subtitle:
                subtitle_text = ozon_subtitle.get("textAtom", {}).get("text", "")
                if "Ozon" in subtitle_text:
                    seller_type = "Ozon"

            # 如果mainState解析不到标题，尝试备用方式
            if not title:
                title = item.get("title", "") or item.get("name", "")

            # 检测付费推广标记
            is_promoted = False
            label = item.get("label", {}) or {}
            label_items = label.get("items", []) or []
            for li in label_items:
                li_text = str(li.get("title", "")).lower()
                if any(w in li_text for w in ["реклама", "спонс", "продвиж", "promo"]):
                    is_promoted = True
                    break
            # 也检查topLabel
            top_label = item.get("topLabel", {}) or {}
            if top_label:
                tl_text = str(top_label.get("text", "") or top_label.get("title", "")).lower()
                if any(w in tl_text for w in ["реклама", "спонс", "продвиж", "promo"]):
                    is_promoted = True

            # 提取订单/购买数量文本（如果有）
            orders_text = ""
            for state in main_state:
                atom = state.get("atom", {})
                text_atom = atom.get("textAtom", {})
                if text_atom:
                    t = text_atom.get("text", "")
                    if re.search(r'\d+.*(?:заказ|покуп|куплен|продан|раз)', t, re.I):
                        orders_text = t

            # === 增强：从搜索结果中提取库存数量 (maxItems) ===
            stock_quantity = None
            ozon_button = multi_button.get("ozonButton", {})
            add_to_cart = ozon_button.get("addToCart", {})
            if add_to_cart:
                qty_button = add_to_cart.get("quantityButton", {})
                max_items = qty_button.get("maxItems")
                if max_items:
                    stock_quantity = int(max_items)
            
            # 也尝试从其他位置获取
            if stock_quantity is None:
                item_str = json.dumps(item)
                max_items_match = re.search(r'"maxItems"\s*:\s*(\d+)', item_str)
                if max_items_match:
                    stock_quantity = int(max_items_match.group(1))

            # === 增强：从搜索结果中提取评分和评论数 ===
            # OZON搜索结果中的labelList包含评分和评论数
            for state in main_state:
                atom_data = state.get("atom", {})
                label_list = atom_data.get("labelList", {})
                if label_list:
                    label_items = label_list.get("items", [])
                    for li in label_items:
                        li_title = li.get("title", "")
                        # 评分："5.0  "
                        if not rating and re.match(r'^\d+[.,]\d+\s*$', li_title.strip()):
                            rating = float(li_title.strip().replace(',', '.'))
                        # 评论数："2 703 отзыва"
                        if not review_count:
                            rv_match = re.search(r'([\d\s]+)\s*отзыв', li_title)
                            if rv_match:
                                review_count = int(rv_match.group(1).replace(' ', ''))

            return {
                "sku": sku,
                "title": title[:500],
                "product_url": product_url,
                "image_url": image_url,
                "price": price,
                "original_price": original_price,
                "discount_percent": discount,
                "brand": brand[:255],
                "rating": rating,
                "review_count": review_count,
                "delivery_info": delivery_info,
                "seller_type": seller_type,
                "is_promoted": is_promoted,
                "orders_text": orders_text,
                "stock_quantity": stock_quantity,
                "keyword": keyword,
                "scraped_at": datetime.now().isoformat(),
                "data_source": "composer-api",
            }

        except Exception as e:
            logger.debug(f"解析商品数据失败: {e}")
            return None

    @staticmethod
    def _parse_price(price_str: str) -> float:
        """解析OZON价格字符串，如 '1 234 ₽' -> 1234.0"""
        if not price_str:
            return 0
        cleaned = re.sub(r'[^\d.,]', '', price_str.replace('\xa0', ''))
        cleaned = cleaned.replace(',', '.')
        try:
            return float(cleaned)
        except ValueError:
            return 0

    async def _extract_products_from_dom(self, keyword: str) -> List[Dict]:
        """从当前页面DOM中提取商品数据（作为API拦截的补充）"""
        new_products = []

        try:
            products_data = await self.page.evaluate("""
                () => {
                    const products = [];
                    const allProductLinks = document.querySelectorAll('a[href*="/product/"]');
                    const processedUrls = new Set();

                    for (const link of allProductLinks) {
                        const href = link.getAttribute('href') || '';
                        if (!href.includes('/product/') || processedUrls.has(href)) continue;
                        processedUrls.add(href);

                        const skuMatch = href.match(/-(\\d{5,})(?:\\/|\\?|$)/);
                        if (!skuMatch) continue;
                        const sku = skuMatch[1];

                        let card = link.closest('[class*="tile"], [class*="card"], [class*="product"]');
                        if (!card) card = link.parentElement?.parentElement?.parentElement;
                        if (!card) continue;

                        const cardText = card.innerText || '';

                        // 提取标题
                        let title = '';
                        const titleEl = card.querySelector('span[class*="tsBody500Medium"], a[class*="tile-hover-target"]');
                        if (titleEl) title = titleEl.textContent?.trim() || '';
                        if (!title) title = link.textContent?.trim() || '';

                        // 提取图片
                        let imageUrl = '';
                        const img = card.querySelector('img[src*="cdn"], img[src*="ozon"]');
                        if (img) imageUrl = img.src || '';

                        // 提取价格
                        let price = 0;
                        let originalPrice = 0;
                        const priceTexts = cardText.match(/([\\d\\s]+[,.]?\\d*)\\s*[₽¥]/g);
                        if (priceTexts && priceTexts.length > 0) {
                            const parsePrice = (text) => {
                                return parseFloat(text.replace(/[^\\d.,]/g, '').replace(/\\s/g, '').replace(',', '.')) || 0;
                            };
                            price = parsePrice(priceTexts[0]);
                            if (priceTexts.length > 1) originalPrice = parsePrice(priceTexts[1]);
                        }

                        // 提取折扣
                        let discount = 0;
                        const discountMatch = cardText.match(/[−-](\\d+)%/);
                        if (discountMatch) discount = parseInt(discountMatch[1]);

                        // 提取评分和评论数
                        let rating = 0;
                        let reviewCount = 0;
                        const ratingMatch = cardText.match(/(\\d+[.,]\\d+)\\s*[•·]?\\s*([\\d\\s,]+)\\s*(?:отзыв|оценк)/i);
                        if (ratingMatch) {
                            rating = parseFloat(ratingMatch[1].replace(',', '.'));
                            reviewCount = parseInt(ratingMatch[2].replace(/[,\\s]/g, ''));
                        }

                        products.push({
                            sku: sku,
                            title: title.substring(0, 500),
                            product_url: href.startsWith('http') ? href : 'https://www.ozon.ru' + href,
                            image_url: imageUrl,
                            price: price,
                            original_price: originalPrice,
                            discount_percent: discount,
                            rating: rating,
                            review_count: reviewCount,
                        });
                    }
                    return products;
                }
            """)

            for product in products_data:
                sku = str(product.get("sku", ""))
                if sku and sku not in self.seen_skus:
                    product["keyword"] = keyword
                    product["scraped_at"] = datetime.now().isoformat()
                    product["data_source"] = "dom"
                    new_products.append(product)

        except Exception as e:
            logger.error(f"DOM提取商品数据出错: {e}")

        return new_products

    def _merge_products(self, api_products: List[Dict], dom_products: List[Dict]) -> List[Dict]:
        """合并API和DOM提取的商品数据，API数据优先"""
        merged = {}
        for p in api_products:
            merged[p["sku"]] = p
        for p in dom_products:
            if p["sku"] not in merged:
                merged[p["sku"]] = p
        return list(merged.values())

    async def _scroll_page(self) -> bool:
        """向下滚动页面以加载更多商品"""
        try:
            prev_height = await self.page.evaluate("document.body.scrollHeight")

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

            await asyncio.sleep(random.uniform(1.5, 3.0))

            new_height = await self.page.evaluate("document.body.scrollHeight")

            # 检查"加载更多"按钮
            try:
                load_more = await self.page.query_selector(
                    'button:has-text("Показать ещё"), div[class*="paginator"] button'
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

    # ==================== 商品详情页深度采集 ====================

    async def get_product_detail(self, sku: str) -> Optional[Dict]:
        """
        获取单个商品的详细信息（通过composer-api获取完整数据）

        通过访问商品详情页并拦截composer-api响应，可以获取以下字段：
        - 完整类目路径（面包屑导航）
        - 卖家名称和类型
        - 商品特征（尺寸、重量等）
        - 评分和评论数
        - 跟卖信息
        - 商品创建时间（从SEO数据中）

        Args:
            sku: 商品SKU

        Returns:
            商品详细数据字典
        """
        detail_url = f"https://www.ozon.ru/product/{sku}/"
        self.intercepted_api_data.clear()

        try:
            detail_page = await self.context.new_page()

            # 设置API拦截
            detail_api_data = []

            async def handle_detail_response(response: Response):
                try:
                    url = response.url
                    if any(p in url for p in self.COMPOSER_API_PATTERNS):
                        if response.status == 200:
                            try:
                                body = await response.json()
                                detail_api_data.append(body)
                            except Exception:
                                pass
                except Exception:
                    pass

            detail_page.on("response", handle_detail_response)

            # 访问商品详情页
            await detail_page.goto(detail_url, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(3, 5))

            # 滚动页面触发第二页数据加载（特征数据通常在第二页）
            await detail_page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(2)
            await detail_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(3)

            # 从拦截到的API数据中解析详情
            detail = self._parse_detail_api_data(detail_api_data, sku)

            # 补充：从DOM中提取（作为API数据的补充）
            dom_detail = await self._extract_detail_from_dom(detail_page, sku)
            detail = self._merge_detail(detail, dom_detail)

            await detail_page.close()
            return detail

        except Exception as e:
            logger.error(f"获取商品详情出错 (SKU: {sku}): {e}")
            return None

    def _parse_detail_api_data(self, api_data_list: List[Dict], sku: str) -> Dict:
        """
        从composer-api响应中解析商品详情数据

        OZON的商品详情页通过composer-api分多次加载：
        - 第一次加载：基本信息（标题、价格、图片、卖家、简要特征）
        - 第二次加载（layout_page_index=2）：完整特征（尺寸、重量等）、评论详情
        """
        detail = {
            "sku": sku,
            "title": "",
            "product_url": f"https://www.ozon.ru/product/{sku}/",
            "image_url": "",
            "images": [],
            "price": 0,
            "original_price": 0,
            "discount_percent": 0,
            "category": "",
            "category_id": "",
            "brand": "",
            "rating": 0,
            "review_count": 0,
            "seller_name": "",
            "seller_type": "",
            "seller_id": "",
            "creation_date": "",
            "followers_count": 0,
            "follower_min_price": 0,
            "follower_min_url": "",
            "length_cm": 0,
            "width_cm": 0,
            "height_cm": 0,
            "weight_g": 0,
            "volume_liters": 0,
            "characteristics": {},
            "short_characteristics": [],
            "delivery_info": "",
            "stock_quantity": None,
            "stock_status": "",
            "is_promoted": False,
            "orders_text": "",
            "estimated_total_sales": 0,
            "extra_data": {},
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

                # 解析标题
                if "webProductHeading" in key:
                    detail["title"] = value.get("title", "") or detail["title"]
                    # 有时SKU在这里
                    detail["sku"] = str(value.get("sku", sku))

                # 解析图片
                elif "webGallery" in key:
                    covers = value.get("coverImage", [])
                    if covers:
                        detail["image_url"] = covers[0] if isinstance(covers[0], str) else covers[0].get("link", "")
                    images = value.get("images", [])
                    detail["images"] = [img if isinstance(img, str) else img.get("link", "") for img in images[:10]]

                # 解析价格
                elif "webPrice" in key or "webSale" in key:
                    price_str = value.get("price", "") or value.get("cardPrice", "")
                    orig_str = value.get("originalPrice", "") or value.get("fullPrice", "")
                    if price_str:
                        detail["price"] = self._parse_price(price_str)
                    if orig_str:
                        detail["original_price"] = self._parse_price(orig_str)
                    # 折扣
                    discount_str = value.get("discount", "")
                    if discount_str:
                        dm = re.search(r'(\d+)', str(discount_str))
                        if dm:
                            detail["discount_percent"] = int(dm.group(1))

                # 解析类目（面包屑导航）
                elif "breadCrumbs" in key or "webCategory" in key:
                    crumbs = value.get("breadcrumbs", []) or value.get("items", [])
                    if crumbs:
                        category_parts = []
                        for crumb in crumbs:
                            name = crumb.get("text", "") or crumb.get("name", "")
                            if name and name != "Ozon":
                                category_parts.append(name)
                        detail["category"] = " > ".join(category_parts)
                        # 最后一个面包屑通常有category_id
                        if crumbs:
                            last_crumb = crumbs[-1]
                            cat_url = last_crumb.get("url", "") or last_crumb.get("link", "")
                            cat_id_match = re.search(r'/category/[^/]*-(\d+)/', cat_url)
                            if cat_id_match:
                                detail["category_id"] = cat_id_match.group(1)

                # 解析卖家信息（增强版：提取trustFactors中的卖家订单数和详细信息）
                elif "webCurrentSeller" in key:
                    # 提取卖家名称
                    detail["seller_name"] = value.get("name", "") or value.get("sellerName", "")
                    detail["seller_id"] = str(value.get("sellerId", "") or value.get("id", ""))
                    
                    # 从seller cell中提取卖家名称（备用）
                    if not detail["seller_name"]:
                        seller_cell = value.get("sellerCell", {})
                        left_block = seller_cell.get("leftBlock", {})
                        common = left_block.get("common", {})
                        title = common.get("title", {})
                        detail["seller_name"] = title.get("text", "")
                    
                    # 从sellerId提取（备用）
                    if not detail["seller_id"]:
                        value_str_tmp = json.dumps(value)
                        sid_match = re.search(r'"sellerId"\s*:\s*"?(\d+)"?', value_str_tmp)
                        if sid_match:
                            detail["seller_id"] = sid_match.group(1)
                    
                    # 判断卖家类型
                    is_ozon = value.get("isOzon", False) or "Ozon" in detail["seller_name"]
                    if is_ozon:
                        detail["seller_type"] = "Ozon (自营)"
                    else:
                        delivery_schema = value.get("deliverySchema", "")
                        if delivery_schema:
                            detail["seller_type"] = delivery_schema
                        else:
                            detail["seller_type"] = "第三方卖家"
                    
                    # === 从trust factors中提取卖家订单数 ===
                    # OZON的trustFactors结构：
                    # [{"title": {"text": "Заказы"}, "badge": {"text": "259 K"}, ...}, ...]
                    trust_factors = value.get("trustFactors", [])
                    for tf in trust_factors:
                        tf_title = tf.get("title", {}).get("text", "")
                        tf_badge = tf.get("badge", {}).get("text", "")
                        
                        # 提取卖家总订单数
                        if "Заказ" in tf_title and tf_badge:
                            detail["extra_data"]["seller_total_orders"] = tf_badge
                        
                        # 提取卖家评分
                        if "Рейтинг" in tf_title and tf_badge:
                            detail["extra_data"]["seller_rating"] = tf_badge
                        
                        # 提取配送信息
                        if "Достав" in tf_title and tf_badge:
                            detail["extra_data"]["seller_delivery"] = tf_badge
                        
                        # 提取卖家注册日期
                        if "Дат" in tf_title and tf_badge:
                            detail["extra_data"]["seller_reg_date"] = tf_badge
                    
                    # 检查FBO/FBS标记（从seller icon中提取）
                    value_str_check = json.dumps(value)
                    if '"sellerIcon"' in value_str_check:
                        if 'premium' in value_str_check.lower() or 'ozon_premium' in value_str_check.lower():
                            detail["seller_type"] = "FBO (Фулфилмент Ozon)"
                        elif 'fbs' in value_str_check.lower():
                            detail["seller_type"] = "FBS (Со склада продавца)"

                # 解析跟卖信息（增强版）
                elif "cellList" in key or "bigPromoPDP" in key:
                    items = value.get("items", [])
                    if items and len(items) > 1:
                        detail["followers_count"] = len(items) - 1
                        min_price = float('inf')
                        min_url = ""
                        for offer_item in items[1:]:
                            offer_price_str = offer_item.get("price", "") or ""
                            offer_price = self._parse_price(str(offer_price_str))
                            if 0 < offer_price < min_price:
                                min_price = offer_price
                                min_url = offer_item.get("action", {}).get("link", "")
                        if min_price < float('inf'):
                            detail["follower_min_price"] = min_price
                            if min_url:
                                detail["follower_min_url"] = f"https://www.ozon.ru{min_url}" if not min_url.startswith("http") else min_url
                
                # 解析webBestSeller（“有更便宜或更快”跟卖提示）
                elif "webBestSeller" in key:
                    # webBestSeller结构：{"textRs": [...], "count": "50", "modalLink": "/modal/otherOffersFromSellers?product_id=xxx"}
                    count_str = value.get("count", "")
                    if count_str:
                        try:
                            follower_count = int(count_str)
                            if follower_count > detail.get("followers_count", 0):
                                detail["followers_count"] = follower_count
                        except (ValueError, TypeError):
                            pass
                    # 提取最低价格
                    text_rs = value.get("textRs", [])
                    for tr in text_rs:
                        content = tr.get("content", "")
                        if "₽" in content or "\u20bd" in content:
                            best_price = self._parse_price(content)
                            if best_price > 0:
                                detail["follower_min_price"] = best_price

                # 解析评分和评论
                elif "webReviewProductScore" in key:
                    detail["rating"] = float(value.get("score", 0) or value.get("rating", 0) or 0)
                    detail["review_count"] = int(value.get("count", 0) or value.get("totalCount", 0) or 0)

                # 解析加购按钮中的库存限制（freeRest字段）
                elif "addToCart" in key.lower() or "webAddToCart" in key:
                    # === 关键发现：freeRest字段包含精确库存数量 ===
                    # OZON的webAddToCart widget结构：
                    # {"firstButton": {"toCart": {...}, "additionalButton": {
                    #     "incrementButton": {...}, "sku": "12345", "freeRest": 152,
                    #     "minAddToCartQuantity": 1, "inCartQuantity": 0
                    # }}, ...}
                    
                    # 方法1：直接从顶层获取freeRest
                    free_rest = value.get("freeRest")
                    if free_rest is not None:
                        detail["stock_quantity"] = int(free_rest)
                    
                    # 方法2：从嵌套结构中获取freeRest
                    if detail["stock_quantity"] is None or detail["stock_quantity"] == 0:
                        first_btn = value.get("firstButton", {})
                        add_btn = first_btn.get("additionalButton", {})
                        nested_rest = add_btn.get("freeRest")
                        if nested_rest is not None:
                            detail["stock_quantity"] = int(nested_rest)
                    
                    # 方法3：深度搜索整个widget JSON中的freeRest
                    if detail["stock_quantity"] is None or detail["stock_quantity"] == 0:
                        value_str_search = json.dumps(value)
                        rest_match = re.search(r'"freeRest"\s*:\s*(\d+)', value_str_search)
                        if rest_match:
                            detail["stock_quantity"] = int(rest_match.group(1))
                    
                    # 方法4：从quantityButton.maxItems获取
                    if detail["stock_quantity"] is None or detail["stock_quantity"] == 0:
                        qty_btn = value.get("quantityButton", {})
                        max_items = qty_btn.get("maxItems")
                        if max_items:
                            detail["stock_quantity"] = int(max_items)
                    
                    # 旧方法兜底
                    if detail["stock_quantity"] is None or detail["stock_quantity"] == 0:
                        max_qty = value.get("maxQuantity") or value.get("limit") or value.get("maxCount")
                        if max_qty:
                            detail["stock_quantity"] = int(max_qty)
                    
                    # 检查是否缺货
                    if value.get("isOutOfStock") or value.get("outOfStock"):
                        detail["stock_quantity"] = 0
                        detail["stock_status"] = "out_of_stock"
                    elif detail["stock_quantity"] is not None and detail["stock_quantity"] > 0:
                        detail["stock_status"] = "in_stock"

                # 解析推广/广告标记
                elif "webStickyProducts" in key or "webPromo" in key:
                    detail["is_promoted"] = True

                # 解析"已购买"等销量提示
                elif "webSocialProof" in key or "webPopularity" in key:
                    proof_text = str(value)
                    orders_match = re.search(r'(\d[\d\s]*)\s*(?:заказ|покуп|куплен|продан|раз)', proof_text, re.I)
                    if orders_match:
                        detail["orders_text"] = orders_match.group(0)
                    bought_match = re.search(r'[Кк]упили\s+(\d[\d\s]*)\s*раз', proof_text)
                    if bought_match:
                        detail["estimated_total_sales"] = int(bought_match.group(1).replace(' ', ''))

                # 解析简要特征
                elif "webShortCharacteristicsValue" in key:
                    chars = value.get("characteristics", []) or value.get("items", [])
                    detail["short_characteristics"] = chars

                # 解析完整特征（包含尺寸、重量等）
                elif "webLongCharacteristics" in key or "webCharacteristics" in key:
                    self._parse_characteristics(value, detail)

            # 解析SEO数据（可能包含创建时间）
            seo = api_data.get("seo", {})
            if seo:
                # 商品创建时间有时在script标签的JSON-LD中
                script_list = seo.get("script", [])
                for script in script_list:
                    if isinstance(script, dict):
                        inner = script.get("innerHTML", "")
                        if inner:
                            try:
                                ld = json.loads(inner)
                                if "datePublished" in ld:
                                    detail["creation_date"] = ld["datePublished"]
                                if "brand" in ld:
                                    brand_data = ld["brand"]
                                    if isinstance(brand_data, dict):
                                        detail["brand"] = brand_data.get("name", "")
                                    elif isinstance(brand_data, str):
                                        detail["brand"] = brand_data
                            except (json.JSONDecodeError, TypeError):
                                pass

        return detail

    def _parse_characteristics(self, value: Any, detail: Dict):
        """
        解析商品特征数据，提取尺寸、重量等信息

        OZON的特征数据结构（webLongCharacteristics widget）：
        {
            "characteristics": [
                {
                    "title": "Общие",
                    "short": [
                        {"key": "Тип", "name": "Тип", "values": [{"text": "Смартфон"}]},
                        {"key": "Вес товара, г", "name": "Вес товара, г", "values": [{"text": "171"}]},
                        ...
                    ]
                },
                {
                    "title": "Габариты",
                    "short": [
                        {"key": "Длина упаковки", "values": [{"text": "17.5 см"}]},
                        ...
                    ]
                }
            ]
        }
        """
        characteristics = value.get("characteristics", [])
        if not characteristics:
            characteristics = value.get("groups", [])

        for group in characteristics:
            group_title = group.get("title", "")
            items = group.get("short", []) or group.get("characteristics", []) or group.get("items", [])

            for item in items:
                key = (item.get("key", "") or item.get("name", "")).strip()
                values = item.get("values", [])
                value_text = ""
                if values and isinstance(values, list):
                    value_text = values[0].get("text", "") if isinstance(values[0], dict) else str(values[0])
                elif isinstance(item.get("value", ""), str):
                    value_text = item["value"]

                if not key or not value_text:
                    continue

                # 存储所有特征
                detail["characteristics"][key] = value_text

                # 提取尺寸和重量
                key_lower = key.lower()

                # 重量
                if any(w in key_lower for w in ["вес", "weight", "масса"]):
                    weight = self._extract_number(value_text)
                    if weight:
                        # 判断单位
                        if "кг" in value_text.lower() or "kg" in value_text.lower():
                            detail["weight_g"] = weight * 1000
                        else:
                            detail["weight_g"] = weight

                # 长度
                elif any(w in key_lower for w in ["длина", "length"]):
                    length = self._extract_number(value_text)
                    if length:
                        if "мм" in value_text.lower() or "mm" in value_text.lower():
                            detail["length_cm"] = length / 10
                        elif "м" in value_text.lower() and "см" not in value_text.lower():
                            detail["length_cm"] = length * 100
                        else:
                            detail["length_cm"] = length

                # 宽度
                elif any(w in key_lower for w in ["ширина", "width"]):
                    width = self._extract_number(value_text)
                    if width:
                        if "мм" in value_text.lower() or "mm" in value_text.lower():
                            detail["width_cm"] = width / 10
                        elif "м" in value_text.lower() and "см" not in value_text.lower():
                            detail["width_cm"] = width * 100
                        else:
                            detail["width_cm"] = width

                # 高度
                elif any(w in key_lower for w in ["высота", "height", "толщина", "глубина"]):
                    height = self._extract_number(value_text)
                    if height:
                        if "мм" in value_text.lower() or "mm" in value_text.lower():
                            detail["height_cm"] = height / 10
                        elif "м" in value_text.lower() and "см" not in value_text.lower():
                            detail["height_cm"] = height * 100
                        else:
                            detail["height_cm"] = height

                # 体积
                elif any(w in key_lower for w in ["объем", "volume", "объём"]):
                    volume = self._extract_number(value_text)
                    if volume:
                        detail["volume_liters"] = volume

                # 品牌
                elif any(w in key_lower for w in ["бренд", "brand", "торговая марка"]):
                    detail["brand"] = value_text

    @staticmethod
    def _extract_number(text: str) -> Optional[float]:
        """从文本中提取数字"""
        match = re.search(r'(\d+[.,]?\d*)', text.replace('\xa0', '').replace(' ', ''))
        if match:
            return float(match.group(1).replace(',', '.'))
        return None

    async def _extract_detail_from_dom(self, page: Page, sku: str) -> Dict:
        """从商品详情页DOM中提取数据（增强版：含尺寸重量、库存、跟卖数据）"""
        try:
            return await page.evaluate("""
                (sku) => {
                    const data = {sku: sku};
                    const text = document.body.innerText;

                    // 提取类目（面包屑导航）
                    const breadcrumbs = document.querySelectorAll(
                        'ol[class*="breadcrumb"] a, nav a[href*="/category/"]'
                    );
                    if (breadcrumbs.length > 0) {
                        data.category = Array.from(breadcrumbs)
                            .map(a => a.textContent.trim())
                            .filter(t => t && t !== 'Ozon')
                            .join(' > ');
                    }

                    // 提取卖家信息
                    const sellerEl = document.querySelector(
                        '[data-widget="webCurrentSeller"] a, [class*="seller"] a'
                    );
                    if (sellerEl) {
                        data.seller_name = sellerEl.textContent.trim();
                    }

                    // 提取标题
                    const titleEl = document.querySelector(
                        '[data-widget="webProductHeading"] h1, h1[class*="title"]'
                    );
                    if (titleEl) {
                        data.title = titleEl.textContent.trim();
                    }

                    // 提取价格
                    const priceEl = document.querySelector(
                        '[data-widget="webPrice"] span[class*="price"], [class*="price-number"]'
                    );
                    if (priceEl) {
                        const priceText = priceEl.textContent || '';
                        const priceMatch = priceText.match(/([\\d\\s]+)/);
                        if (priceMatch) {
                            data.price = parseFloat(priceMatch[1].replace(/\\s/g, ''));
                        }
                    }

                    // === 增强：从webCharacteristics中提取尺寸和重量 ===
                    const charWidget = document.querySelector('[data-widget="webCharacteristics"]');
                    if (charWidget) {
                        const charText = charWidget.innerText;
                        
                        // 提取重量："Вес товара, г: 171" 或 "Вес, кг: 0.171"
                        const weightPatterns = [
                            /\u0412\u0435\u0441[^:]*,\s*\u0433\s*[:\n]\s*([\d.,]+)/i,
                            /\u0412\u0435\u0441[^:]*,\s*\u043a\u0433\s*[:\n]\s*([\d.,]+)/i,
                            /\u0412\u0435\u0441[^:]*\s*[:\n]\s*([\d.,]+)\s*\u0433/i,
                            /\u0412\u0435\u0441[^:]*\s*[:\n]\s*([\d.,]+)\s*\u043a\u0433/i,
                            /weight[^:]*:\s*([\d.,]+)/i,
                        ];
                        for (const p of weightPatterns) {
                            const m = charText.match(p);
                            if (m) {
                                const val = parseFloat(m[1].replace(',', '.'));
                                if (charText.match(p)[0].includes('\u043a\u0433') || charText.match(p)[0].includes('kg')) {
                                    data.weight_g = val * 1000;
                                } else {
                                    data.weight_g = val;
                                }
                                break;
                            }
                        }
                        
                        // 提取尺寸："Размеры, мм: 147,6х71,6х7,8" 或分开的长宽高
                        const dimMatch = charText.match(/\u0420\u0430\u0437\u043c\u0435\u0440\u044b[^:]*,\s*\u043c\u043c\s*[:\n]\s*([\d.,]+)\s*[\u0445xX\u00d7]\s*([\d.,]+)\s*[\u0445xX\u00d7]\s*([\d.,]+)/i);
                        if (dimMatch) {
                            data.length_cm = parseFloat(dimMatch[1].replace(',', '.')) / 10;
                            data.width_cm = parseFloat(dimMatch[2].replace(',', '.')) / 10;
                            data.height_cm = parseFloat(dimMatch[3].replace(',', '.')) / 10;
                        } else {
                            // 分别提取长宽高
                            const lenMatch = charText.match(/\u0414\u043b\u0438\u043d\u0430[^:]*\s*[:\n]\s*([\d.,]+)/i);
                            const widMatch = charText.match(/\u0428\u0438\u0440\u0438\u043d\u0430[^:]*\s*[:\n]\s*([\d.,]+)/i);
                            const heiMatch = charText.match(/\u0412\u044b\u0441\u043e\u0442\u0430[^:]*\s*[:\n]\s*([\d.,]+)/i);
                            if (lenMatch) data.length_cm = parseFloat(lenMatch[1].replace(',', '.'));
                            if (widMatch) data.width_cm = parseFloat(widMatch[1].replace(',', '.'));
                            if (heiMatch) data.height_cm = parseFloat(heiMatch[1].replace(',', '.'));
                        }
                    }

                    // === 增强：提取跟卖数量（webBestSeller widget） ===
                    const bestSellerWidget = document.querySelector('[data-widget="webBestSeller"]');
                    if (bestSellerWidget) {
                        const bsText = bestSellerWidget.innerText;
                        // "Есть дешевле или быстрее\nот 46 378 ₽\n50"
                        const countMatch = bsText.match(/(\d+)\s*$/);
                        if (countMatch) {
                            data.followers_count = parseInt(countMatch[1]);
                        }
                        const priceMatch = bsText.match(/\u043e\u0442\s+([\d\s]+)\s*\u20bd/i);
                        if (priceMatch) {
                            data.follower_min_price = parseFloat(priceMatch[1].replace(/\s/g, ''));
                        }
                    }

                    return data;
                }
            """, sku)
        except Exception as e:
            logger.error(f"DOM详情提取出错: {e}")
            return {}

    @staticmethod
    def _merge_detail(api_detail: Dict, dom_detail: Dict) -> Dict:
        """合并API和DOM提取的详情数据，API数据优先"""
        for key, value in dom_detail.items():
            if key in api_detail:
                if not api_detail[key] and value:
                    api_detail[key] = value
            else:
                api_detail[key] = value
        return api_detail

    # ==================== 批量详情采集 ====================

    async def scrape_product_details(
        self,
        sku_list: List[str],
        delay_range: tuple = (3, 6),
        on_detail_progress: Optional[Callable] = None,
    ) -> List[Dict]:
        """
        批量获取商品详情

        Args:
            sku_list: SKU列表
            delay_range: 每个请求之间的延迟范围（秒）
            on_detail_progress: 进度回调

        Returns:
            商品详情列表
        """
        details = []
        total = len(sku_list)

        for i, sku in enumerate(sku_list):
            if self.should_stop:
                break

            logger.info(f"获取商品详情 [{i+1}/{total}]: SKU={sku}")

            detail = await self.get_product_detail(str(sku))
            if detail:
                details.append(detail)

            if on_detail_progress:
                on_detail_progress({
                    "current": i + 1,
                    "total": total,
                    "sku": sku,
                    "status": "running",
                })

            # 随机延迟
            if i < total - 1:
                await asyncio.sleep(random.uniform(*delay_range))

        logger.info(f"批量详情采集完成，共 {len(details)}/{total} 件")
        return details

    def cancel(self):
        """取消当前采集任务"""
        self.should_stop = True
        logger.info("采集任务已取消")


class OzonScraperManager:
    """
    OZON爬虫管理器
    管理多个关键词的采集任务，支持列表页+详情页两阶段采集
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
        fetch_details: bool = False,
        detail_delay_range: tuple = (3, 6),
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
            fetch_details: 是否进一步获取每个商品的详情页数据
            detail_delay_range: 详情页请求延迟范围
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

                # 第一阶段：搜索列表页采集
                if switch_mode == "sequential":
                    products = await self.scraper.scrape_products(
                        keyword=keyword,
                        max_products=max_products_per_keyword,
                        import_only=import_only,
                    )
                elif switch_mode == "timer":
                    start_time = time.time()
                    timeout = switch_interval_minutes * 60
                    products = await self.scraper.scrape_products(
                        keyword=keyword,
                        max_products=max_products_per_keyword,
                        import_only=import_only,
                    )
                    elapsed = time.time() - start_time
                    if elapsed < timeout:
                        await asyncio.sleep(timeout - elapsed)
                elif switch_mode == "quantity":
                    products = await self.scraper.scrape_products(
                        keyword=keyword,
                        max_products=min(switch_quantity, max_products_per_keyword),
                        import_only=import_only,
                    )
                else:
                    products = await self.scraper.scrape_products(
                        keyword=keyword,
                        max_products=max_products_per_keyword,
                        import_only=import_only,
                    )

                # 第二阶段：详情页深度采集（可选）
                if fetch_details and products:
                    logger.info(f"开始获取 {len(products)} 个商品的详情数据...")
                    sku_list = [p["sku"] for p in products]
                    details = await self.scraper.scrape_product_details(
                        sku_list=sku_list,
                        delay_range=detail_delay_range,
                    )

                    # 合并详情数据到商品列表
                    detail_map = {str(d.get("sku", "")): d for d in details}
                    for product in products:
                        detail = detail_map.get(str(product.get("sku", "")))
                        if detail:
                            for key, value in detail.items():
                                if key not in product or not product[key]:
                                    product[key] = value

                self.all_products.extend(products)

                logger.info(
                    f"关键词 '{keyword}' 采集完成，"
                    f"本次采集 {len(products)} 件，"
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
