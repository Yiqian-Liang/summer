"""
Authentication module for user management.
Handles registration, login, sessions, and password hashing.
"""

import hashlib
import secrets
import logging
from typing import Optional, Dict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# In-memory session store (for simplicity; use Redis in production)
sessions: Dict[str, Dict] = {}

# Session configuration
SESSION_DURATION_HOURS = 24 * 7  # 7 days


def hash_password(password: str) -> str:
    """Hash a password using SHA-256 with salt."""
    salt = secrets.token_hex(16)
    password_hash = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}:{password_hash}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against its stored hash."""
    try:
        salt, password_hash = stored_hash.split(":")
        return hashlib.sha256(f"{salt}{password}".encode()).hexdigest() == password_hash
    except:
        return False


def create_session(user_id: int, user_data: Dict) -> str:
    """Create a new session for a user. Returns session token."""
    token = secrets.token_urlsafe(32)
    sessions[token] = {
        "user_id": user_id,
        "user": user_data,
        "created_at": datetime.now(),
        "expires_at": datetime.now() + timedelta(hours=SESSION_DURATION_HOURS),
    }
    logger.info(f"Created session for user {user_id}")
    return token


def get_session(token: str) -> Optional[Dict]:
    """Get session data from token. Returns None if invalid or expired."""
    if not token or token not in sessions:
        return None
    
    session = sessions[token]
    
    # Check expiration
    if datetime.now() > session["expires_at"]:
        del sessions[token]
        return None
    
    return session


def destroy_session(token: str) -> bool:
    """Destroy a session (logout)."""
    if token in sessions:
        del sessions[token]
        return True
    return False


def get_current_user(token: str) -> Optional[Dict]:
    """Get current user from session token."""
    session = get_session(token)
    if session:
        return session.get("user")
    return None


def validate_email(email: str) -> bool:
    """Basic email validation."""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def validate_username(username: str) -> tuple[bool, str]:
    """Validate username. Returns (is_valid, error_message)."""
    if not username:
        return False, "用户名不能为空"
    if len(username) < 3:
        return False, "用户名至少3个字符"
    if len(username) > 20:
        return False, "用户名最多20个字符"
    if not username.replace("_", "").replace("-", "").isalnum():
        return False, "用户名只能包含字母、数字、下划线和连字符"
    return True, ""


def validate_password(password: str) -> tuple[bool, str]:
    """Validate password strength. Returns (is_valid, error_message)."""
    if not password:
        return False, "密码不能为空"
    if len(password) < 6:
        return False, "密码至少6个字符"
    if len(password) > 128:
        return False, "密码太长"
    return True, ""


# Interest/Profile options for onboarding
EDUCATION_LEVELS = [
    {"id": "undergraduate", "label": "本科在读", "label_en": "Undergraduate"},
    {"id": "masters", "label": "硕士在读", "label_en": "Master's Student"},
    {"id": "phd", "label": "博士在读", "label_en": "PhD Student"},
    {"id": "graduated", "label": "已毕业", "label_en": "Graduated"},
]

SCHOOL_TIERS = [
    {"id": "985", "label": "985/C9", "label_en": "Top Tier (985/C9)"},
    {"id": "211", "label": "211", "label_en": "211 University"},
    {"id": "overseas", "label": "海外本科", "label_en": "Overseas Undergraduate"},
    {"id": "other", "label": "其他", "label_en": "Other"},
]

GRADES = [
    {"id": "freshman", "label": "大一", "label_en": "Freshman"},
    {"id": "sophomore", "label": "大二", "label_en": "Sophomore"},
    {"id": "junior", "label": "大三", "label_en": "Junior"},
    {"id": "senior", "label": "大四", "label_en": "Senior"},
    {"id": "grad1", "label": "研一", "label_en": "1st Year Grad"},
    {"id": "grad2", "label": "研二", "label_en": "2nd Year Grad"},
    {"id": "grad3", "label": "研三+", "label_en": "3rd+ Year Grad"},
]

TARGET_TYPES = [
    {"id": "summer_research", "label": "🔬 暑期科研", "label_en": "Summer Research"},
    {"id": "phd", "label": "🎓 PhD申请", "label_en": "PhD Application"},
    {"id": "intern", "label": "💼 研究实习", "label_en": "Research Internship"},
    {"id": "visiting", "label": "📚 访问学者", "label_en": "Visiting Scholar"},
]

MAJORS = [
    {"id": "cs", "label": "计算机科学", "label_en": "Computer Science"},
    {"id": "ee", "label": "电子工程", "label_en": "Electrical Engineering"},
    {"id": "math", "label": "数学", "label_en": "Mathematics"},
    {"id": "physics", "label": "物理", "label_en": "Physics"},
    {"id": "biology", "label": "生物", "label_en": "Biology"},
    {"id": "chemistry", "label": "化学", "label_en": "Chemistry"},
    {"id": "me", "label": "机械工程", "label_en": "Mechanical Engineering"},
    {"id": "stats", "label": "统计学", "label_en": "Statistics"},
    {"id": "economics", "label": "经济学", "label_en": "Economics"},
    {"id": "psychology", "label": "心理学", "label_en": "Psychology"},
    {"id": "other", "label": "其他", "label_en": "Other"},
]

RESEARCH_FIELDS = [
    {"id": "nlp", "label": "NLP / 自然语言处理", "label_en": "NLP"},
    {"id": "cv", "label": "CV / 计算机视觉", "label_en": "Computer Vision"},
    {"id": "ml", "label": "ML / 机器学习", "label_en": "Machine Learning"},
    {"id": "robotics", "label": "机器人", "label_en": "Robotics"},
    {"id": "systems", "label": "系统", "label_en": "Systems"},
    {"id": "theory", "label": "理论", "label_en": "Theory"},
    {"id": "hci", "label": "人机交互", "label_en": "HCI"},
    {"id": "security", "label": "安全", "label_en": "Security"},
    {"id": "graphics", "label": "图形学", "label_en": "Graphics"},
    {"id": "database", "label": "数据库", "label_en": "Database"},
    {"id": "networks", "label": "网络", "label_en": "Networks"},
    {"id": "ai_general", "label": "AI通用", "label_en": "General AI"},
    {"id": "bioinformatics", "label": "生物信息学", "label_en": "Bioinformatics"},
    {"id": "quantum", "label": "量子计算", "label_en": "Quantum Computing"},
    {"id": "other", "label": "其他", "label_en": "Other"},
]

REGIONS = [
    {"id": "north_america", "label": "🇺🇸 北美", "label_en": "North America"},
    {"id": "europe", "label": "🇪🇺 欧洲", "label_en": "Europe"},
    {"id": "asia", "label": "🌏 亚洲", "label_en": "Asia"},
    {"id": "any", "label": "🌍 不限", "label_en": "Any"},
]


def get_onboarding_options() -> Dict:
    """Get all options for onboarding page."""
    return {
        "education_levels": EDUCATION_LEVELS,
        "school_tiers": SCHOOL_TIERS,
        "grades": GRADES,
        "target_types": TARGET_TYPES,
        "majors": MAJORS,
        "research_fields": RESEARCH_FIELDS,
        "regions": REGIONS,
    }
