# -*- coding: utf-8 -*-
"""通用工具函数"""
import re
import logging
from typing import Optional
from datetime import datetime
from app_paths import data_dir

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(data_dir() / "trainer.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def validate_stock_code(code: str) -> bool:
    """验证股票代码格式（6位数字）"""
    return bool(re.match(r'^\d{6}$', code.strip()))


def safe_float_to_str(value: float, decimals: int = 2) -> str:
    """安全地将浮点数转换为字符串，避免科学计数法"""
    return f"{value:.{decimals}f}"


def parse_date_robust(date_str: str) -> Optional[datetime]:
    """尝试多种格式解析日期字符串"""
    patterns = [
        '%Y-%m-%d', '%Y/%m/%d', '%Y%m%d',
        '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S',
        '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M'
    ]
    for fmt in patterns:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None
