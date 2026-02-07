/**
 * OZON商品采集助手 - Background Service Worker
 * 负责：消息路由、数据存储、API通信、任务调度
 */

// ==================== 配置 ====================
const CONFIG = {
  // 后端API地址（部署后修改为实际地址）
  API_BASE_URL: 'http://localhost:8000',
  // 单次最大采集数
  MAX_PRODUCTS: 50000,
  // 默认滚动间隔（毫秒）
  SCROLL_INTERVAL: 2000,
  // 数据推送批次大小
  PUSH_BATCH_SIZE: 50,
};

// ==================== 全局状态 ====================
let scrapeState = {
  isRunning: false,
  currentKeyword: '',
  totalCollected: 0,
  keywords: [],
  currentKeywordIndex: 0,
  settings: {},
  products: [],
};

// ==================== 消息监听 ====================
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const { action, data } = message;
  
  switch (action) {
    case 'GET_STATE':
      sendResponse({ success: true, data: scrapeState });
      break;
      
    case 'START_SCRAPE':
      handleStartScrape(data, sender);
      sendResponse({ success: true, message: '采集任务已启动' });
      break;
      
    case 'STOP_SCRAPE':
      handleStopScrape();
      sendResponse({ success: true, message: '采集任务已停止' });
      break;
      
    case 'PRODUCTS_FOUND':
      handleProductsFound(data, sender);
      sendResponse({ success: true });
      break;
      
    case 'SCROLL_COMPLETE':
      handleScrollComplete(data, sender);
      sendResponse({ success: true });
      break;
      
    case 'EXPORT_DATA':
      handleExportData(data.format);
      sendResponse({ success: true });
      break;
      
    case 'CLEAR_DATA':
      scrapeState.products = [];
      scrapeState.totalCollected = 0;
      chrome.storage.local.set({ products: [] });
      sendResponse({ success: true, message: '数据已清空' });
      break;
      
    case 'PUSH_TO_SERVER':
      handlePushToServer(data);
      sendResponse({ success: true });
      break;
      
    case 'GET_PRODUCTS':
      sendResponse({ success: true, data: scrapeState.products });
      break;
      
    default:
      sendResponse({ success: false, message: '未知操作' });
  }
  
  return true; // 保持消息通道开放
});

// ==================== 采集控制 ====================

async function handleStartScrape(data, sender) {
  const { keywords, maxProducts, importOnly, switchMode, switchInterval, switchQuantity } = data;
  
  scrapeState = {
    isRunning: true,
    currentKeyword: keywords[0] || '',
    totalCollected: 0,
    keywords: keywords,
    currentKeywordIndex: 0,
    settings: {
      maxProducts: maxProducts || CONFIG.MAX_PRODUCTS,
      importOnly: importOnly || false,
      switchMode: switchMode || 'sequential',
      switchInterval: switchInterval || 30,
      switchQuantity: switchQuantity || 1000,
    },
    products: scrapeState.products, // 保留已有数据
  };
  
  // 保存状态
  chrome.storage.local.set({ scrapeState });
  
  // 通知content script开始采集
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tabs[0]) {
    navigateToSearch(tabs[0].id, scrapeState.currentKeyword, scrapeState.settings.importOnly);
  }
}

function handleStopScrape() {
  scrapeState.isRunning = false;
  chrome.storage.local.set({ scrapeState });
  
  // 通知所有OZON标签页停止
  chrome.tabs.query({ url: '*://www.ozon.ru/*' }, (tabs) => {
    tabs.forEach(tab => {
      chrome.tabs.sendMessage(tab.id, { action: 'STOP_SCRAPE' }).catch(() => {});
    });
  });
}

