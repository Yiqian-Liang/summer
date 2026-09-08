"""
Utilities for search query expansion, source detection, and external shortcuts.

The app needs to work in two modes:
1. Local/offline search over the SQLite cache.
2. Live search through Gemini/SerpApi when API keys are configured.

These helpers keep platform names and bilingual query aliases consistent across
both paths.
"""

import re
import urllib.parse
from functools import lru_cache
from typing import Dict, List, Optional


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "for", "from", "in", "into",
    "of", "on", "or", "the", "to", "with", "position", "positions",
    "opportunity", "opportunities", "program", "programs",
    "我想找", "帮我找", "帮我", "寻找", "找一下", "有没有", "相关", "方向", "项目", "机会",
}


CHINESE_PLATFORM_SOURCES = {"xiaohongshu", "wechat", "zhihu", "bilibili", "weibo", "tieba"}


PLATFORM_INTENT_TERMS = {
    "university": ['"summer research"', '"research internship"', '"lab openings"', '"undergraduate research"'],
    "professor_site": ['"join my lab"', '"prospective students"', '"interest form"', '"application form"', "openings"],
    "nsf": ['"NSF REU"', "REU", '"summer research"', "undergraduate"],
    "pathways": ['"summer research"', "REU", "internship", "undergraduate"],
    "academicjobs": ["PhD", "doctoral", '"research assistant"', "postdoc"],
    "euraxess": ["PhD", "doctoral", "researcher", "internship"],
    "findaphd": ["PhD", "studentship", "doctoral"],
    "nature_careers": ["PhD", "researcher", "postdoc", "internship"],
    "jobs_ac_uk": ["PhD", "studentship", "researcher", "internship"],
    "mitacs": ['"Mitacs Globalink"', "internship", "undergraduate"],
    "daad": ['"DAAD RISE"', "internship", "undergraduate"],
    "cnrs": ["PhD", "doctorat", "internship", "stage"],
    "inria": ["PhD", "internship", "stage", "researcher"],
    "max_planck": ["PhD", "doctoral", "internship", '"student assistant"'],
    "linkedin": ['"research intern"', '"PhD student"', '"summer research"', "hiring"],
    "twitter": ['"PhD position"', '"looking for students"', '"research intern"', "hiring"],
    "xiaohongshu": ["暑研", "科研实习", "博士招生", "课题组招生", "导师招生"],
    "wechat": ["暑研", "科研实习", "博士招生", "课题组招生", "导师招生"],
    "zhihu": ["暑研", "科研实习", "博士", "导师评价", "申请"],
    "bilibili": ["暑研", "科研", "博士申请", "申请经验"],
    "weibo": ["暑研", "科研实习", "博士招生", "招生", "申请"],
    "tieba": ["暑研", "科研实习", "博士", "招生", "申请"],
    "scholar": ['"recent paper"', '"research group"', "lab"],
    "discord": ["research", "phd", "study", "internship"],
}


OVERBROAD_EXPANDED_TERMS = {
    "science", "materials", "research", "student", "students", "program", "programs",
    "opportunity", "opportunities", "internship", "internships",
}


SOURCE_ALIASES = {
    "community": "community",
    "站内招募": "community",
    "": None,
    "all": None,
    "google_scholar": "scholar",
    "scholar": "scholar",
    "linkedin": "linkedin",
    "领英": "linkedin",
    "xiaohongshu": "xiaohongshu",
    "rednote": "xiaohongshu",
    "小红书": "xiaohongshu",
    "wechat": "wechat",
    "weixin": "wechat",
    "微信": "wechat",
    "twitter": "twitter",
    "x": "twitter",
    "x.com": "twitter",
    "nsf": "nsf",
    "reu": "nsf",
    "instagram": "instagram",
    "zhihu": "zhihu",
    "知乎": "zhihu",
    "bilibili": "bilibili",
    "b站": "bilibili",
    "weibo": "weibo",
    "wb": "weibo",
    "微博": "weibo",
    "tieba": "tieba",
    "贴吧": "tieba",
    "tiktok": "tiktok",
    "facebook": "facebook",
    "discord": "discord",
    "university": "university",
    "official": "university",
    "lab": "university",
    "professor": "professor_site",
    "professor_site": "professor_site",
    "homepage": "professor_site",
    "faculty": "professor_site",
    "interest_form": "professor_site",
    "prospective_students": "professor_site",
    "academicjobs": "academicjobs",
    "academicjobsonline": "academicjobs",
    "academic_jobs": "academicjobs",
    "euraxess": "euraxess",
    "findaphd": "findaphd",
    "nature": "nature_careers",
    "nature_careers": "nature_careers",
    "jobs_ac_uk": "jobs_ac_uk",
    "jobs.ac.uk": "jobs_ac_uk",
    "pathways": "pathways",
    "pathwaystoscience": "pathways",
    "mitacs": "mitacs",
    "daad": "daad",
    "cnrs": "cnrs",
    "inria": "inria",
    "max_planck": "max_planck",
    "maxplanck": "max_planck",
}


