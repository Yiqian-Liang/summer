"""
Internationalization (i18n) module for bilingual support.
Supports Chinese (zh) and English (en).
"""

from typing import Dict

# Available languages
LANGUAGES = {
    "zh": "中文",
    "en": "English",
}

DEFAULT_LANGUAGE = "zh"

# Translation dictionary
TRANSLATIONS: Dict[str, Dict[str, str]] = {
    # Navigation
    "nav.home": {"zh": "首页", "en": "Home"},
    "nav.search": {"zh": "搜索", "en": "Search"},
    "nav.saved": {"zh": "收藏", "en": "Saved"},
    "nav.login": {"zh": "登录", "en": "Login"},
    "nav.register": {"zh": "注册", "en": "Register"},
    "nav.logout": {"zh": "退出登录", "en": "Logout"},
    "nav.settings": {"zh": "偏好设置", "en": "Settings"},
    
    # Site
    "site.name": {"zh": "暑研通", "en": "Summer Research Hub"},
    "site.tagline": {"zh": "发现你的理想暑研 & PhD 机会", "en": "Find Your Perfect Summer Research & PhD"},
    "site.description": {"zh": "AI 驱动的全球学术机会匹配。连接教授、发现机会、加速你的科研之路。", 
                        "en": "AI-driven matching for global academic opportunities. Connect with professors, find funding, and accelerate your research career."},
    
    # Home page
    "home.welcome_back": {"zh": "欢迎回来，{username}！", "en": "Welcome back, {username}!"},
    "home.discover": {"zh": "发现适合你的机会", "en": "Discover opportunities for you"},
    "home.personalized": {"zh": "基于你的兴趣为你推荐", "en": "Personalized based on your interests"},
    "home.edit_prefs": {"zh": "修改偏好", "en": "Edit preferences"},
    "home.complete_profile": {"zh": "完善你的偏好设置，获得个性化推荐", "en": "Complete your preferences for personalized recommendations"},
    "home.search_placeholder": {"zh": "搜索你感兴趣的方向（如 NLP、Computer Vision、机器学习）...", 
                               "en": "Search by interest (e.g., NLP, Computer Vision, Machine Learning)..."},
    "home.search_btn": {"zh": "搜索", "en": "Search"},
    "home.recommended_searches": {"zh": "为你推荐的搜索：", "en": "Recommended searches for you:"},
    "home.opportunities": {"zh": "机会总数", "en": "Opportunities"},
    "home.platforms": {"zh": "数据平台", "en": "Platforms"},
    "home.ai_matching": {"zh": "智能匹配", "en": "AI Matching"},
    "home.latest": {"zh": "最新机会", "en": "Latest Opportunities"},
    "home.details": {"zh": "详情", "en": "Details"},
    "home.apply": {"zh": "申请", "en": "Apply"},
    "home.no_opportunities": {"zh": "暂无机会", "en": "No opportunities yet"},
    "home.try_search": {"zh": "尝试搜索", "en": "Try a Search"},
    "home.collect_data": {"zh": "收集数据", "en": "Collect Data"},
    "home.all_sources": {"zh": "所有来源", "en": "All Sources"},
    
    # Features
    "feature.ai_title": {"zh": "看得懂的个人推荐", "en": "Explainable recommendations"},
    "feature.ai_desc": {"zh": "结合简历、偏好和收藏寻找共同方向，展示推荐理由，也能调整或关闭行为推荐。", "en": "Find shared topics using your resume, preferences and bookmarks, with visible reasons and activity controls."},
    "feature.global_title": {"zh": "跨来源找机会", "en": "Explore across sources"},
    "feature.global_desc": {"zh": "合并公开项目和站内招募，保留原文入口；具体覆盖取决于已接入的数据来源。", "en": "Browse public opportunities and community recruitment with original links. Coverage depends on connected sources."},
    "feature.email_title": {"zh": "从发现到联系", "en": "From discovery to contact"},
    "feature.email_desc": {"zh": "用免费模板准备草稿和附件；连接邮箱后核对发送，AI 起草按需选用。", "en": "Prepare drafts and attachments with free templates, then review and send through a connected mailbox. AI drafting is optional."},

    # Search
    "search.title": {"zh": "搜索", "en": "Search"},
    "search.results_for": {"zh": "搜索结果：\"{query}\"", "en": "Search Results for \"{query}\""},
    "search.results_count": {"zh": "{count} 条结果来自多平台", "en": "{count} results from multiple platforms"},
    "search.no_results": {"zh": "没有找到相关结果", "en": "No results found"},
    "search.try_keywords": {"zh": "尝试不同的关键词", "en": "Try different keywords"},
    "search.score": {"zh": "匹配度", "en": "Match"},
    "search.high_quality": {"zh": "高质量", "en": "High Quality"},
    
    # Auth
    "auth.login_title": {"zh": "登录你的账号", "en": "Sign in to your account"},
    "auth.no_account": {"zh": "还没有账号？", "en": "Don't have an account?"},
    "auth.register_now": {"zh": "立即注册", "en": "Register now"},
    "auth.email": {"zh": "邮箱", "en": "Email"},
    "auth.password": {"zh": "密码", "en": "Password"},
    "auth.remember_me": {"zh": "记住我", "en": "Remember me"},
    "auth.forgot_password": {"zh": "忘记密码？", "en": "Forgot password?"},
    "auth.login_btn": {"zh": "登录", "en": "Sign in"},
    "auth.or": {"zh": "或者", "en": "Or"},
    "auth.register_title": {"zh": "创建新账号", "en": "Create a new account"},
    "auth.have_account": {"zh": "已有账号？", "en": "Already have an account?"},
    "auth.login_now": {"zh": "立即登录", "en": "Sign in now"},
    "auth.username": {"zh": "用户名", "en": "Username"},
    "auth.username_hint": {"zh": "3-20个字符，字母数字下划线", "en": "3-20 characters, letters, numbers, underscores"},
    "auth.password_hint": {"zh": "至少6个字符", "en": "At least 6 characters"},
    "auth.confirm_password": {"zh": "确认密码", "en": "Confirm password"},
    "auth.confirm_hint": {"zh": "再次输入密码", "en": "Enter password again"},
    "auth.agree_terms": {"zh": "我同意", "en": "I agree to the"},
    "auth.terms": {"zh": "用户协议", "en": "Terms of Service"},
    "auth.and": {"zh": "和", "en": "and"},
    "auth.privacy": {"zh": "隐私政策", "en": "Privacy Policy"},
    "auth.register_btn": {"zh": "注册", "en": "Register"},
    "auth.error_email_password": {"zh": "邮箱或密码错误", "en": "Invalid email or password"},
    "auth.forgot_title": {"zh": "找回密码", "en": "Forgot Password"},
    "auth.forgot_desc": {"zh": "输入你的邮箱，我们将发送重置链接", "en": "Enter your email and we'll send you a reset link"},
    "auth.send_reset": {"zh": "发送重置链接", "en": "Send Reset Link"},
    "auth.back_to_login": {"zh": "返回登录", "en": "Back to Login"},
    "auth.reset_email_sent": {"zh": "如果该邮箱已注册，重置链接已发送", "en": "If an account exists with this email, a reset link has been sent"},
    "auth.reset_title": {"zh": "重置密码", "en": "Reset Password"},
    "auth.new_password": {"zh": "新密码", "en": "New Password"},
    "auth.confirm_new_password": {"zh": "确认新密码", "en": "Confirm New Password"},
    "auth.reset_btn": {"zh": "重置密码", "en": "Reset Password"},
    "auth.invalid_reset_link": {"zh": "重置链接无效或已过期", "en": "Invalid or expired reset link"},
    "auth.password_reset_success": {"zh": "密码重置成功，请登录", "en": "Password reset successful. Please login."},
    
    # Onboarding
    "onboarding.title": {"zh": "告诉我们你的兴趣", "en": "Tell us your interests"},
    "onboarding.subtitle": {"zh": "我们会根据你的偏好推荐最相关的机会", "en": "We'll recommend the most relevant opportunities based on your preferences"},
    "onboarding.education": {"zh": "你的学历阶段", "en": "Your Education Level"},
    "onboarding.school_tier": {"zh": "学校层次", "en": "School Tier"},
    "onboarding.grade": {"zh": "当前年级", "en": "Current Grade"},
    "onboarding.targets": {"zh": "你的目标（可多选）", "en": "Your Goals (select multiple)"},
    "onboarding.targets_hint": {"zh": "选择你想要寻找的机会类型", "en": "Select the types of opportunities you're looking for"},
    "onboarding.majors": {"zh": "你的专业（可多选）", "en": "Your Major (select multiple)"},
    "onboarding.fields": {"zh": "感兴趣的研究方向（可多选）", "en": "Research Fields of Interest (select multiple)"},
    "onboarding.fields_hint": {"zh": "选择你想探索或已经在研究的领域", "en": "Select fields you want to explore or are already researching"},
    "onboarding.regions": {"zh": "目标地区（可多选）", "en": "Target Regions (select multiple)"},
    "onboarding.skip": {"zh": "跳过，稍后设置", "en": "Skip for now"},
    "onboarding.complete": {"zh": "完成设置，开始探索", "en": "Complete & Start Exploring"},
    
    # Saved
    "saved.title": {"zh": "我的收藏", "en": "My Saved"},
    "saved.count": {"zh": "你收藏了 {count} 个机会", "en": "You saved {count} opportunities"},
    "saved.empty": {"zh": "还没有收藏", "en": "No saved items yet"},
    "saved.empty_hint": {"zh": "浏览机会时点击收藏按钮，方便稍后查看", "en": "Click the bookmark button while browsing to save for later"},
    "saved.go_search": {"zh": "去搜索机会", "en": "Search Opportunities"},
    "saved.progress_label": {"zh": "进度", "en": "Progress"},
    "saved.progress_stage": {"zh": "进度阶段", "en": "Progress Stage"},
    "saved.progress_stage_preparing": {"zh": "准备中", "en": "Preparing"},
    "saved.progress_stage_contacted": {"zh": "已联系", "en": "Contacted"},
    "saved.progress_stage_applied": {"zh": "已申请", "en": "Applied"},
    "saved.progress_stage_replied": {"zh": "已回复", "en": "Replied"},
    "saved.progress_stage_": {"zh": "其他", "en": "Other"},
    "saved.reminder_label": {"zh": "提醒", "en": "Reminder"},
    "saved.reminder_days_label": {"zh": "提前提醒（天）", "en": "Reminder (days)"},
    "saved.days": {"zh": "天", "en": "days"},
    "saved.progress_notes": {"zh": "进度备注", "en": "Progress Notes"},
    "saved.progress_save": {"zh": "保存进度", "en": "Save Progress"},
    "saved.progress_saved": {"zh": "进度已保存", "en": "Progress saved"},
    "saved.deadline_soon": {"zh": "即将到期（剩余 {days} 天）", "en": "Deadline soon (in {days} days)"},
    "saved.deadline_overdue": {"zh": "已逾期 {days} 天", "en": "Overdue by {days} days"},
    "saved.view_details": {"zh": "查看详情", "en": "View Details"},
    "saved.saved_at": {"zh": "收藏于", "en": "Saved at"},
    "saved.unsave_confirm": {"zh": "确定要取消收藏吗？", "en": "Remove from saved?"},
    
    # Detail page
    "detail.back": {"zh": "返回", "en": "Back"},
    "detail.source": {"zh": "来源", "en": "Source"},
    "detail.professor": {"zh": "教授", "en": "Professor"},
    "detail.university": {"zh": "学校", "en": "University"},
    "detail.deadline": {"zh": "截止日期", "en": "Deadline"},
    "detail.signals": {"zh": "招生信号", "en": "Hiring Signals"},
    "detail.description": {"zh": "详细描述", "en": "Description"},
    "detail.apply_now": {"zh": "立即申请", "en": "Apply Now"},
    "detail.view_source": {"zh": "查看原文", "en": "View Source"},
    "detail.generate_email": {"zh": "生成套磁邮件", "en": "Generate Email Draft"},
    "detail.save": {"zh": "收藏", "en": "Save"},
    "detail.saved": {"zh": "已收藏", "en": "Saved"},
    
    # Common
    "common.loading": {"zh": "加载中...", "en": "Loading..."},
    "common.error": {"zh": "出错了", "en": "Error"},
    "common.success": {"zh": "成功", "en": "Success"},
    "common.cancel": {"zh": "取消", "en": "Cancel"},
    "common.confirm": {"zh": "确认", "en": "Confirm"},
    "common.save": {"zh": "保存", "en": "Save"},
    "common.delete": {"zh": "删除", "en": "Delete"},
    "common.edit": {"zh": "编辑", "en": "Edit"},
    "common.page": {"zh": "第 {current} 页，共 {total} 页", "en": "Page {current} of {total}"},
    "common.prev": {"zh": "上一页", "en": "Previous"},
    "common.next": {"zh": "下一页", "en": "Next"},
}


def get_text(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """
    Get translated text for a given key.
    Supports string formatting with **kwargs.
    """
    if key not in TRANSLATIONS:
        return key
    
    translations = TRANSLATIONS[key]
    text = translations.get(lang, translations.get(DEFAULT_LANGUAGE, key))
    
    # Format with kwargs if provided
    if kwargs:
        try:
            text = text.format(**kwargs)
        except KeyError:
            pass
    
    return text


def get_all_translations(lang: str = DEFAULT_LANGUAGE) -> Dict[str, str]:
    """Get all translations for a language as a flat dictionary."""
    result = {}
    for key, translations in TRANSLATIONS.items():
        result[key] = translations.get(lang, translations.get(DEFAULT_LANGUAGE, key))
    return result


class I18n:
    """Helper class for templates."""
    
    def __init__(self, lang: str = DEFAULT_LANGUAGE):
        self.lang = lang
        self.languages = LANGUAGES
    
    def t(self, key: str, **kwargs) -> str:
        """Translate a key."""
        return get_text(key, self.lang, **kwargs)
    
    def get_lang(self) -> str:
        return self.lang
    
    def is_zh(self) -> bool:
        return self.lang == "zh"
    
    def is_en(self) -> bool:
        return self.lang == "en"
