/**
 * OZON商品采集助手 - Content Script
 * 注入到OZON页面中，负责：
 * 1. 从搜索结果页面DOM中提取商品数据
 * 2. 自动滚动加载更多商品
 * 3. 创建浮动控制面板
 */

(function() {
  'use strict';

  // ==================== 配置 ====================
  const SCROLL_DELAY_MIN = 1500;
  const SCROLL_DELAY_MAX = 3500;
  const EXTRACT_INTERVAL = 2000;
  const MAX_NO_NEW_DATA_COUNT = 10;

  // ==================== 状态 ====================
  let state = {
    isCollecting: false,
    keyword: '',
    maxProducts: 50000,
    collectedProducts: [],
    seenSkus: new Set(),
    scrollCount: 0,
    noNewDataCount: 0,
    totalOnPage: 0,
  };

  // ==================== 消息监听 ====================
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    switch (message.action) {
      case 'START_COLLECT':
        startCollecting(message.data);
        sendResponse({ success: true });
        break;
      case 'STOP_SCRAPE':
        stopCollecting();
        sendResponse({ success: true });
        break;
      case 'EXTRACT_NOW':
        const products = extractProducts();
        sendResponse({ success: true, data: products });
        break;
    }
    return true;
  });

  // ==================== 采集控制 ====================

  function startCollecting(data) {
    state.isCollecting = true;
    state.keyword = data.keyword || '';
    state.maxProducts = data.maxProducts || 50000;
    state.collectedProducts = [];
    state.seenSkus = new Set();
    state.scrollCount = 0;
    state.noNewDataCount = 0;

    updatePanel('采集中...', 'running');
    console.log(`[OZON采集] 开始采集关键词: ${state.keyword}`);

    // 开始采集循环
    collectLoop();
  }

  function stopCollecting() {
    state.isCollecting = false;
    updatePanel('已停止', 'stopped');
    console.log(`[OZON采集] 停止采集，共采集 ${state.seenSkus.size} 件商品`);
  }

  async function collectLoop() {
    while (state.isCollecting) {
      // 1. 提取当前页面上的商品
      const products = extractProducts();
      
      if (products.length > 0) {
        // 发送到background
        chrome.runtime.sendMessage({
          action: 'PRODUCTS_FOUND',
          data: { products, keyword: state.keyword }
        });
        state.noNewDataCount = 0;
      } else {
        state.noNewDataCount++;
      }

      updatePanel(
        `采集中: ${state.seenSkus.size} 件 | 滚动: ${state.scrollCount}次`,
        'running'
      );

      // 2. 检查是否达到上限
      if (state.seenSkus.size >= state.maxProducts) {
        console.log(`[OZON采集] 达到采集上限 ${state.maxProducts}`);
        stopCollecting();
        chrome.runtime.sendMessage({
          action: 'SCROLL_COMPLETE',
          data: { hasMore: false, currentCount: state.seenSkus.size }
        });
        break;
      }

      // 3. 检查是否无新数据
      if (state.noNewDataCount >= MAX_NO_NEW_DATA_COUNT) {
        console.log('[OZON采集] 连续多次无新数据，尝试点击加载更多');
        const clicked = await clickLoadMore();
        if (!clicked) {
          console.log('[OZON采集] 没有更多数据可加载');
          stopCollecting();
          chrome.runtime.sendMessage({
            action: 'SCROLL_COMPLETE',
            data: { hasMore: false, currentCount: state.seenSkus.size }
          });
          break;
        }
        state.noNewDataCount = 0;
      }

      // 4. 滚动页面
      await smoothScroll();
      state.scrollCount++;

      // 5. 随机等待
      const delay = SCROLL_DELAY_MIN + Math.random() * (SCROLL_DELAY_MAX - SCROLL_DELAY_MIN);
      await sleep(delay);
    }
  }

  // ==================== 数据提取 ====================

  function extractProducts() {
    const newProducts = [];
    
    // 获取所有商品链接
    const allLinks = document.querySelectorAll('a[href*="/product/"]');
    
    for (const link of allLinks) {
      const href = link.getAttribute('href') || '';
      if (!href.includes('/product/')) continue;
      
      // 提取SKU
      const skuMatch = href.match(/-(\d{5,})(?:\/|\?|$)/);
      if (!skuMatch) continue;
      const sku = skuMatch[1];
      
      // 跳过已采集的
      if (state.seenSkus.has(sku)) continue;
      
      // 找到商品卡片容器
      let card = findProductCard(link);
      if (!card) continue;
      
      const cardText = card.innerText || '';
      if (cardText.length < 20) continue; // 过滤空卡片
      
      // ---- 提取各字段 ----
      
      // 标题
      let title = extractTitle(card, link);
      if (!title || title.length < 3) continue;
      
      // 图片
      let imageUrl = extractImage(card);
      
      // 价格
      let { price, originalPrice, discount } = extractPrice(card, cardText);
      
      // 评分和评论
      let { rating, reviewCount } = extractRating(cardText);
      
      // 品牌
      let brand = extractBrand(card);
      
      // 配送信息
      let deliveryInfo = extractDelivery(card, cardText);
      
      // 卖家类型
      let sellerType = extractSellerType(cardText);
      
      // 标记为已采集
      state.seenSkus.add(sku);
      
      const product = {
        sku: sku,
        title: title.substring(0, 500),
        product_url: href.startsWith('http') ? href : 'https://www.ozon.ru' + href,
        image_url: imageUrl,
        price: price,
        original_price: originalPrice,
        discount_percent: discount,
        category: '',
        brand: brand,
        rating: rating,
        review_count: reviewCount,
        monthly_sales: 0,
        weekly_sales: 0,
        paid_promo_days: 0,
        ad_cost_ratio: 0,
        seller_type: sellerType,
        seller_name: '',
        creation_date: '',
        followers_count: 0,
        follower_min_price: 0,
        follower_min_url: '',
        length_cm: 0,
        width_cm: 0,
        height_cm: 0,
        weight_g: 0,
        delivery_info: deliveryInfo,
      };
      
      newProducts.push(product);
    }
    
    if (newProducts.length > 0) {
      state.collectedProducts.push(...newProducts);
      console.log(`[OZON采集] 本次提取 ${newProducts.length} 件新商品，累计 ${state.seenSkus.size} 件`);
    }
    
    return newProducts;
  }

  // ==================== 字段提取辅助函数 ====================

  function findProductCard(link) {
    // 向上查找商品卡片容器
    let el = link;
    for (let i = 0; i < 10; i++) {
      if (!el.parentElement) return null;
      el = el.parentElement;
      
      // 判断是否是商品卡片（足够大的容器）
      const rect = el.getBoundingClientRect();
      if (rect.height > 150 && rect.width > 120) {
        // 检查是否包含价格信息（确认是商品卡片而不是导航）
        const text = el.innerText || '';
        if (text.includes('₽') || text.includes('руб')) {
          return el;
        }
      }
    }
    
    // 退回到link的第5层父元素
    el = link;
    for (let i = 0; i < 5; i++) {
      if (el.parentElement) el = el.parentElement;
    }
    return el;
  }

  function extractTitle(card, link) {
    // 尝试多种选择器
    const selectors = [
      'span[class*="tsBody500Medium"]',
      'a[class*="tile-hover-target"] span',
      '[class*="product-card"] span[class*="text"]',
      'span[class*="title"]',
      'div[class*="title"] span',
    ];
    
    for (const sel of selectors) {
      const el = card.querySelector(sel);
      if (el) {
        const text = el.textContent?.trim();
        if (text && text.length > 5) return text;
      }
    }
    
    // 从链接文本获取
    const linkText = link.textContent?.trim();
    if (linkText && linkText.length > 5) return linkText;
    
    // 从aria-label获取
    const ariaLabel = link.getAttribute('aria-label');
    if (ariaLabel) return ariaLabel;
    
    return '';
  }

  function extractImage(card) {
    const img = card.querySelector('img[src*="cdn"], img[src*="ozon"], img[loading]');
    if (img) {
      return img.src || img.getAttribute('srcset')?.split(' ')[0] || img.getAttribute('data-src') || '';
    }
    return '';
  }

  function extractPrice(card, cardText) {
    let price = 0;
    let originalPrice = 0;
    let discount = 0;
    
    // 方法1: 查找价格元素
    const priceElements = card.querySelectorAll(
      'span[class*="price"], span[class*="tsHeadline"], div[class*="price"] span'
    );
    
    const prices = [];
    for (const pe of priceElements) {
      const text = pe.textContent.replace(/\s/g, '');
      const match = text.match(/(\d+)₽/);
      if (match) {
        prices.push(parseInt(match[1]));
      }
    }
    
    // 方法2: 从文本中提取
    if (prices.length === 0) {
      const priceMatches = cardText.match(/(\d[\d\s]*\d)\s*₽/g);
      if (priceMatches) {
        for (const pm of priceMatches) {
          const val = parseInt(pm.replace(/[^\d]/g, ''));
          if (val > 0 && val < 100000000) {
            prices.push(val);
          }
        }
      }
    }
    
    if (prices.length > 0) {
      // 最小的通常是当前价格
      prices.sort((a, b) => a - b);
      price = prices[0];
      if (prices.length > 1) {
        originalPrice = prices[prices.length - 1];
      }
    }
    
    // 提取折扣
    const discountMatch = cardText.match(/[−-](\d+)\s*%/);
    if (discountMatch) {
      discount = parseInt(discountMatch[1]);
    }
    
    return { price, originalPrice, discount };
  }

  function extractRating(cardText) {
    let rating = 0;
    let reviewCount = 0;
    
    // 格式: "4.8 · 1 234 отзыва" 或 "4,8"
    const ratingMatch = cardText.match(/(\d[,\.]\d)\s*[·•]?\s*([\d\s]+)?\s*(?:отзыв|оценк|оценок)/i);
    if (ratingMatch) {
      rating = parseFloat(ratingMatch[1].replace(',', '.'));
      if (ratingMatch[2]) {
        reviewCount = parseInt(ratingMatch[2].replace(/\s/g, ''));
      }
    } else {
      const simpleRating = cardText.match(/(\d[,\.]\d)/);
      if (simpleRating) {
        const val = parseFloat(simpleRating[1].replace(',', '.'));
        if (val >= 1.0 && val <= 5.0) {
          rating = val;
        }
      }
    }
    
    return { rating, reviewCount };
  }

  function extractBrand(card) {
    const brandEl = card.querySelector(
      '[class*="brand"], [class*="tsBodyControl"], [class*="manufacturer"]'
    );
    return brandEl ? brandEl.textContent?.trim().substring(0, 255) : '';
  }

  function extractDelivery(card, cardText) {
    const deliveryEl = card.querySelector(
      '[class*="delivery"], [class*="tsBodyControl400Small"]'
    );
    if (deliveryEl) return deliveryEl.textContent?.trim() || '';
    
    const deliveryMatch = cardText.match(/(доставит\s+\S+|завтра|послезавтра|\d+\s+(?:янв|фев|мар|апр|мая|июн|июл|авг|сен|окт|ноя|дек))/i);
    return deliveryMatch ? deliveryMatch[0] : '';
  }

  function extractSellerType(cardText) {
    if (cardText.includes('Ozon') && (cardText.includes('Express') || cardText.includes('Экспресс'))) {
      return 'Ozon Express';
    }
    if (cardText.includes('Ozon')) return 'Ozon';
    if (cardText.includes('FBO')) return 'FBO';
    if (cardText.includes('FBS')) return 'FBS';
    return '';
  }

  // ==================== 滚动控制 ====================

  async function smoothScroll() {
    return new Promise((resolve) => {
      const distance = window.innerHeight * (0.6 + Math.random() * 0.4);
      const steps = 10 + Math.floor(Math.random() * 10);
      const stepSize = distance / steps;
      let scrolled = 0;
      
      const timer = setInterval(() => {
        window.scrollBy(0, stepSize);
        scrolled += stepSize;
        if (scrolled >= distance) {
          clearInterval(timer);
          resolve();
        }
      }, 30 + Math.random() * 30);
    });
  }

  async function clickLoadMore() {
    const selectors = [
      'div[class*="paginator"] button',
      'button:not([disabled])',
    ];
    
    for (const sel of selectors) {
      const buttons = document.querySelectorAll(sel);
      for (const btn of buttons) {
        const text = btn.textContent?.trim().toLowerCase() || '';
        if (text.includes('показать ещё') || text.includes('показать еще') || 
            text.includes('загрузить') || text.includes('ещё') ||
            text.includes('показать больше')) {
          btn.click();
          await sleep(2000 + Math.random() * 2000);
          return true;
        }
      }
    }
    
    // 尝试滚动到底部触发加载
    window.scrollTo(0, document.body.scrollHeight);
    await sleep(3000);
    
    return false;
  }

  // ==================== 浮动控制面板 ====================

  function createPanel() {
    if (document.getElementById('ozon-scraper-panel')) return;
    
    const panel = document.createElement('div');
    panel.id = 'ozon-scraper-panel';
    panel.innerHTML = `
      <div id="ozon-scraper-header">
        <span>🔍 OZON采集助手</span>
        <span id="ozon-scraper-minimize" style="cursor:pointer;font-size:16px;">−</span>
      </div>
      <div id="ozon-scraper-body">
        <div id="ozon-scraper-status">就绪</div>
        <div id="ozon-scraper-count">已采集: 0 件</div>
        <div id="ozon-scraper-keyword">关键词: -</div>
        <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;">
          <button id="ozon-scraper-start" class="ozon-btn ozon-btn-primary">开始采集</button>
          <button id="ozon-scraper-stop" class="ozon-btn ozon-btn-danger" disabled>停止</button>
          <button id="ozon-scraper-export-csv" class="ozon-btn">导出CSV</button>
          <button id="ozon-scraper-export-json" class="ozon-btn">导出JSON</button>
          <button id="ozon-scraper-clear" class="ozon-btn ozon-btn-warning">清空</button>
        </div>
        <div style="margin-top:8px;">
          <input id="ozon-scraper-keyword-input" type="text" placeholder="输入关键词（每行一个）" 
                 style="width:100%;padding:4px 8px;border:1px solid #ddd;border-radius:4px;font-size:12px;">
          <div style="margin-top:4px;display:flex;gap:6px;align-items:center;">
            <label style="font-size:11px;"><input type="checkbox" id="ozon-scraper-import-only"> 仅进口商品</label>
            <label style="font-size:11px;">最大数量: <input type="number" id="ozon-scraper-max" value="5000" 
                   style="width:60px;padding:2px 4px;border:1px solid #ddd;border-radius:3px;font-size:11px;"></label>
          </div>
        </div>
      </div>
    `;
    
    document.body.appendChild(panel);
    
    // 绑定事件
    document.getElementById('ozon-scraper-minimize').addEventListener('click', togglePanel);
    document.getElementById('ozon-scraper-start').addEventListener('click', onStartClick);
    document.getElementById('ozon-scraper-stop').addEventListener('click', onStopClick);
    document.getElementById('ozon-scraper-export-csv').addEventListener('click', () => onExportClick('csv'));
    document.getElementById('ozon-scraper-export-json').addEventListener('click', () => onExportClick('json'));
    document.getElementById('ozon-scraper-clear').addEventListener('click', onClearClick);
    
    // 拖拽
    makeDraggable(panel, document.getElementById('ozon-scraper-header'));
    
    // 从URL获取当前关键词
    const urlParams = new URLSearchParams(window.location.search);
    const textParam = urlParams.get('text');
    if (textParam) {
      document.getElementById('ozon-scraper-keyword-input').value = textParam;
    }
  }

  function togglePanel() {
    const body = document.getElementById('ozon-scraper-body');
    const btn = document.getElementById('ozon-scraper-minimize');
    if (body.style.display === 'none') {
      body.style.display = 'block';
      btn.textContent = '−';
    } else {
      body.style.display = 'none';
      btn.textContent = '+';
    }
  }

  function updatePanel(statusText, statusType) {
    const statusEl = document.getElementById('ozon-scraper-status');
    const countEl = document.getElementById('ozon-scraper-count');
    const keywordEl = document.getElementById('ozon-scraper-keyword');
    
    if (statusEl) {
      statusEl.textContent = statusText;
      statusEl.className = `status-${statusType}`;
    }
    if (countEl) countEl.textContent = `已采集: ${state.seenSkus.size} 件`;
    if (keywordEl) keywordEl.textContent = `关键词: ${state.keyword}`;
  }

  function onStartClick() {
    const keywordInput = document.getElementById('ozon-scraper-keyword-input');
    const importOnly = document.getElementById('ozon-scraper-import-only').checked;
    const maxProducts = parseInt(document.getElementById('ozon-scraper-max').value) || 5000;
    
    let keywords = keywordInput.value.trim().split('\n').map(k => k.trim()).filter(k => k);
    
    if (keywords.length === 0) {
      // 从URL获取
      const urlParams = new URLSearchParams(window.location.search);
      const textParam = urlParams.get('text');
      if (textParam) keywords = [textParam];
    }
    
    if (keywords.length === 0) {
      alert('请输入至少一个关键词');
      return;
    }
    
    // 如果当前页面已经是搜索结果页，直接开始采集
    if (window.location.href.includes('/search/') || window.location.href.includes('text=')) {
      startCollecting({
        keyword: keywords[0],
        maxProducts: maxProducts,
        settings: { importOnly, maxProducts },
      });
    }
    
    // 同时通知background处理多关键词
    chrome.runtime.sendMessage({
      action: 'START_SCRAPE',
      data: {
        keywords,
        maxProducts,
        importOnly,
        switchMode: 'sequential',
        switchInterval: 30,
        switchQuantity: 1000,
      }
    });
    
    document.getElementById('ozon-scraper-start').disabled = true;
    document.getElementById('ozon-scraper-stop').disabled = false;
  }

  function onStopClick() {
    stopCollecting();
    chrome.runtime.sendMessage({ action: 'STOP_SCRAPE' });
    
    document.getElementById('ozon-scraper-start').disabled = false;
    document.getElementById('ozon-scraper-stop').disabled = true;
  }

  function onExportClick(format) {
    chrome.runtime.sendMessage({ action: 'EXPORT_DATA', data: { format } });
  }

  function onClearClick() {
    if (confirm('确定要清空所有已采集的数据吗？')) {
      state.collectedProducts = [];
      state.seenSkus = new Set();
      state.totalOnPage = 0;
      chrome.runtime.sendMessage({ action: 'CLEAR_DATA' });
      updatePanel('数据已清空', 'stopped');
    }
  }

  // ==================== 拖拽功能 ====================

  function makeDraggable(element, handle) {
    let isDragging = false;
    let startX, startY, startLeft, startTop;
    
    handle.addEventListener('mousedown', (e) => {
      isDragging = true;
      startX = e.clientX;
      startY = e.clientY;
      const rect = element.getBoundingClientRect();
      startLeft = rect.left;
      startTop = rect.top;
      e.preventDefault();
    });
    
    document.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      element.style.left = (startLeft + dx) + 'px';
      element.style.top = (startTop + dy) + 'px';
      element.style.right = 'auto';
    });
    
    document.addEventListener('mouseup', () => {
      isDragging = false;
    });
  }

  // ==================== 工具函数 ====================

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  // ==================== 初始化 ====================

  // 等待页面加载完成后创建面板
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', createPanel);
  } else {
    createPanel();
  }

  // 监听来自background的状态更新
  chrome.runtime.onMessage.addListener((message) => {
    if (message.action === 'STATE_UPDATE') {
      const { totalCollected, currentKeyword, isRunning } = message.data;
      if (isRunning) {
        updatePanel(`采集中: ${totalCollected} 件`, 'running');
      }
    }
  });

  console.log('[OZON采集助手] Content script 已加载');

})();