SOURCE_PATTERNS = [
    ("scholar", ["google scholar", "scholar.google"]),
    ("linkedin", ["linkedin.com", "linkedin"]),
    ("xiaohongshu", ["xiaohongshu.com", "小红书", "rednote", "xiaohongshu"]),
    ("wechat", ["mp.weixin.qq.com", "weixin", "wechat", "微信公众号", "微信"]),
    ("twitter", ["twitter.com", "x.com", "twitter", "posts / x"]),
    ("instagram", ["instagram.com", "instagram"]),
    ("zhihu", ["zhihu.com", "知乎", "zhihu"]),
    ("bilibili", ["bilibili.com", "bilibili", "b站"]),
    ("weibo", ["weibo.com", "weibo.cn", "微博", "weibo"]),
    ("tieba", ["tieba.baidu.com", "贴吧", "tieba"]),
    ("tiktok", ["tiktok.com", "tiktok"]),
    ("facebook", ["facebook.com", "facebook"]),
    ("discord", ["discord.com", "discord.gg", "discord"]),
    ("nsf", ["nsf.gov", "nsf reu", "nsf"]),
    ("pathways", ["pathwaystoscience.org", "pathways to science"]),
    ("academicjobs", ["academicjobsonline.org", "academic jobs online"]),
    ("euraxess", ["euraxess.ec.europa.eu", "euraxess"]),
    ("findaphd", ["findaphd.com", "findaphd"]),
    ("nature_careers", ["nature.com/naturecareers", "nature careers"]),
    ("jobs_ac_uk", ["jobs.ac.uk"]),
    ("mitacs", ["mitacs.ca", "globalink"]),
    ("daad", ["daad.de", "daad rise"]),
    ("cnrs", ["cnrs.fr", "emploi.cnrs.fr"]),
    ("inria", ["inria.fr", "jobs.inria.fr"]),
    ("max_planck", ["mpg.de", "max planck"]),
    ("professor_site", [
        "personal website", "professor homepage", "faculty homepage",
        "prospective students", "join my lab", "join the lab",
        "interest form", "prospective student form",
    ]),
    ("university", [
        ".edu", ".ac.uk", ".edu.au", "university website", "official university",
        "department website", "research institute", "university", "college",
    ]),
]


