"""
OZON爬虫系统 - 全局配置
"""
import os
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

# 数据库配置
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/data/ozon_scraper.db")

# Redis配置（用于任务队列）
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# 爬虫配置
SCRAPER_CONFIG = {
    "max_products_per_keyword": 50000,       # 每个关键词最大采集商品数
    "scroll_pause_time": 1.5,                 # 滚动暂停时间（秒）
    "scroll_pause_time_random": 1.0,          # 随机附加暂停时间
    "page_load_timeout": 60000,               # 页面加载超时（毫秒）
    "request_delay_min": 0.5,                 # 最小请求延迟（秒）
    "request_delay_max": 2.0,                 # 最大请求延迟（秒）
    "max_retries": 3,                         # 最大重试次数
    "batch_size": 100,                        # 批量存储大小
    "headless": True,                         # 是否无头模式
    "viewport_width": 1920,                   # 浏览器视口宽度
    "viewport_height": 1080,                  # 浏览器视口高度
    "user_agents": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ],
}

# OZON URL配置
OZON_CONFIG = {
    "base_url": "https://www.ozon.ru",
    "search_url": "https://www.ozon.ru/search/?text={keyword}&from_global=true",
    "global_url": "https://www.ozon.ru/highlight/ozon-global/",
    "product_url_prefix": "https://www.ozon.ru/product/",
    "language": "zh",  # 中文版
}

# 代理配置
PROXY_CONFIG = {
    "enabled": False,
    "proxy_list": [],  # 代理IP列表
    "rotation_strategy": "round_robin",  # round_robin, random
}

# 导出配置
EXPORT_CONFIG = {
    "export_dir": str(BASE_DIR / "data" / "exports"),
    "max_export_rows": 100000,
}

# API配置
API_CONFIG = {
    "host": "0.0.0.0",
    "port": 8000,
    "debug": True,
    "cors_origins": ["*"],
}

# 汇率配置（默认值，可通过API更新）
EXCHANGE_RATES = {
    "RUB_TO_CNY": 0.074,   # 1卢布 ≈ 0.074人民币
    "CNY_TO_RUB": 13.5,    # 1人民币 ≈ 13.5卢布
    "USD_TO_CNY": 7.25,    # 1美元 ≈ 7.25人民币
    "USD_TO_RUB": 97.5,    # 1美元 ≈ 97.5卢布
}
