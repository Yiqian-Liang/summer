"""Manual live-search smoke test (not run during test discovery)."""

import logging

from dotenv import load_dotenv


def main():
    load_dotenv()
    from app.scrapers import AcademicScraper

    logging.basicConfig(level=logging.INFO)
    print(AcademicScraper().search_all_sources("哈佛"))


if __name__ == "__main__":
    main()