async function navigateToSearch(tabId, keyword, importOnly) {
  let searchUrl = `https://www.ozon.ru/search/?text=${encodeURIComponent(keyword)}`;
  if (importOnly) {
    searchUrl += '&from_global=true';
  }
  
  await chrome.tabs.update(tabId, { url: searchUrl });
  
  // 等待页面加载完成后开始采集
  chrome.webNavigation.onCompleted.addListener(function onComplete(details) {
    if (details.tabId === tabId && details.url.includes('ozon.ru/search')) {
      chrome.webNavigation.onCompleted.removeListener(onComplete);
      
      // 延迟后通知content script开始
      setTimeout(() => {
        chrome.tabs.sendMessage(tabId, {
          action: 'START_COLLECT',
          data: {
            keyword: scrapeState.currentKeyword,
            maxProducts: scrapeState.settings.maxProducts,
            settings: scrapeState.settings,
          }
        }).catch(err => console.error('发送采集指令失败:', err));
      }, 3000);
    }
  });
}

// ==================== 数据处理 ====================

function handleProductsFound(data, sender) {
  if (!scrapeState.isRunning) return;
  
  const { products, keyword } = data;
  const existingSkus = new Set(scrapeState.products.map(p => p.sku));
  
  let newCount = 0;
  for (const product of products) {
    if (!existingSkus.has(product.sku)) {
      product.keyword = keyword;
      product.scraped_at = new Date().toISOString();
      scrapeState.products.push(product);
      existingSkus.add(product.sku);
      newCount++;
    }
  }
  
  scrapeState.totalCollected = scrapeState.products.length;
  
  // 保存到storage
  chrome.storage.local.set({ 
    products: scrapeState.products,
    scrapeState: scrapeState,
  });
  
  // 通知popup更新
  chrome.runtime.sendMessage({
    action: 'STATE_UPDATE',
    data: {
      totalCollected: scrapeState.totalCollected,
      currentKeyword: scrapeState.currentKeyword,
      newCount: newCount,
      isRunning: scrapeState.isRunning,
    }
  }).catch(() => {});
  
  console.log(`[采集] 关键词: ${keyword}, 新增: ${newCount}, 累计: ${scrapeState.totalCollected}`);
  
  // 检查是否需要切换关键词
  checkKeywordSwitch(sender.tab.id);
}

function handleScrollComplete(data, sender) {
  if (!scrapeState.isRunning) return;
  
  const { hasMore, currentCount } = data;
  
  if (!hasMore || currentCount >= scrapeState.settings.maxProducts) {
    // 当前关键词采集完成，切换到下一个
    switchToNextKeyword(sender.tab.id);
  }
}

function checkKeywordSwitch(tabId) {
  const { switchMode, switchQuantity } = scrapeState.settings;
  
  if (switchMode === 'quantity') {
    // 按数量切换
    const currentKeywordProducts = scrapeState.products.filter(
      p => p.keyword === scrapeState.currentKeyword
    ).length;
    
    if (currentKeywordProducts >= switchQuantity) {
      switchToNextKeyword(tabId);
    }
  }
}

async function switchToNextKeyword(tabId) {
  scrapeState.currentKeywordIndex++;
  
  if (scrapeState.currentKeywordIndex >= scrapeState.keywords.length) {
    // 所有关键词采集完成
    scrapeState.isRunning = false;
    chrome.storage.local.set({ scrapeState });
    
    chrome.runtime.sendMessage({
      action: 'SCRAPE_COMPLETE',
      data: { totalCollected: scrapeState.totalCollected }
    }).catch(() => {});
    
    console.log(`[完成] 所有关键词采集完成，共 ${scrapeState.totalCollected} 件商品`);
    return;
  }
  
  scrapeState.currentKeyword = scrapeState.keywords[scrapeState.currentKeywordIndex];
  chrome.storage.local.set({ scrapeState });
  
  console.log(`[切换] 切换到关键词: ${scrapeState.currentKeyword}`);
  
  // 等待一段时间后切换
  await new Promise(resolve => setTimeout(resolve, 3000 + Math.random() * 5000));
  
  navigateToSearch(tabId, scrapeState.currentKeyword, scrapeState.settings.importOnly);
}