PLATFORM_SEARCHES = [
    {
        "key": "university",
        "label": "University/Lab sites",
        "query": '(site:.edu OR site:.ac.uk OR site:.edu.au OR site:.ca) ("lab" OR "research group" OR "openings") "{query}"',
    },
    {
        "key": "professor_site",
        "label": "Professor homepages/forms",
        "query": '("{query}") ("professor" OR "faculty" OR "PI") ("prospective students" OR "join my lab" OR "join the lab" OR "interest form" OR "application form")',
    },
    {
        "key": "nsf",
        "label": "NSF REU",
        "query": '(site:nsf.gov/crssprgm/reu OR "NSF REU") "{query}"',
    },
    {
        "key": "pathways",
        "label": "PathwaysToScience",
        "query": 'site:pathwaystoscience.org "{query}" ("summer research" OR REU OR internship)',
    },
    {
        "key": "academicjobs",
        "label": "AcademicJobsOnline",
        "query": 'site:academicjobsonline.org "{query}" ("PhD" OR "research assistant" OR postdoc)',
    },
    {
        "key": "euraxess",
        "label": "Euraxess",
        "query": 'site:euraxess.ec.europa.eu/jobs "{query}" ("PhD" OR doctoral OR researcher)',
    },
    {
        "key": "findaphd",
        "label": "FindAPhD",
        "query": 'site:findaphd.com "{query}" ("PhD" OR studentship)',
    },
    {
        "key": "nature_careers",
        "label": "Nature Careers",
        "query": 'site:nature.com/naturecareers "{query}" ("PhD" OR researcher OR postdoc)',
    },
    {
        "key": "jobs_ac_uk",
        "label": "jobs.ac.uk",
        "query": 'site:jobs.ac.uk "{query}" ("PhD" OR researcher OR studentship)',
    },
    {
        "key": "mitacs",
        "label": "Mitacs Globalink",
        "query": 'site:mitacs.ca/globalink "{query}" OR "Mitacs Globalink" "{query}"',
    },
    {
        "key": "daad",
        "label": "DAAD RISE",
        "query": 'site:daad.de/rise "{query}" OR "DAAD RISE" "{query}"',
    },
    {
        "key": "cnrs",
        "label": "CNRS",
        "query": '(site:emploi.cnrs.fr OR site:cnrs.fr) "{query}" ("PhD" OR doctorat OR internship)',
    },
    {
        "key": "inria",
        "label": "INRIA",
        "query": '(site:jobs.inria.fr OR site:inria.fr) "{query}" ("PhD" OR internship)',
    },
    {
        "key": "max_planck",
        "label": "Max Planck",
        "query": 'site:mpg.de "{query}" ("PhD" OR internship OR doctoral)',
    },
    {
        "key": "linkedin",
        "label": "LinkedIn",
        "query": '(site:linkedin.com/jobs OR site:linkedin.com/posts) "{query}" "research"',
    },
    {
        "key": "twitter",
        "label": "X / Twitter",
        "query": '(site:x.com OR site:twitter.com) "{query}" ("PhD position" OR hiring OR "summer research")',
    },
    {
        "key": "xiaohongshu",
        "label": "小红书 / Rednote",
        "query": 'site:xiaohongshu.com "{query}" (暑研 OR 科研 OR 招生)',
    },
    {
        "key": "wechat",
        "label": "微信公众号",
        "query": 'site:mp.weixin.qq.com "{query}" (暑研 OR 科研实习 OR 招生)',
    },
    {
        "key": "scholar",
        "label": "Google Scholar",
        "query": 'site:scholar.google.com "{query}"',
    },
    {
        "key": "zhihu",
        "label": "知乎",
        "query": '(site:zhihu.com OR site:zhuanlan.zhihu.com) "{query}" (暑研 OR 科研 OR 博士)',
    },
    {
        "key": "bilibili",
        "label": "Bilibili",
        "query": 'site:bilibili.com/video "{query}" (暑研 OR 科研 OR 申请)',
    },
    {
        "key": "weibo",
        "label": "微博 / Weibo",
        "query": '(site:weibo.com OR site:m.weibo.cn) "{query}" (暑研 OR 科研实习 OR 招生)',
    },
    {
        "key": "tieba",
        "label": "百度贴吧",
        "query": 'site:tieba.baidu.com "{query}" (暑研 OR 科研实习 OR 招生)',
    },
    {
        "key": "discord",
        "label": "Discord",
        "query": '(site:discord.com/invite OR site:discord.gg) "{query}" (research OR phd OR study)',
    },
]


