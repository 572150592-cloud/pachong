"""
OZON爬虫系统 - 启动脚本
"""
import os
import sys
import uvicorn
from pathlib import Path

# 添加项目路径
project_dir = Path(__file__).resolve().parent
backend_dir = project_dir / "backend"
sys.path.insert(0, str(backend_dir))

# 确保必要目录存在
(project_dir / "data").mkdir(exist_ok=True)
(project_dir / "data" / "exports").mkdir(exist_ok=True)
(project_dir / "logs").mkdir(exist_ok=True)

# 初始化数据库
from app.models.database import init_db
init_db()
print("数据库初始化完成")

# 启动FastAPI应用
if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
