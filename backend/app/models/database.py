"""
OZON爬虫系统 - 数据库模型定义
包含：商品表、关键词表、任务表、调度表、系统配置表
"""
import datetime
from sqlalchemy import (
    create_engine, Column, Integer, BigInteger, String, Text, Float,
    Boolean, DateTime, JSON, Index, UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.core.config import DATABASE_URL

engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Product(Base):
    """OZON商品数据表"""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(BigInteger, index=True, nullable=False, comment="OZON商品SKU")
    title = Column(Text, comment="商品标题")
    product_url = Column(Text, comment="商品链接")
    image_url = Column(Text, comment="主图链接")
    price = Column(Float, comment="当前售价（卢布）")
    original_price = Column(Float, comment="原价（卢布）")
    discount_percent = Column(Float, comment="折扣百分比")
    category = Column(Text, comment="商品类目")
    brand = Column(String(255), comment="品牌")
    rating = Column(Float, comment="评分")
    review_count = Column(Integer, comment="评论数")
    monthly_sales = Column(Integer, default=0, comment="月销量（BCS数据）")
    weekly_sales = Column(Integer, default=0, comment="周销量（BCS数据）")
    gmv_rub = Column(Float, default=0, comment="月销售额（卢布）")
    paid_promo_days = Column(Integer, default=0, comment="付费推广参与天数（28天内）")
    ad_cost_ratio = Column(Float, default=0, comment="广告费用占比（%）")
    is_promoted = Column(Boolean, default=False, comment="是否有付费推广标记")
    seller_type = Column(String(100), comment="卖家类型")
    seller_name = Column(String(255), comment="卖家名称")
    seller_id = Column(String(100), comment="卖家ID")
    creation_date = Column(DateTime, comment="商品创建时间")
    followers_count = Column(Integer, default=0, comment="被跟卖数量")
    follower_min_price = Column(Float, comment="跟卖最低价")
    follower_min_url = Column(Text, comment="跟卖最低价链接")
    length_cm = Column(Float, comment="长度（厘米）")
    width_cm = Column(Float, comment="宽度（厘米）")
    height_cm = Column(Float, comment="高度（厘米）")
    weight_g = Column(Float, comment="重量（克）")
    volume_liters = Column(Float, comment="体积（升）")
    delivery_info = Column(String(255), comment="配送信息")
    stock_quantity = Column(Integer, comment="当前库存数量")
    pdd_purchase_price = Column(Float, comment="拼多多采购价（人民币）")
    profit_rub = Column(Float, comment="利润（卢布）")
    profit_cny = Column(Float, comment="利润（人民币）")
    keyword = Column(String(255), index=True, comment="采集关键词")
    task_id = Column(Integer, index=True, comment="关联任务ID")
    extra_data = Column(JSON, comment="额外数据（JSON）")
    last_scraped_at = Column(DateTime, default=datetime.datetime.utcnow, comment="最后采集时间")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow, comment="更新时间")

    __table_args__ = (
        Index("idx_sku_keyword", "sku", "keyword"),
        UniqueConstraint("sku", "keyword", name="uq_sku_keyword"),
    )


class Keyword(Base):
    """关键词管理表"""
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(255), nullable=False, unique=True, comment="搜索关键词")
    keyword_zh = Column(String(255), comment="中文翻译")
    is_active = Column(Boolean, default=True, comment="是否启用")
    priority = Column(Integer, default=0, comment="采集优先级（越大越优先）")
    max_products = Column(Integer, default=5000, comment="最大采集商品数")
    total_scraped = Column(Integer, default=0, comment="已采集商品总数")
    last_scraped_at = Column(DateTime, comment="上次采集时间")
    schedule_cron = Column(String(100), comment="定时采集Cron表达式")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)


class ScrapeTask(Base):
    """采集任务表"""
    __tablename__ = "scrape_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword_id = Column(Integer, index=True, comment="关联关键词ID")
    keyword = Column(String(255), comment="搜索关键词")
    task_type = Column(String(50), default="search",
                       comment="任务类型: search/detail")
    status = Column(String(50), default="pending",
                    comment="任务状态: pending/running/completed/failed/cancelled")
    max_products = Column(Integer, default=5000, comment="目标采集数量")
    scraped_count = Column(Integer, default=0, comment="已采集数量")
    error_message = Column(Text, comment="错误信息")
    started_at = Column(DateTime, comment="开始时间")
    completed_at = Column(DateTime, comment="完成时间")
    duration_seconds = Column(Integer, comment="耗时（秒）")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)


class TaskSchedule(Base):
    """定时任务配置表"""
    __tablename__ = "task_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, comment="调度名称")
    keywords = Column(JSON, comment="关键词列表")
    cron_expression = Column(String(100), comment="Cron表达式")
    max_products_per_keyword = Column(Integer, default=5000, comment="每个关键词最大采集数")
    switch_mode = Column(String(50), default="sequential",
                         comment="切换模式: sequential/timer/quantity")
    switch_interval_minutes = Column(Integer, default=30, comment="定时切换间隔（分钟）")
    switch_quantity = Column(Integer, default=1000, comment="定量切换阈值")
    is_active = Column(Boolean, default=True, comment="是否启用")
    last_run_at = Column(DateTime, comment="上次执行时间")
    next_run_at = Column(DateTime, comment="下次执行时间")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)


class SystemConfig(Base):
    """系统配置表"""
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(255), unique=True, nullable=False, comment="配置键")
    value = Column(Text, comment="配置值")
    description = Column(Text, comment="配置描述")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)


def init_db():
    """初始化数据库，创建所有表"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