QUERY_EXPANSIONS: Dict[str, List[str]] = {
    "暑研": ["summer research", "summer research internship", "research intern", "undergraduate research", "REU"],
    "暑期科研": ["summer research", "summer research program", "REU"],
    "科研实习": ["research intern", "research internship", "summer intern"],
    "套磁": ["prospective student", "phd position", "openings", "looking for students"],
    "招生": ["hiring", "openings", "recruiting", "looking for students"],
    "博士": ["phd", "ph.d", "doctoral", "博士生"],
    "硕士": ["master", "msc", "ms student", "graduate student"],
    "本科": ["undergraduate", "bachelor", "REU"],
    "计算机科学": ["computer science", "computer engineering"],
    "暑期实习": ["summer internship", "summer intern"],
    "全奖": ["fully funded", "funded", "stipend", "scholarship"],
    "大语言模型": ["llm", "large language model", "language model", "generative ai"],
    "自然语言处理": ["nlp", "natural language processing", "language model", "text mining"],
    "计算机视觉": ["computer vision", "cv", "image", "video", "segmentation"],
    "机器学习": ["machine learning", "ml", "deep learning", "neural network"],
    "深度学习": ["deep learning", "machine learning", "neural network"],
    "强化学习": ["reinforcement learning", "rl", "decision making"],
    "机器人": ["robotics", "robot", "autonomous", "control"],
    "系统": ["systems", "distributed systems", "operating systems", "cloud"],
    "安全": ["security", "privacy", "cryptography"],
    "生物信息": ["bioinformatics", "computational biology", "genomics"],
    "生物": ["biology", "biological sciences", "life sciences", "molecular biology"],
    "医学": ["medical", "medicine", "biomedical", "clinical research", "health"],
    "化学": ["chemistry", "chemical", "molecular", "catalysis"],
    "物理": ["physics", "physical sciences", "astrophysics", "condensed matter"],
    "数学": ["mathematics", "math", "applied mathematics", "statistics"],
    "统计": ["statistics", "statistical", "biostatistics", "data science"],
    "经济学": ["economics", "economic", "econometrics", "finance"],
    "金融": ["finance", "financial", "quantitative finance", "economics"],
    "心理学": ["psychology", "cognitive science", "behavioral science"],
    "神经科学": ["neuroscience", "neural", "brain", "cognitive science"],
    "环境科学": ["environmental science", "climate", "sustainability", "ecology"],
    "气候": ["climate", "climate change", "environmental science"],
    "能源": ["energy", "sustainable energy", "renewable energy", "battery"],
    "土木": ["civil engineering", "infrastructure", "structural engineering"],
    "机械": ["mechanical engineering", "mechanics", "manufacturing"],
    "电子": ["electrical engineering", "electronics", "circuits"],
    "电气": ["electrical engineering", "power systems", "electronics"],
    "航空航天": ["aerospace engineering", "aeronautics", "space"],
    "公共政策": ["public policy", "policy research", "governance"],
    "社会学": ["sociology", "social science", "social research"],
    "政治学": ["political science", "politics", "international relations"],
    "历史": ["history", "historical", "digital humanities"],
    "教育": ["education", "learning sciences", "educational research"],
    "材料科学": [
        "materials science",
        "material science",
        "materials",
        "advanced materials",
        "nanomaterials",
        "polymers",
        "metallurgy",
        "solid-state",
        "quantum materials",
    ],
    "材料": [
        "materials",
        "materials science",
        "advanced materials",
        "nanomaterials",
        "polymers",
        "metallurgy",
    ],
    "纳米材料": ["nanomaterials", "nano materials", "nanotechnology", "materials science"],
    "高分子": ["polymers", "polymer science", "soft matter", "materials science"],
    "金属材料": ["metallurgy", "metallic materials", "alloys", "materials science"],
    "量子材料": ["quantum materials", "condensed matter", "solid-state", "materials science"],
    "半导体材料": ["semiconductor materials", "semiconductors", "solid-state", "materials science"],
    "美国": ["usa", "united states", "america"],
    "英国": ["uk", "united kingdom", "britain"],
    "欧洲": ["europe", "germany", "france", "switzerland", "netherlands"],
    "法国": ["france", "french", "cnrs", "inria"],
    "德国": ["germany", "german", "max planck", "tum"],
    "加拿大": ["canada", "canadian"],
    "新加坡": ["singapore", "nus", "ntu"],
    "香港": ["hong kong", "hku", "hkust", "cuhk"],
    "哈佛": ["harvard", "harvard university"],
    "斯坦福": ["stanford", "stanford university"],
    "麻省理工": ["mit", "massachusetts institute of technology"],
    "伯克利": ["berkeley", "uc berkeley"],
    "剑桥": ["cambridge", "university of cambridge"],
    "牛津": ["oxford", "university of oxford"],
    "nlp": ["natural language processing", "language model", "llm", "自然语言处理"],
    "llm": ["large language model", "language model", "generative ai", "大语言模型"],
    "cv": ["computer vision", "image", "video", "计算机视觉"],
    "ml": ["machine learning", "deep learning", "机器学习"],
    "ai": ["artificial intelligence", "machine learning", "人工智能"],
    "biology": ["生物", "biological sciences", "life sciences"],
    "chemistry": ["化学", "chemical", "molecular"],
    "physics": ["物理", "physical sciences", "astrophysics"],
    "mathematics": ["数学", "math", "applied mathematics"],
    "statistics": ["统计", "statistical", "biostatistics"],
    "economics": ["经济学", "econometrics", "finance"],
    "psychology": ["心理学", "cognitive science", "behavioral science"],
    "neuroscience": ["神经科学", "brain", "cognitive science"],
    "environmental science": ["环境科学", "climate", "sustainability"],
    "electrical engineering": ["电子", "电气", "circuits"],
    "mechanical engineering": ["机械", "mechanics", "manufacturing"],
    "civil engineering": ["土木", "infrastructure", "structural engineering"],
    "aerospace engineering": ["航空航天", "aeronautics", "space"],
    "materials science": ["材料科学", "materials", "nanomaterials", "polymers", "metallurgy"],
    "material science": ["材料科学", "materials science", "materials"],
    "materials": ["材料", "材料科学", "materials science", "advanced materials"],
    "nanomaterials": ["纳米材料", "materials science"],
    "polymers": ["高分子", "polymer science", "materials science"],
    "reu": ["summer research", "undergraduate research", "NSF REU", "暑研"],
    "phd": ["doctoral", "ph.d", "博士", "博士生"],
    "intern": ["internship", "research intern", "summer intern", "实习"],
}


