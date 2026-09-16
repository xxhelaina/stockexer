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
UP_COLOR = '#EF5350'
DOWN_COLOR = '#26A69A'
MA_COLORS = {
    5: '#CFD5DD',
    10: '#E8BD58',
    20: '#A17BD9',
    60: '#5589CE'
}

# 文件编码尝试顺序
FILE_ENCODINGS = ['gbk', 'gb2312', 'utf-8', 'gb18030']

# 股票代码正则
STOCK_CODE_PATTERN = r'^\d{6}$'
