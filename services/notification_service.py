import json
from sqlalchemy import select
from database import async_session_maker
from models import FollowedTopic,User
async def check_followed_topics(new_results):
    async with async_session_maker() as session:
        topics=(await session.execute(select(FollowedTopic).where(FollowedTopic.enabled.is_(True)))).scalars().all(); out=[]
        for topic in topics:
            try: keys=json.loads(topic.keywords_json or "[]")
            except Exception: keys=[topic.topic]
            for r in new_results:
                title=(r.get("title") or "").lower()
                if any(str(k).lower() in title for k in keys):
                    user=await session.get(User,topic.user_id)
                    if user:out.append({"user_id":user.telegram_id,"topic":topic.topic,"result":r})
                    break
        return out
async def notify_user(bot,telegram_id,message):
    try: await bot.send_message(telegram_id,message); return True
    except Exception:return False
