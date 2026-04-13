"""
初始化資料庫 Schema
執行方式: python scripts/init_db.py
"""
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(message)s")

from state_manager import StateManager

if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "bot_state.db"
    db = StateManager(db_path=db_path)
    print(f"資料庫初始化完成：{db_path}")
