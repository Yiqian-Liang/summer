import json
import re
import unittest
from pathlib import Path

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.auth import get_onboarding_options
from app.i18n import I18n, LANGUAGES


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / "app" / "templates"
        self.templates = Environment(loader=FileSystemLoader(self.root), autoescape=select_autoescape())

    def test_all_templates_compile_and_registration_renders(self):
        for path in self.root.rglob("*.html"):
            with self.subTest(template=path.name):
                self.templates.get_template(str(path.relative_to(self.root)))
        for language in LANGUAGES:
            rendered = self.templates.get_template("auth/register.html").render(i18n=I18n(language), languages=LANGUAGES)
            self.assertIn('action="/register"', rendered)

    def test_saved_preferences_are_selected(self):
        profile = {"target_types": ["phd"], "majors": ["cs"], "research_fields": ["ml"], "region_preferences": ["europe"]}
        for language in LANGUAGES:
            rendered = self.templates.get_template("auth/onboarding.html").render(
                profile=profile, options=get_onboarding_options(), i18n=I18n(language), languages=LANGUAGES,
            )
            soup = BeautifulSoup(rendered, "html.parser")
            selected = {(item["name"], item["value"]) for item in soup.select('input[type="checkbox"][checked]')}
            self.assertEqual(selected, {(key, value[0]) for key, value in profile.items()})

    def test_detail_script_preserves_untrusted_text(self):
        text = 'Research "title" & </script>\nends with \\'
        rendered = self.templates.get_template("detail.html").render(
            opportunity={"id": "test", "title": text, "description": text, "professor_name": text, "source_type": "wechat"},
            i18n=I18n("zh"), languages=LANGUAGES,
        )
        data = re.search(r"const opportunityData = (.*?);\nconst sourceUrl", rendered, re.DOTALL).group(1)
        self.assertEqual(json.loads(data), {"profName": text, "title": text, "interests": text, "source": "wechat"})
        self.assertNotIn("</script>", data)
        self.assertIn('href="/search?source=wechat"', rendered)


if __name__ == "__main__":
    unittest.main()
