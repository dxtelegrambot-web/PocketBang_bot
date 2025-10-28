import asyncio
from itertools import count
import os
from decimal import Decimal
from typing import Optional, List
from dataclasses import dataclass
from datetime import datetime

from tonutils.client import TonapiClient, ToncenterV3Client
from tonutils.wallet import WalletV4R2
from pytoniq_core import Address, Cell
from database.models import Database
import hashlib
import traceback


@dataclass
class TONTransaction:
    """TON交易信息"""
    hash: str
    from_address: str
    to_address: str
    amount: Decimal
    fee: Decimal
    timestamp: datetime
    status: str


class TONClient:
    def __init__(self, network: str = "testnet"):
        self.network = network
        self.client = None
        self.bot_wallet = None
        self.bot_address = None
        
        # 从环境变量获取配置
        self.bot_wallet_mnemonic = os.getenv('TON_BOT_WALLET_MNEMONIC')
        self.tonapi_key = os.getenv('TON_API_KEY', '') 
    
    async def init(self):
        """异步初始化客户端"""
        try:
            # 初始化 tonutils 客户端
            if self.tonapi_key:
                # 优先使用 TonapiClient（需要 API key）
                self.client = TonapiClient(
                    api_key=self.tonapi_key,
                    is_testnet=(self.network == "testnet")
                )
            else:
                # 使用 ToncenterV3Client（免费但有限制）
                self.client = ToncenterV3Client(
                    is_testnet=(self.network == "testnet"),
                    rps=1,  # 每秒请求数限制
                    max_retries=3
                )
            
            # 初始化Bot钱包
            if self.bot_wallet_mnemonic:
                mnemonics = self.bot_wallet_mnemonic.split(",")
                # 使用 tonutils 的 WalletV4R2
                self.bot_wallet, _, _, _ = WalletV4R2.from_mnemonic(
                    client=self.client,
                    mnemonic=mnemonics
                )
                
                # 获取钱包地址
                self.bot_address = self.bot_wallet.address.to_str(
                    is_user_friendly=True,
                    is_url_safe=True,
                    is_bounceable=False,
                    is_test_only=(self.network == "testnet")
                )
            
            print(f"TON客户端初始化成功 - 网络: {self.network}, bot address: {self.bot_address}")
            
        except Exception as e:
            print(f"TON客户端初始化失败: {e}")
            raise
    
    async def get_balance(self, address: str) -> Decimal:
        """获取地址余额"""
        try:
            if not self.client:
                await self.init()
            
            # 使用 tonutils 客户端获取余额
            balance = await self.client.get_account_balance(address)
            return Decimal(str(balance)) / Decimal('1000000000')  # 转换为TON单位
            
        except Exception as e:
            print(f"获取余额失败: {e}")
            return Decimal('0')
    
    async def check_account_active(self, address: str) -> bool:
        """获取账户状态,必须是活跃状态才能进行转账"""
        try:
            if not self.client:
                await self.init()
            
            account_info = await self.client.get_raw_account(address)
            print(f"账户信息: {account_info.status}")
            if account_info:
                return account_info.status == 'active'
            return False
        except Exception as e:
            print(f"获取账户状态失败: {e}")
            return False
    
    async def get_bot_balance(self) -> Decimal:
        """获取Bot钱包余额"""
        if self.bot_address:
            return await self.get_balance(self.bot_address)
        return Decimal('0')
    
    async def get_transactions(self, address: str,from_lt:int, count: int = 10) -> List[TONTransaction]:
        """获取地址交易记录"""
        try:
            if not self.client:
                await self.init()
            
            # 使用 tonutils 获取交易记录
            transactions = await self.client.get_transactions(
                address=address,
                limit=count,
                from_lt=from_lt
            )
            result = []
            for tx in transactions:
                # 解析交易信息（根据 tonutils 的返回格式调整）
                result.append(TONTransaction(
                    hash=tx.hash,
                    from_address=tx.in_msg.source if tx.in_msg else "",
                    to_address=tx.in_msg.destination if tx.in_msg else "",
                    amount=Decimal(str(tx.in_msg.value)) / Decimal('1000000000') if tx.in_msg else Decimal('0'),
                    fee=Decimal(str(tx.fee)) / Decimal('1000000000'),
                    timestamp=datetime.fromtimestamp(tx.utime),
                    status="success" if tx.success else "failed"
                ))
            
            return result
        except Exception as e:
            print(f"获取交易记录失败: {e}")
            traceback.print_exc()
            return []
    
    def validate_address(self, address: str) -> bool:
        """验证TON地址格式"""
        try:
            Address(address)
            return True
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
        """估算交易手续费"""
        try:
            if not self.bot_wallet:
                return Decimal('0.01')  # 默认手续费
            
            # tonutils 的手续费估算
            return Decimal('0.005')  # 0.005 TON
            
        except Exception as e:
            print(f"估算手续费失败: {e}")
            return Decimal('0.01')
    
    async def close(self):
        """关闭客户端连接"""
        try:
            if self.client and hasattr(self.client, 'close'):
                await self.client.close()
        except Exception as e:
            print(f"关闭TON客户端失败: {e}")

    async def generate_user_deposit_address(self, user_id: int) -> str:
        """为用户生成独立的充值地址"""
        try:
            user_index = user_id % 2147483647
            
            if self.bot_wallet_mnemonic:
                mnemonics = self.bot_wallet_mnemonic.split(",")
                # 使用 tonutils 生成用户钱包
                user_wallet, _, _, _ = WalletV4R2.from_mnemonic(
                    client=self.client,
                    mnemonic=mnemonics,
                    wallet_id=user_index
                )
                
                user_address = user_wallet.address.to_str(
                    is_user_friendly=True,
                    is_url_safe=True,
                    is_bounceable=False,
                    is_test_only=(self.network == "testnet")
                )
                return user_address
            else:
                print("未配置mnemonic")
                return ""
        except Exception as e:
            print(f"生成用户充值地址失败: {e}")
            return ""

    async def get_user_wallet(self, user_id: int) -> Optional[WalletV4R2]:
        """获取用户钱包实例"""
        try:
            if not self.bot_wallet_mnemonic:
                return None
                
            user_index = user_id % 2147483647
            mnemonics = self.bot_wallet_mnemonic.split(",")
            
            user_wallet, _, _, _ = WalletV4R2.from_mnemonic(
                client=self.client,
                mnemonic=mnemonics,
                wallet_id=user_index
            )
            return user_wallet
        except Exception as e:
            print(f"获取用户钱包失败: {e}")
            return None
    
    async def withdraw_user_funds(self,target_address: str, amount: Decimal) -> Optional[str]:
        try:
            # 用户提款，直接使用bot_wallet进行转账
            if not self.bot_wallet:
                raise ValueError("Bot钱包未初始化")
            
            # 检查账户状态并部署（如果需要）
            if not await self.check_account_active(self.bot_address):
                await self.bot_wallet.deploy()
            
            result = await self.bot_wallet.transfer(
                destination=Address(target_address),
                amount=float(amount),
            )
            return result
        except Exception as e:
            traceback.print_exc()
            return None


# 全局TON客户端实例
ton_client = None


async def get_ton_client() -> TONClient:
    """获取TON客户端实例"""
    global ton_client
    if ton_client is None:
        network = os.getenv('TON_NETWORK', 'mainnet')
        ton_client = TONClient(network=network)
        await ton_client.init()
    return ton_client