# -*- coding: utf-8 -*-
"""配置文件，存放常量、默认参数"""

# 默认参数
DEFAULT_INITIAL_CAPITAL = 1000000.0
DEFAULT_FEE_RATE = 0.003

# 绘图参数
ZOOM_STEP = 5
MIN_DISPLAY_COUNT = 5
KLINE_WIDTH = 0.6
VOLUME_WIDTH = 0.4
UP_COLOR = '#FF4500'
DOWN_COLOR = '#00FF00'
MA_COLORS = {
    5: '#FFFFFF',
    10: '#FFFF00',
    20: '#9932CC',
    60: '#00FF00'
}

# 文件编码尝试顺序
FILE_ENCODINGS = ['gbk', 'gb2312', 'utf-8', 'gb18030']

# 股票代码正则
STOCK_CODE_PATTERN = r'^\d{6}$'