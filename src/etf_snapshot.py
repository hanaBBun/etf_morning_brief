"""평일 장 마감 ETF 전체 시세를 다음 날 브리핑용으로 고정 저장."""
from __future__ import annotations
import logging
from .krx import save_closing_etf_snapshot
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s", datefmt="%H:%M:%S")
if __name__ == "__main__":
    save_closing_etf_snapshot()
