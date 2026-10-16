from __future__ import annotations
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Index
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, index=True, nullable=False)
    username = Column(String(255))
    full_name = Column(String(255))
    study_level = Column(String(100))
    major = Column(String(255))
    is_blocked = Column(Boolean, default=False, nullable=False)
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_active = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    search_count = Column(Integer, default=0, nullable=False)
    upload_count = Column(Integer, default=0, nullable=False)


class Search(Base):
    __tablename__ = "searches"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    query = Column(Text, nullable=False)
    mode = Column(String(30), default="fast")
    scope = Column(String(100))
    year = Column(String(10))
    section = Column(String(30), default="university", index=True)
    result_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Result(Base):
    __tablename__ = "results"
    id = Column(Integer, primary_key=True)
    search_id = Column(Integer, ForeignKey("searches.id", ondelete="CASCADE"), index=True, nullable=False)
    source_name = Column(String(255), nullable=False)
    source_type = Column(String(50), default="web")
    title = Column(Text, nullable=False)
    url = Column(Text)
    author = Column(Text)
    year = Column(String(10))
    snippet = Column(Text)
    file_url = Column(Text)
    is_downloadable = Column(Boolean, default=False)
    tg_chat = Column(String(255))
    tg_message_id = Column(Integer)
    metadata_json = Column(Text)
    rank_score = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LibraryItem(Base):
    __tablename__ = "library_items"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    result_id = Column(Integer, ForeignKey("results.id", ondelete="CASCADE"), index=True)
    folder = Column(String(100), default="عام")
    note = Column(Text)
    is_starred = Column(Boolean, default=False)
    saved_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("uq_library_user_result", "user_id", "result_id", unique=True),)


class TelegramSource(Base):
    __tablename__ = "telegram_sources"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    username = Column(String(255), unique=True, index=True, nullable=False)
    url = Column(Text)
    source_type = Column(String(100), default="books")
    priority = Column(Integer, default=1)
    enabled = Column(Boolean, default=True)
    fast_enabled = Column(Boolean, default=True)
    deep_enabled = Column(Boolean, default=True)
    total_searches = Column(Integer, default=0)
    total_results = Column(Integer, default=0)
    last_error = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class WebSource(Base):
    __tablename__ = "web_sources"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    base_url = Column(Text, nullable=False)
    search_url = Column(Text, nullable=False)
    source_type = Column(String(100), default="general")
    method = Column(String(20), default="GET")
    priority = Column(Integer, default=2)
    enabled = Column(Boolean, default=True)
    fast_enabled = Column(Boolean, default=True)
    deep_enabled = Column(Boolean, default=True)
    search_param = Column(String(100), default="q")
    headers_json = Column(Text)
    result_mapping_json = Column(Text)
    total_searches = Column(Integer, default=0)
    total_results = Column(Integer, default=0)
    last_error = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ErrorLog(Base):
    __tablename__ = "error_logs"
    id = Column(Integer, primary_key=True)
    level = Column(String(20), default="ERROR")
    component = Column(String(100))
    message = Column(Text, nullable=False)
    user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class FollowedTopic(Base):
    __tablename__ = "followed_topics"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    topic = Column(String(255), nullable=False)
    keywords_json = Column(Text, default="[]")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class StudentProfile(Base):
    __tablename__ = "student_profiles"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    path = Column(String(50), nullable=False)
    stage = Column(String(100))
    grade = Column(String(100))
    branch = Column(String(100))
    department = Column(String(150))
    subject = Column(String(150))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SchoolContent(Base):
    __tablename__ = "school_contents"
    id = Column(Integer, primary_key=True)
    title = Column(String(500), nullable=False)
    content_type = Column(String(100), nullable=False)
    url = Column(Text)
    text_content = Column(Text)
    photo_id = Column(Text)
    video_id = Column(Text)
    file_id = Column(Text)
    mime_type = Column(String(100))
    storage_path = Column(Text)
    teacher = Column(String(255))
    stage = Column(String(100))
    grade = Column(String(100))
    branch = Column(String(100))
    department = Column(String(150))
    subject = Column(String(150))
    shared = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (Index("idx_school_content_type", "content_type"), Index("idx_school_content_subject", "subject"),)


class SchoolPlacement(Base):
    __tablename__ = "school_placements"
    id = Column(Integer, primary_key=True)
    content_id = Column(Integer, ForeignKey("school_contents.id", ondelete="CASCADE"), nullable=False, index=True)
    stage = Column(String(100), default="")
    grade = Column(String(100), default="")
    branch = Column(String(100), default="")
    department = Column(String(150), default="")
    subject = Column(String(150), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("uq_school_placement", "content_id", "stage", "grade", "branch", "department", "subject", unique=True),)


class UniversityResource(Base):
    __tablename__ = "university_resources"
    id = Column(Integer, primary_key=True)
    title = Column(String(500), nullable=False)
    url = Column(Text, nullable=False)
    author = Column(Text)
    year = Column(String(10))
    resource_type = Column(String(100), default="reference")
    description = Column(Text)
    major = Column(String(255))
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (Index("idx_uni_resource_type", "resource_type"), Index("idx_uni_resource_major", "major"),)


class UniversityProject(Base):
    __tablename__ = "university_projects"
    id = Column(Integer, primary_key=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    url = Column(Text)
    major = Column(String(255))
    year = Column(String(10))
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (Index("idx_uni_project_major", "major"),)


class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, index=True, nullable=False)
    is_owner = Column(Boolean, default=False, nullable=False)
    added_by = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True, index=True)
    telegram_id = Column(Integer, index=True, nullable=False)
    event_type = Column(String(80), nullable=False, index=True)
    metadata_json = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class StudySession(Base):
    __tablename__ = "study_sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    section = Column(String(30), default="university")
    source_title = Column(String(500), nullable=False)
    source_text = Column(Text, nullable=False)
    source_content_id = Column(Integer, nullable=True)
    source_content_type = Column(String(100))
    current_mode = Column(String(50))
    context_json = Column(Text, default="{}")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
