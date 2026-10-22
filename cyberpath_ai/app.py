"""Bot composition and polling entry point."""

import logging

from aiogram import Bot, Dispatcher

from cyberpath_ai.access import AllowlistMiddleware
from cyberpath_ai.academy.content import load_catalog
from cyberpath_ai.academy.adaptive import AdaptiveStore
from cyberpath_ai.academy.storage import AcademyStore
from cyberpath_ai.academy.mission import MissionStore
from cyberpath_ai.academy.resources import load_resources
from cyberpath_ai.academy.resource_recommendations import ResourceStore, ResourceRecommender
from cyberpath_ai.ai.explainer import AIExplainer
from cyberpath_ai.config import Settings
from cyberpath_ai.handlers.menu import create_router as create_menu_router
from cyberpath_ai.handlers.academy import AcademyViews, create_router as create_academy_router
from cyberpath_ai.handlers.news import NewsViewStore, create_router as create_news_router
from cyberpath_ai.handlers.settings import SettingsViewStore, create_router as create_settings_router
from cyberpath_ai.handlers.mission import MissionViews, create_router as create_mission_router
from cyberpath_ai.handlers.commands import create_router as create_command_router, register_native_menu
from cyberpath_ai.handlers.resources import ResourceViews, create_router as create_resource_router
from cyberpath_ai.news.service import NewsService
from cyberpath_ai.news.storage import NewsStore
from cyberpath_ai.scheduler import DailyScheduler, DigestService, PreferenceStore


def configure_news_logging() -> None:
    for logger_name in ("cyberpath_ai.news", "cyberpath_ai.ai", "cyberpath_ai.scheduler"):
        news_logger = logging.getLogger(logger_name)
        if not news_logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            news_logger.addHandler(handler)
        news_logger.setLevel(logging.INFO)
        news_logger.propagate = False


def create_dispatcher(settings: Settings, news_service: NewsService | None = None,
                      ai_explainer: AIExplainer | None = None,
                      preferences: PreferenceStore | None = None) -> Dispatcher:
    dispatcher = Dispatcher()
    preference_store = preferences or PreferenceStore(settings.news_db_path,
                                                      settings.default_timezone,
                                                      settings.daily_digest_hour)
    service = news_service or NewsService(settings)
    digest = DigestService(settings, preference_store, service)
    scheduler = DailyScheduler(settings, preference_store, digest)
    dispatcher.update.outer_middleware(
        AllowlistMiddleware(settings.allowed_user_ids, preference_store.touch))
    dispatcher["news_service"] = service
    service_store = getattr(service, "store", None)
    dispatcher["news_store"] = (service_store if isinstance(service_store, NewsStore)
                                else NewsStore(settings.news_db_path))
    dispatcher["settings"] = settings
    dispatcher["news_views"] = NewsViewStore()
    dispatcher["ai_explainer"] = ai_explainer or AIExplainer(settings)
    dispatcher["preferences"] = preference_store
    dispatcher["digest_service"] = digest
    dispatcher["daily_scheduler"] = scheduler
    dispatcher["settings_views"] = SettingsViewStore()
    catalog = load_catalog()
    def academy_timezone(user_id: int) -> str:
        preference = preference_store.get(user_id)
        return preference.timezone if preference else settings.default_timezone

    dispatcher["catalog"] = catalog
    dispatcher["academy_store"] = AcademyStore(settings.news_db_path, catalog,
                                                settings.mastery_threshold_percent)
    dispatcher["adaptive_store"] = AdaptiveStore(
        settings.news_db_path, catalog,
        mastery_threshold_percent=settings.mastery_threshold_percent,
        success_intervals=settings.review_success_intervals_days,
        failure_interval=settings.review_failure_interval_days,
        session_size=settings.review_session_size,
        timezone_for_user=academy_timezone)
    dispatcher["mission_store"] = MissionStore(settings.news_db_path, catalog,
                                                 dispatcher["adaptive_store"], academy_timezone)
    dispatcher["mission_views"] = MissionViews()
    dispatcher["academy_views"] = AcademyViews()
    dispatcher["resource_catalog"] = load_resources(catalog)
    dispatcher["resource_store"] = ResourceStore(settings.news_db_path)
    dispatcher["resource_recommender"] = ResourceRecommender(
        catalog, dispatcher["resource_catalog"], dispatcher["academy_store"],
        dispatcher["adaptive_store"], dispatcher["resource_store"])
    dispatcher["resource_views"] = ResourceViews()
    dispatcher.include_router(create_command_router())
    dispatcher.include_router(create_news_router())
    dispatcher.include_router(create_settings_router())
    dispatcher.include_router(create_menu_router())
    dispatcher.include_router(create_mission_router())
    dispatcher.include_router(create_academy_router())
    dispatcher.include_router(create_resource_router())
    return dispatcher


async def run() -> None:
    configure_news_logging()
    settings = Settings.from_env()
    dispatcher = create_dispatcher(settings)
    async with Bot(token=settings.telegram_bot_token) as bot:
        await register_native_menu(bot)
        scheduler = dispatcher["daily_scheduler"]
        scheduler.start(bot)
        try:
            await dispatcher.start_polling(bot)
        finally:
            await scheduler.stop()