def _term_pattern(term: str) -> str:
    escaped = re.escape(term).replace(r"\ ", r"[\s\-\u2010-\u2015]+")
    # Accept ordinary plurals without treating acronyms such as AI as substrings.
    last_word = term.rsplit(" ", 1)[-1]
    if re.fullmatch(r"[a-z]{4,}", last_word) and not last_word.endswith("s"):
        escaped += "s?"
    return rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"


def text_contains_term(text: str, term: str) -> bool:
    """Match short Latin terms on token boundaries and CJK terms by substring."""
    text = re.sub(r"\s+", " ", str(text or "")).lower()
    term = re.sub(r"\s+", " ", str(term or "").strip()).lower()
    if not text or not term:
        return False
    if _contains_chinese(term):
        return term in text

    return bool(re.search(_term_pattern(term), text))


@lru_cache(maxsize=256)
def _query_tokens(query: str) -> List[str]:
    """Tokenize a mixed-language query while preserving known academic phrases."""
    query = re.sub(r"\s+", " ", str(query or "").strip())
    if not query:
        return []

    known = set(QUERY_EXPANSIONS) | STOPWORDS
    for aliases in QUERY_EXPANSIONS.values():
        known.update(aliases)

    candidates = []
    lowered = query.lower()
    for phrase in sorted(known, key=lambda value: len(value), reverse=True):
        phrase = re.sub(r"\s+", " ", phrase.strip())
        if len(phrase) < 2:
            continue
        pattern = re.escape(phrase.lower()) if _contains_chinese(phrase) else _term_pattern(phrase.lower())
        for match in re.finditer(pattern, lowered):
            candidates.append((match.start(), match.end(), query[match.start():match.end()]))

    # Prefer the longest phrase when aliases overlap (for example, "materials
    # science" over "materials").
    selected = []
    occupied = [False] * len(query)
    for start, end, value in sorted(candidates, key=lambda item: (-(item[1] - item[0]), item[0])):
        if any(occupied[start:end]):
            continue
        selected.append((start, end, value))
        for index in range(start, end):
            occupied[index] = True

    pieces = []
    cursor = 0
    for start, end, value in sorted(selected):
        if cursor < start:
            pieces.extend(
                (match.start() + cursor, match.end() + cursor, match.group(0))
                for match in re.finditer(
                    r"[a-zA-Z0-9][a-zA-Z0-9+.#/-]*|[\u4e00-\u9fff]{2,}",
                    query[cursor:start],
                )
            )
        pieces.append((start, end, value))
        cursor = end
    if cursor < len(query):
        pieces.extend(
            (match.start() + cursor, match.end() + cursor, match.group(0))
            for match in re.finditer(
                r"[a-zA-Z0-9][a-zA-Z0-9+.#/-]*|[\u4e00-\u9fff]{2,}",
                query[cursor:],
            )
        )

    tokens = []
    seen = set()
    for _, _, value in sorted(pieces):
        value = value.strip()
        lower_value = value.lower()
        if not value or lower_value in STOPWORDS or lower_value in seen:
            continue
        seen.add(lower_value)
        tokens.append(value)
    return tokens


