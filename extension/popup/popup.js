/**
 * OZON商品采集助手 - Popup Script
 */

document.addEventListener('DOMContentLoaded', () => {
  // 获取DOM元素
  const elements = {
    statusBar: document.getElementById('statusBar'),
    totalCount: document.getElementById('totalCount'),
    keywordCount: document.getElementById('keywordCount'),
    keywords: document.getElementById('keywords'),
    maxProducts: document.getElementById('maxProducts'),
    switchMode: document.getElementById('switchMode'),
    importOnly: document.getElementById('importOnly'),
    btnStart: document.getElementById('btnStart'),
    btnStop: document.getElementById('btnStop'),
    btnExportCSV: document.getElementById('btnExportCSV'),
    btnExportJSON: document.getElementById('btnExportJSON'),
    btnClear: document.getElementById('btnClear'),
    serverUrl: document.getElementById('serverUrl'),
    btnPush: document.getElementById('btnPush'),
  };

  // 加载保存的设置
  chrome.storage.local.get(['settings', 'scrapeState'], (result) => {
    if (result.settings) {
      elements.keywords.value = result.settings.keywords || '';
      elements.maxProducts.value = result.settings.maxProducts || 5000;
      elements.switchMode.value = result.settings.switchMode || 'sequential';
      elements.importOnly.checked = result.settings.importOnly || false;
      elements.serverUrl.value = result.settings.serverUrl || '';
    }
    
    if (result.scrapeState) {
      updateUI(result.scrapeState);
    }
  });

  // 获取当前状态
  chrome.runtime.sendMessage({ action: 'GET_STATE' }, (response) => {
    if (response && response.success) {
      updateUI(response.data);
    }
  });

  // 开始采集
  elements.btnStart.addEventListener('click', async () => {
    const keywordsText = elements.keywords.value.trim();
    const keywords = keywordsText.split('\n').map(k => k.trim()).filter(k => k);
    
    if (keywords.length === 0) {
      setStatus('请输入至少一个关键词', 'error');
      return;
    }
    
    const settings = {
      keywords: keywordsText,
      maxProducts: parseInt(elements.maxProducts.value) || 5000,
      switchMode: elements.switchMode.value,
      importOnly: elements.importOnly.checked,
      serverUrl: elements.serverUrl.value,
    };
    
    // 保存设置
    chrome.storage.local.set({ settings });
    
    // 检查当前标签页是否是OZON
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const currentTab = tabs[0];
    
    if (!currentTab || !currentTab.url.includes('ozon.ru')) {
      // 打开OZON搜索页
      const searchUrl = `https://www.ozon.ru/search/?text=${encodeURIComponent(keywords[0])}${settings.importOnly ? '&from_global=true' : ''}`;
      chrome.tabs.create({ url: searchUrl });
    }
    
    // 发送采集指令
    chrome.runtime.sendMessage({
      action: 'START_SCRAPE',
      data: {
        keywords,
        maxProducts: settings.maxProducts,
        importOnly: settings.importOnly,
        switchMode: settings.switchMode,
        switchInterval: 30,
        switchQuantity: 1000,
      }
    });
    
    elements.btnStart.disabled = true;
    elements.btnStop.disabled = false;
    setStatus('采集任务已启动...', 'running');
  });

  // 停止采集
  elements.btnStop.addEventListener('click', () => {
    chrome.runtime.sendMessage({ action: 'STOP_SCRAPE' });
    elements.btnStart.disabled = false;
    elements.btnStop.disabled = true;
    setStatus('采集已停止', '');
  });

  // 导出CSV
  elements.btnExportCSV.addEventListener('click', () => {
    chrome.runtime.sendMessage({ action: 'EXPORT_DATA', data: { format: 'csv' } });
    setStatus('正在导出CSV...', '');
  });

  // 导出JSON
  elements.btnExportJSON.addEventListener('click', () => {
    chrome.runtime.sendMessage({ action: 'EXPORT_DATA', data: { format: 'json' } });
    setStatus('正在导出JSON...', '');
  });

  // 清空数据
  elements.btnClear.addEventListener('click', () => {
    if (confirm('确定要清空所有已采集的数据吗？')) {
      chrome.runtime.sendMessage({ action: 'CLEAR_DATA' });
      elements.totalCount.textContent = '0';
      setStatus('数据已清空', '');
    }
  });

  // 推送到服务器
  elements.btnPush.addEventListener('click', () => {
    const apiUrl = elements.serverUrl.value.trim();
    if (!apiUrl) {
      setStatus('请输入服务器地址', 'error');
      return;
    }
    
    chrome.runtime.sendMessage({
      action: 'PUSH_TO_SERVER',
      data: { apiUrl }
    });
    setStatus('正在推送数据到服务器...', 'running');
  });

  // 监听状态更新
  chrome.runtime.onMessage.addListener((message) => {
    if (message.action === 'STATE_UPDATE') {
      elements.totalCount.textContent = message.data.totalCollected || 0;
      if (message.data.isRunning) {
        setStatus(`采集中: ${message.data.currentKeyword} | +${message.data.newCount}`, 'running');
      }
    }
    
    if (message.action === 'SCRAPE_COMPLETE') {
      elements.btnStart.disabled = false;
      elements.btnStop.disabled = true;
      setStatus(`采集完成！共 ${message.data.totalCollected} 件商品`, '');
    }
    
    if (message.action === 'PUSH_COMPLETE') {
      if (message.data.success) {
        setStatus(`推送成功！共 ${message.data.count} 件`, '');
      } else {
        setStatus(`推送失败: ${message.data.error}`, 'error');
      }
    }
  });

  // 关键词数量实时更新
  elements.keywords.addEventListener('input', () => {
    const count = elements.keywords.value.trim().split('\n').filter(k => k.trim()).length;
    elements.keywordCount.textContent = count;
  });

  // ==================== 辅助函数 ====================
  
  function updateUI(state) {
    elements.totalCount.textContent = state.totalCollected || 0;
    
    if (state.isRunning) {
      elements.btnStart.disabled = true;
      elements.btnStop.disabled = false;
      setStatus(`采集中: ${state.currentKeyword}`, 'running');
    } else {
      elements.btnStart.disabled = false;
      elements.btnStop.disabled = true;
    }
    
    if (state.keywords && state.keywords.length > 0) {
      elements.keywordCount.textContent = state.keywords.length;
    }
  }
  
  function setStatus(text, type) {
    elements.statusBar.textContent = text;
    elements.statusBar.className = 'status-bar' + (type ? ` ${type}` : '');
  }
});