// ==================== 数据导出 ====================

async function handleExportData(format) {
  const products = scrapeState.products;
  if (products.length === 0) return;
  
  if (format === 'csv') {
    exportCSV(products);
  } else if (format === 'json') {
    exportJSON(products);
  }
}

function exportCSV(products) {
  const headers = [
    'SKU', '商品标题', '商品链接', '商品图片', '当前价格(₽)', '原价(₽)', 
    '折扣(%)', '类目', '品牌', '评分', '评论数', '月销量', '周销量',
    '付费推广(天)', '广告费用占比(%)', '卖家类型', '卖家名称', '商品创建时间',
    '被跟数量', '被跟最低价', '被跟最低价链接', '长度(cm)', '宽度(cm)', 
    '高度(cm)', '重量(g)', '配送信息', '搜索关键词', '采集时间'
  ];
  
  const rows = products.map(p => [
    p.sku, `"${(p.title || '').replace(/"/g, '""')}"`, p.product_url, p.image_url,
    p.price, p.original_price, p.discount_percent, `"${(p.category || '').replace(/"/g, '""')}"`,
    p.brand, p.rating, p.review_count, p.monthly_sales, p.weekly_sales,
    p.paid_promo_days, p.ad_cost_ratio, p.seller_type, p.seller_name, p.creation_date,
    p.followers_count, p.follower_min_price, p.follower_min_url,
    p.length_cm, p.width_cm, p.height_cm, p.weight_g,
    `"${(p.delivery_info || '').replace(/"/g, '""')}"`, p.keyword, p.scraped_at
  ].join(','));
  
  const csvContent = '\uFEFF' + headers.join(',') + '\n' + rows.join('\n');
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  
  chrome.downloads.download({
    url: url,
    filename: `ozon_products_${new Date().toISOString().slice(0,10)}.csv`,
    saveAs: true,
  });
}

function exportJSON(products) {
  const jsonContent = JSON.stringify(products, null, 2);
  const blob = new Blob([jsonContent], { type: 'application/json;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  
  chrome.downloads.download({
    url: url,
    filename: `ozon_products_${new Date().toISOString().slice(0,10)}.json`,
    saveAs: true,
  });
}

// ==================== 后端推送 ====================

async function handlePushToServer(data) {
  const { apiUrl } = data;
  const baseUrl = apiUrl || CONFIG.API_BASE_URL;
  const products = scrapeState.products;
  
  if (products.length === 0) return;
  
  try {
    // 分批推送
    for (let i = 0; i < products.length; i += CONFIG.PUSH_BATCH_SIZE) {
      const batch = products.slice(i, i + CONFIG.PUSH_BATCH_SIZE);
      
      const response = await fetch(`${baseUrl}/api/products/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ products: batch }),
      });
      
      if (!response.ok) {
        throw new Error(`推送失败: ${response.status}`);
      }
      
      console.log(`[推送] 已推送 ${Math.min(i + CONFIG.PUSH_BATCH_SIZE, products.length)}/${products.length}`);
    }
    
    chrome.runtime.sendMessage({
      action: 'PUSH_COMPLETE',
      data: { success: true, count: products.length }
    }).catch(() => {});
    
  } catch (error) {
    console.error('[推送失败]', error);
    chrome.runtime.sendMessage({
      action: 'PUSH_COMPLETE',
      data: { success: false, error: error.message }
    }).catch(() => {});
  }
}

// ==================== 初始化 ====================

// 从storage恢复状态
chrome.storage.local.get(['scrapeState', 'products'], (result) => {
  if (result.scrapeState) {
    scrapeState = { ...scrapeState, ...result.scrapeState, isRunning: false };
  }
  if (result.products) {
    scrapeState.products = result.products;
    scrapeState.totalCollected = result.products.length;
  }
});

console.log('[OZON采集助手] Background service worker 已启动');
