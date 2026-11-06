"""수동 크롤링 명령.

예) python crawl.py --pages 3                 # 전체 게시판, 게시판마다 3페이지
    python crawl.py --pages 5 --board 721     # 장학공지만
    python crawl.py --pages 3 --refresh       # 이미 저장된 글도 다시 수집
"""
import argparse
import json
import logging

from app.config import BOARDS, CRAWLABLE_BOARDS
from app.crawler import crawl

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser()
    p.add_argument("--pages", type=int, default=3, help="게시판마다 목록 몇 페이지까지 수집할지 (1페이지=10개)")
    p.add_argument("--refresh", action="store_true", help="이미 저장된 글도 다시 수집")
    p.add_argument("--board", type=int, action="append", choices=CRAWLABLE_BOARDS,
                   help="특정 게시판만 (여러 번 지정 가능). " + ", ".join(f"{k}={v['name']}" for k, v in BOARDS.items() if k in CRAWLABLE_BOARDS))
    args = p.parse_args()
    print(json.dumps(crawl(args.pages, args.refresh, args.board), ensure_ascii=False, indent=2))