def expand_concept_terms(term: str, max_terms: int = 16) -> List[str]:
    """Expand one query concept without mixing it with neighboring concepts."""
    if not term:
        return []

    terms: List[str] = []

    def add(value: str):
        value = re.sub(r"\s+", " ", str(value).strip())
        if value and value.lower() not in {existing.lower() for existing in terms}:
            terms.append(value)

    normalized = re.sub(r"\s+", " ", term.strip())
    add(normalized)

    lookup = re.sub(r"[\-\u2010-\u2015]+", " ", normalized.lower())
    known = {value.lower() for key, aliases in QUERY_EXPANSIONS.items() for value in [key, *aliases]}
    if lookup not in known and lookup.endswith("s") and lookup[:-1] in known:
        lookup = lookup[:-1]
    add(lookup)

    # Expand only the group directly selected by the user's phrase. Transitive
    # expansion turns "France" into every country listed under "Europe", or a
    # PhD query into a master's query.
    key_match = next(
        ((key, aliases) for key, aliases in QUERY_EXPANSIONS.items() if key.lower() == lookup),
        None,
    )
    if key_match:
        groups = [key_match]
    else:
        alias_matches = []
        for key, aliases in QUERY_EXPANSIONS.items():
            for index, alias in enumerate(aliases):
                if alias.lower() == lookup:
                    alias_matches.append((index, key, aliases))
        best_index = min((item[0] for item in alias_matches), default=None)
        groups = [
            (key, aliases)
            for index, key, aliases in alias_matches
            if index == best_index
        ]

    for key, aliases in groups:
        add(key)
        for value in aliases:
            add(value)

    return terms[:max_terms]


def build_query_concepts(query: str) -> List[List[str]]:
    """
    Split a user query into concepts that should all be represented.

    Examples:
    - "法国 博士" => [["法国", "france", ...], ["博士", "phd", ...]]
    - "暑研 NLP" => [["暑研", "summer research", ...], ["NLP", ...]]
    - "材料科学" => one broad concept with material-science aliases.
    """
    if not query:
        return []

    concepts = [expand_concept_terms(token) for token in _query_tokens(query)]
    return [concept for concept in concepts if concept]


def canonicalize_source_url(url: str) -> str:
    """Unwrap Google redirects and remove common tracking parameters."""
    value = str(url or "").strip()
    if not value:
        return ""

    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return value

    host = (parsed.hostname or "").lower()
    query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if host in {"google.com", "www.google.com"} and parsed.path == "/url":
        params = dict(query_items)
        target = params.get("q") or params.get("url")
        if target:
            return canonicalize_source_url(target)

    tracking_names = {
        "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src",
        "source", "spm", "spm_id_from", "trk", "trkinfo",
    }
    filtered_query = []
    for key, item_value in query_items:
        lower_key = key.lower()
        if lower_key.startswith("utm_") or lower_key in tracking_names:
            continue
        # Xiaohongshu xsec values are access tokens, not disposable analytics.
        filtered_query.append((key, item_value))

    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(),
        netloc,
        path,
        urllib.parse.urlencode(filtered_query, doseq=True),
        "",
    ))


def normalize_source_filter(source: Optional[str]) -> Optional[str]:
    """Normalize source query params into canonical source_type values."""
    if source is None:
        return None
    return SOURCE_ALIASES.get(str(source).strip().lower(), str(source).strip().lower() or None)


