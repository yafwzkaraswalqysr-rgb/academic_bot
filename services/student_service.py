from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from database import async_session_maker
from models import StudentProfile

async def save_student_profile(user_id:int, *, path:str, stage:str='', grade:str='', branch:str='', department:str='', subject:str=''):
    async with async_session_maker() as s:
        row=await s.scalar(select(StudentProfile).where(StudentProfile.user_id==user_id))
        if row is None:
            row=StudentProfile(user_id=user_id,path=path)
            s.add(row)
            try:
                await s.flush()
            except IntegrityError:
                # Concurrent onboarding updates may create the one-to-one
                # profile first. Re-read the winner instead of leaking a 500.
                await s.rollback()
                row = await s.scalar(select(StudentProfile).where(StudentProfile.user_id==user_id))
                if row is None:
                    raise
        row.path=path; row.stage=stage; row.grade=grade; row.branch=branch; row.department=department; row.subject=subject
        await s.commit()
        return row
