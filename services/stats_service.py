from sqlalchemy import select,func
from database import async_session_maker
from models import User,Search,Result,TelegramSource,WebSource,ErrorLog
async def get_dashboard_stats():
    async with async_session_maker() as s:
        vals={}
        for k,m in [('users',User),('searches',Search),('results',Result),('errors',ErrorLog),('telegram_sources',TelegramSource),('web_sources',WebSource)]: vals[k]=await s.scalar(select(func.count()).select_from(m))
        vals['active_telegram']=await s.scalar(select(func.count()).select_from(TelegramSource).where(TelegramSource.enabled.is_(True)))
        vals['active_web']=await s.scalar(select(func.count()).select_from(WebSource).where(WebSource.enabled.is_(True)))
        return vals
