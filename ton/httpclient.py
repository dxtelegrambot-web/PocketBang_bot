import asyncio
import aiohttp
import os
from decimal import Decimal
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime
import traceback
from pytoniq_core import Address


@dataclass
class HTTPTONTransaction:
    """HTTP API 获取的 TON 交易信息"""
    hash: str
    lt: int
    from_address: str
    to_address: str
    amount: Decimal
    fee: Decimal
    timestamp: datetime
    status: str
    message: str = ""


class TONHTTPClient:
    """使用 HTTP API 的 TON 客户端"""
    
    def __init__(self, network: str = "testnet"):
        self.network = network
        self.session = None
        
        # API 端点配置 - 修复：使用正确的 v3 API
        if network == "mainnet":
            self.base_url = "https://toncenter.com/api/v3"
        else:
            self.base_url = "https://testnet.toncenter.com/api/v3"
        
        # API Key（提高请求限制）
        self.api_key = os.getenv('TON_CENTER_API_KEY', 'ee7df7f7dd5592b3f5a4710b9f153e8e34b73da2aed4030154d12072cb40a97e')
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.init()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()
    
    async def init(self):
        """初始化 HTTP 客户端"""
        if not self.session:
            timeout = aiohttp.ClientTimeout(total=30)
            # 修复：设置默认 headers
            headers = {
                'accept': 'application/json',
                'X-Api-Key': self.api_key
            }
            self.session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        print(f"TON HTTP 客户端初始化成功 - 网络: {self.network}")
    
    async def close(self):
        """关闭 HTTP 客户端"""
        if self.session:
            await self.session.close()
            self.session = None
    
    async def _make_request(self, endpoint: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """发起 HTTP 请求"""
        if not self.session:
            await self.init()
        
        url = f"{self.base_url}/{endpoint}"
        
        if params is None:
            params = {}
        
        try:
            print(f"请求 URL: {url}")
            print(f"请求参数: {params}")
            
            async with self.session.get(url, params=params) as response:
                print(f"响应状态: {response.status}")
                
                if response.status == 200:
                    data = await response.json()
                    
                    # 修复：v3 API 的响应格式不同
                    if isinstance(data, list) and len(data) > 0:
                        return data[0]  # v3 API 返回数组
                    elif isinstance(data, dict):
                        return data
                    else:
                        raise Exception(f"意外的响应格式: {data}")
                else:
                    error_text = await response.text()
                    raise Exception(f"HTTP 错误: {response.status}, 响应: {error_text}")
        
        except Exception as e:
            print(f"请求失败: {e}")
            raise
    
    async def get_account_states(self, address: str, include_boc: bool = True) -> Dict[str, Any]:
        """获取账户状态 - 使用 v3 API"""
        params = {
            'address': address,
            'include_boc': str(include_boc).lower()
        }
        return await self._make_request('accountStates', params)
    
    async def get_balance(self, address: str) -> Decimal:
        """获取地址余额"""
        try:
            info = await self.get_account_states(address)
            # 修复：v3 API 的字段名可能不同
            balance = int(info.get('balance', 0))
            return Decimal(str(balance)) / Decimal('1000000000')  # 转换为 TON 单位
        
        except Exception as e:
            print(f"获取余额失败: {e}")
            return Decimal('0')
    
    async def get_transactions(self, address: str, from_lt: int = None,count: int = 10, hash_: str = None) -> List[HTTPTONTransaction]:
        """获取地址交易记录"""
        try:
            params = {
                'account': address,
                'limit': count,
                'sort': 'asc'
            }
            if from_lt:
                params['start_lt'] = from_lt
            if hash_:
                params['hash'] = hash_
            
            # 修复：使用正确的 v3 API 端点
            data = await self._make_request('transactions', params)
            
            transactions = []
            # 处理可能的数组响应
            tx_list = data["transactions"] if "transactions" in data else []
            print(f"获取到 {len(tx_list)} 符合条件的交易记录")
            for tx in tx_list:
                if tx["in_msg"] is None:
                    continue
                if tx["in_msg"].get("value") is None or Decimal(str(tx["in_msg"]["value"])) <= 0:
                    continue
                # 判断交易是否被退回
                if tx["in_msg"].get("bounced") == True:
                    continue 
                from_address = Address(tx["in_msg"].get("source", "")).to_str(1, 1, 0)  # 确保地址格式正确
                to_address = Address(tx["in_msg"].get("destination", "")).to_str(1, 1, 0)
                amount = Decimal(str(tx["in_msg"].get('value', 0))) / Decimal('1000000000')
                
                # 计算手续费
                fee = Decimal(str(tx.get('total_fees', 0))) / Decimal('1000000000')
                timestamp = datetime.fromtimestamp(tx.get('now', 0))
                lt = tx.get('lt', 0)
                
                transactions.append(HTTPTONTransaction(
                    hash=tx.get('hash', ''),
                    lt=lt,
                    from_address=from_address,
                    to_address=to_address,
                    amount=amount,
                    fee=fee,
                    timestamp=timestamp,
                    status='confirmed',
                ))
            
            return transactions
        
        except Exception as e:
            print(f"获取交易记录失败: {e}")
            traceback.print_exc()
            return []
    
    def validate_address(self, address: str) -> bool:
        """验证 TON 地址格式（简单验证）"""
        try:
            # 基本格式检查
            if not address:
                return False
            
            # TON 地址通常以特定前缀开始
            valid_prefixes = ['EQ', 'UQ', 'kQ', 'kf', '0Q', '0f']
            
            if any(address.startswith(prefix) for prefix in valid_prefixes):
                # 检查长度（Base64 编码的地址通常是 48 字符）
                return len(address) >= 40
            
            return False
        
        except Exception:
            return False
    
    def format_address(self, address: str, short: bool = True) -> str:
        """格式化地址显示"""
        if not address:
            return "未知地址"
        
        if short and len(address) > 10:
            return f"{address[:6]}...{address[-4:]}"
        
        return address
    
    async def estimate_fee(self, to_address: str, amount: Decimal) -> Decimal:
        """估算交易手续费（返回固定值）"""
        # HTTP API 通常不提供手续费估算，返回合理的默认值
        return Decimal('0.005')  # 0.005 TON


# 全局 HTTP 客户端实例
http_client = None


async def get_http_client() -> TONHTTPClient:
    """获取 HTTP 客户端实例"""
    global http_client
    if http_client is None:
        network = os.getenv('TON_NETWORK', 'mainnet')
        http_client = TONHTTPClient(network=network)
        await http_client.init()
    return http_client


# 使用示例
async def example_usage():
    """使用示例"""
    async with TONHTTPClient("mainnet") as client:  # 修复：使用 mainnet
        # 获取地址余额
        address = "UQCmSkp9SiRDDhqsqoLV0nU1cTjKF5wrWyem4E4jxfzml9G5"
        balance = await client.get_balance(address)
        print(f"余额: {balance} TON")
        
        # 获取账户状态
        state = await client.get_account_state(address)
        print(f"账户状态: {state}")
        
        # 获取交易记录
        transactions = await client.get_transactions(address, limit=5)
        print(f"交易数量: {len(transactions)}")
        
        for tx in transactions:
            print(f"交易: {tx.hash[:8]}... 金额: {tx.amount} TON")


if __name__ == "__main__":
    asyncio.run(example_usage())