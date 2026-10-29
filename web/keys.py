"""Ключи приложения aiohttp (AppKey) — типизированный доступ к settings/db/sessions."""

from aiohttp import web

from config import Settings
from db.database import Database

SETTINGS = web.AppKey("settings", Settings)
DB = web.AppKey("db", Database)
SESSIONS = web.AppKey("sessions", dict)