def detect_source_type(source_text: str = "", url: str = "") -> str:
    """Infer canonical source_type from a source label and/or URL."""
    canonical_url = canonicalize_source_url(url)
    parsed = urllib.parse.urlparse(canonical_url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()

    host_rules = [
        ("linkedin", "linkedin.com"),
        ("xiaohongshu", "xiaohongshu.com"),
        ("xiaohongshu", "rednote.com"),
        ("wechat", "mp.weixin.qq.com"),
        ("wechat", "weixin.sogou.com"),
        ("twitter", "x.com"),
        ("twitter", "twitter.com"),
        ("zhihu", "zhihu.com"),
        ("bilibili", "bilibili.com"),
        ("weibo", "weibo.com"),
        ("weibo", "weibo.cn"),
        ("tieba", "tieba.baidu.com"),
        ("nsf", "nsf.gov"),
        ("pathways", "pathwaystoscience.org"),
        ("academicjobs", "academicjobsonline.org"),
        ("euraxess", "euraxess.ec.europa.eu"),
        ("findaphd", "findaphd.com"),
        ("jobs_ac_uk", "jobs.ac.uk"),
        ("mitacs", "mitacs.ca"),
        ("daad", "daad.de"),
        ("cnrs", "cnrs.fr"),
        ("inria", "inria.fr"),
        ("max_planck", "mpg.de"),
    ]
    for source_type, domain in host_rules:
        if host == domain or host.endswith(f".{domain}"):
            return source_type
    if "scholar.google." in host:
        return "scholar"

    haystack = f"{source_text or ''} {canonical_url} {host} {path}".lower()
    for source_type, patterns in SOURCE_PATTERNS:
        if any(pattern.lower() in haystack for pattern in patterns):
            return source_type
    return "other"


def expand_query_terms(query: str, max_terms: int = 48) -> List[str]:
    """
    Expand a search query with bilingual aliases and related academic terms.

    The first returned item is always the original query, which lets callers
    give exact phrase matches a stronger score.
    """
    if not query:
        return []

    terms: List[str] = []

    def add(term: str):
        term = re.sub(r"\s+", " ", str(term).strip())
        if not term:
            return
        lower_terms = {t.lower() for t in terms}
        if term.lower() not in lower_terms:
            terms.append(term)

    normalized = re.sub(r"\s+", " ", query.strip())
    add(normalized)

    for concept in build_query_concepts(normalized):
        for term in concept:
            add(term)

    return terms[:max_terms]


def _contains_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _strip_google_quotes(term: str) -> str:
    term = re.sub(r"\s+", " ", str(term).strip())
    if len(term) >= 2 and term[0] == '"' and term[-1] == '"':
        return term[1:-1]
    return term


def _quote_google_term(term: str) -> str:
    term = _strip_google_quotes(term)
    term = term.replace('"', "")
    return f'"{term}"'


def _add_unique(values: List[str], value: str):
    value = re.sub(r"\s+", " ", str(value).strip())
    if value and value.lower() not in {existing.lower() for existing in values}:
        values.append(value)


def _platform_definition(platform_key: Optional[str]) -> Optional[Dict[str, str]]:
    if not platform_key:
        return None
    normalized = normalize_source_filter(platform_key) or platform_key
    for platform in PLATFORM_SEARCHES:
        if platform["key"] == normalized:
            return platform
    return None


def build_platform_query_terms(query: str, platform_key: Optional[str] = None, max_terms: int = 7) -> List[str]:
    """
    Pick compact search terms for a live platform query.

    Local DB search can use many aliases, but Google/SerpApi queries become noisy
    if we stuff every synonym into one request. This keeps enough bilingual recall
    without turning a subject query into a generic "research" search.
    """
    if not query:
        return []

    normalized_source = normalize_source_filter(platform_key) or platform_key
    all_terms: List[str] = []
    for concept in build_query_concepts(query):
        for term in concept:
            _add_unique(all_terms, term)

    chinese_terms = [
        term for term in all_terms
        if _contains_chinese(term) and term.lower() not in OVERBROAD_EXPANDED_TERMS
    ]
    english_terms = [
        term for term in all_terms
        if not _contains_chinese(term)
        and term.lower() not in STOPWORDS
        and term.lower() not in OVERBROAD_EXPANDED_TERMS
        and len(term.strip()) >= 2
    ]

    selected: List[str] = []
    _add_unique(selected, query)

    if normalized_source in CHINESE_PLATFORM_SOURCES:
        for term in chinese_terms[:4]:
            _add_unique(selected, term)
        for term in english_terms[:3]:
            _add_unique(selected, term)
    else:
        for term in english_terms[:5]:
            _add_unique(selected, term)
        for term in chinese_terms[:2]:
            _add_unique(selected, term)

    return selected[:max_terms]


def build_platform_query_clause(query: str, platform_key: Optional[str] = None, max_terms: int = 7) -> str:
    normalized_source = normalize_source_filter(platform_key) or platform_key
    groups = []
    for concept in build_query_concepts(query):
        original = concept[0]
        chinese = [term for term in concept if _contains_chinese(term)]
        english = [
            term for term in concept
            if not _contains_chinese(term)
            and term.lower() not in STOPWORDS
            and term.lower() not in OVERBROAD_EXPANDED_TERMS
        ]
        selected = []
        _add_unique(selected, original)
        ordered = [*chinese, *english] if normalized_source in CHINESE_PLATFORM_SOURCES else [*english, *chinese]
        for term in ordered:
            _add_unique(selected, term)
            if len(selected) >= min(max_terms, 4):
                break
        if selected:
            groups.append("(" + " OR ".join(_quote_google_term(term) for term in selected) + ")")
    return " ".join(groups) if groups else _quote_google_term(query)


def build_platform_search_query(query: str, platform_key: Optional[str] = None) -> str:
    """
    Build the actual Google/SerpApi query for one platform using expanded terms.

    The returned string is intended for Google Search, not for display as a
    fabricated source. It only changes how we search; results still need real URLs.
    """
    normalized_source = normalize_source_filter(platform_key) or platform_key
    platform = _platform_definition(normalized_source)
    clause = build_platform_query_clause(query, normalized_source)
    expanded_query = f"({clause})"

    if platform:
        template = platform["query"]
        return template.replace('"{query}"', expanded_query).replace("{query}", expanded_query)

    return expanded_query


def build_live_search_queries(query: str, platform_key: Optional[str] = None, max_queries: int = 8) -> List[str]:
    """
    Build a small set of expanded free-text queries for Gemini/direct search.

    SerpApi platform searches use build_platform_search_query(). Gemini benefits
    from plain-language variants instead of one large boolean query.
    """
    if not query:
        return []

    normalized_source = normalize_source_filter(platform_key) or platform_key
    concepts = build_query_concepts(query)
    if not concepts or max_queries <= 0:
        return []
    variants = [query]
    # Every free-text variant must retain each constraint (country, subject,
    # degree). Sending a single expanded alias loses the other requirements.
    for prefer_chinese in (normalized_source in CHINESE_PLATFORM_SOURCES, False, True):
        terms = []
        for concept in concepts:
            terms.append(next((
                term for term in concept
                if _contains_chinese(term) == prefer_chinese
                and term.lower() not in OVERBROAD_EXPANDED_TERMS
            ), concept[0]))
        _add_unique(variants, " ".join(terms))
    intents = PLATFORM_INTENT_TERMS.get(normalized_source or "", [])
    if not intents:
        if normalized_source in CHINESE_PLATFORM_SOURCES:
            intents = ["暑研", "科研实习", "博士招生"]
        else:
            intents = ['"summer research"', '"research internship"', '"PhD position"', '"lab openings"']

    queries: List[str] = []
    _add_unique(queries, query)

    for variant in variants:
        _add_unique(queries, variant)

    for variant in variants:
        clean_term = _strip_google_quotes(variant)
        for intent in intents[:3]:
            clean_intent = _strip_google_quotes(intent)
            _add_unique(queries, f"{clean_term} {clean_intent}")
            if len(queries) >= max_queries:
                return queries[:max_queries]

    return queries[:max_queries]


def build_external_search_links(
    query: str,
    source_filter: Optional[str] = None,
    max_links: int = 8,
) -> List[Dict[str, str]]:
    """
    Build Google search shortcut links for platforms that require login/API keys.

    These links are UI hints, not scraped results. They keep the product useful
    when live API credentials are not configured locally.
    """
    if not query:
        return []

    normalized_source = normalize_source_filter(source_filter)
    platform_by_key = {platform["key"]: platform for platform in PLATFORM_SEARCHES}
    if normalized_source:
        platforms = [platform_by_key[normalized_source]] if normalized_source in platform_by_key else []
    else:
        preferred = ["university", "professor_site", "nsf", "academicjobs", "euraxess", "findaphd"]
        if _contains_chinese(query):
            preferred.extend(["xiaohongshu", "wechat", "zhihu", "bilibili"])
        else:
            preferred.extend(["linkedin", "twitter"])
        platforms = [platform_by_key[key] for key in preferred if key in platform_by_key][:max_links]

    links = []
    for platform in platforms:
        google_query = build_platform_search_query(query, platform["key"])
        links.append({
            "key": platform["key"],
            "label": platform["label"],
            "url": "https://www.google.com/search?q=" + urllib.parse.quote(google_query),
            "query": google_query,
        })
    return links
