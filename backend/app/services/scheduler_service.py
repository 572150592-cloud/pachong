"""
定时调度服务
管理定时采集任务的调度和执行
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.models.database import SessionLocal, TaskSchedule, ScrapeTask

logger = logging.getLogger(__name__)


class SchedulerService:
    """定时任务调度服务"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.jobs: Dict[int, str] = {}  # schedule_id -> job_id

    def start(self):
        """启动调度器"""
        try:
            if not self.scheduler.running:
                self.scheduler.start()
                logger.info("定时调度器已启动")

                # 加载已有的定时任务
                self._load_existing_schedules()
        except Exception as e:
            logger.error(f"启动调度器失败: {e}")

    def stop(self):
        """停止调度器"""
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
                logger.info("定时调度器已停止")
        except Exception as e:
            logger.error(f"停止调度器失败: {e}")

    def _load_existing_schedules(self):
        """加载数据库中已有的定时任务"""
        db = SessionLocal()
        try:
            schedules = db.query(TaskSchedule).filter(
                TaskSchedule.is_active == True
            ).all()
            for schedule in schedules:
                self.add_schedule(schedule)
            logger.info(f"已加载 {len(schedules)} 个定时任务")
        except Exception as e:
            logger.error(f"加载定时任务失败: {e}")
        finally:
            db.close()

    def add_schedule(self, schedule: TaskSchedule):
        """添加定时任务"""
        try:
            job_id = f"schedule_{schedule.id}"

            # 解析Cron表达式
            cron_parts = schedule.cron_expression.split()
            if len(cron_parts) == 5:
                trigger = CronTrigger(
                    minute=cron_parts[0],
                    hour=cron_parts[1],
                    day=cron_parts[2],
                    month=cron_parts[3],
                    day_of_week=cron_parts[4],
                )
            elif len(cron_parts) == 6:
                trigger = CronTrigger(
                    second=cron_parts[0],
                    minute=cron_parts[1],
                    hour=cron_parts[2],
                    day=cron_parts[3],
                    month=cron_parts[4],
                    day_of_week=cron_parts[5],
                )
            else:
                logger.error(f"无效的Cron表达式: {schedule.cron_expression}")
                return

            job = self.scheduler.add_job(
                self._execute_schedule,
                trigger=trigger,
                id=job_id,
                args=[schedule.id],
                replace_existing=True,
                name=schedule.name,
            )

            self.jobs[schedule.id] = job_id
            logger.info(f"定时任务已注册: {schedule.name} ({schedule.cron_expression})")

        except Exception as e:
            logger.error(f"添加定时任务失败: {e}")

    def remove_schedule(self, schedule_id: int):
        """移除定时任务"""
        job_id = self.jobs.get(schedule_id)
        if job_id:
            try:
                self.scheduler.remove_job(job_id)
                del self.jobs[schedule_id]
                logger.info(f"定时任务已移除: {schedule_id}")
            except Exception as e:
                logger.error(f"移除定时任务失败: {e}")

    async def _execute_schedule(self, schedule_id: int):
        """执行定时任务"""
        from app.services.scraper_service import ScraperService

        db = SessionLocal()
        try:
            schedule = db.query(TaskSchedule).filter(
                TaskSchedule.id == schedule_id
            ).first()

            if not schedule or not schedule.is_active:
                return

            logger.info(f"定时任务开始执行: {schedule.name}")

            keywords = schedule.keywords or []
            if not keywords:
                logger.warning(f"定时任务 {schedule.name} 没有关键词")
                return

            # 创建任务记录
            task_ids = []
            for kw in keywords:
                task = ScrapeTask(
                    keyword=kw,
                    status="pending",
                    max_products=schedule.max_products_per_keyword,
                )
                db.add(task)
            db.commit()

            task_ids = [t.id for t in db.query(ScrapeTask).filter(
                ScrapeTask.status == "pending"
            ).order_by(ScrapeTask.created_at.desc()).limit(len(keywords)).all()]

            # 更新调度记录
            schedule.last_run_at = datetime.utcnow()
            db.commit()

            # 执行采集
            service = ScraperService()
            await service.run_scrape_task(
                keywords=keywords,
                task_ids=task_ids,
                max_products=schedule.max_products_per_keyword,
                switch_mode=schedule.switch_mode,
                switch_interval=schedule.switch_interval_minutes,
                switch_quantity=schedule.switch_quantity,
            )

            logger.info(f"定时任务执行完成: {schedule.name}")

        except Exception as e:
            logger.error(f"定时任务执行失败: {e}", exc_info=True)
        finally:
            db.close()
