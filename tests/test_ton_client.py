import pytest
import sys
import os
from decimal import Decimal

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ton.client import get_ton_client
from ton.httpclient import get_http_client


class TestTONClientGetBalance:
    """测试 TONClient 的 get_balance 方法"""
    
    @pytest.mark.asyncio
    async def test_get_balance_success(self):
        """测试成功获取余额"""
        ton_client = await get_http_client()
        # 测试地址
        test_address = "EQD4FPq-PRDieyQKkizFTRtSDyucUIqrj0v_zXJmqaDp6_0t"
        # 调用方法
        account_states = await ton_client.get_account_states(test_address)
        # 使用 pytest 的 capsys 捕获输出，或者直接用 print，注意 pytest 默认不显示 print，除非加 -s 参数
        # print(f"get_account_states: {account_states}")
        transactions = await ton_client.get_transactions(test_address, limit=1)
        print(f"get_transactions: {transactions}")
        # 清理
        await ton_client.close()
    
