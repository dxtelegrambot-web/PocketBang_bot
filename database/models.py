import mysql.connector
from mysql.connector import Error
from mysql.connector.cursor import MySQLCursor  # 添加这行导入
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from decimal import Decimal
import json
import os
from urllib.parse import urlparse


@dataclass
class User:
    """用户模型"""
    telegram_id: int
    username: Optional[str] = None
    display_name: Optional[str] = None
    balance: Decimal = Decimal('0.00000000')
    total_sent: Decimal = Decimal('0.00000000')
    total_received: Decimal = Decimal('0.00000000')
    deposit_address: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class RedPacket:
    """红包模型"""
    sender_id: int
    group_id: int
    group_name: Optional[str] = None
    total_amount: Decimal = Decimal('0.00000000')
    packet_count: int = 0
    remaining_count: int = 0
    remaining_amount: Decimal = Decimal('0.00000000')
    message: Optional[str] = None
    status: str = 'active'
    id: Optional[int] = None
    amounts: Optional[List[Decimal]] = None
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


@dataclass
class Claim:
    """红包领取记录模型"""
    red_packet_id: int  # 改为 int 类型
    claimer_id: int
    amount: Decimal
    id: Optional[int] = None  # 改为 int 类型，可选（自增）
    claimed_at: Optional[datetime] = None


@dataclass
class Transaction:
    """交易记录模型"""
    telegram_id: int
    lt: int
    type: str  # deposit/withdraw/red_packet_send/red_packet_receive
    amount: Decimal
    red_packet_id: Optional[int] = None  # 红包ID，可选
    ton_tx_hash: Optional[str] = None
    status: str = 'pending'  # pending/confirmed/failed
    created_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None

@dataclass
class RedPacketConfirmation:
    """红包确认临时数据模型"""
    sender_id: int
    amount: Decimal
    count: int
    type: str  # random/equal
    id: Optional[int] = None  # 改为 int 类型，可选（自增）
    message: Optional[str] = None
    group_id: Optional[int] = None
    created_at: Optional[datetime] = None

class Database:
    """数据库操作类"""
    
    def __init__(self, connection_string: str = None, table_prefix: str = "lb_"):
        self.table_prefix = os.getenv('TABLE_PRIFIX', table_prefix)  # 添加表前缀属性
        if connection_string:
            self.config = self._parse_connection_string(connection_string)
        else:
            # 从环境变量获取配置
            self.config = {
                'host': os.getenv('MYSQL_HOST', 'localhost'),
                'port': int(os.getenv('MYSQL_PORT', 3306)),
                'user': os.getenv('MYSQL_USER', 'root'),
                'password': os.getenv('MYSQL_PASSWORD', ''),
                'database': os.getenv('MYSQL_DATABASE', 'luckypack_db'),
                'charset': 'utf8mb4',
                'autocommit': False
            }
        self.init_database()
    
    def _parse_connection_string(self, connection_string: str) -> dict:
        """解析数据库连接字符串"""
        if connection_string.startswith('mysql://'):
            parsed = urlparse(connection_string)
            return {
                'host': parsed.hostname or 'localhost',
                'port': parsed.port or 3306,
                'user': parsed.username or 'root',
                'password': parsed.password or '',
                'database': parsed.path.lstrip('/') if parsed.path else 'luckypack_db',
                'charset': 'utf8mb4',
                'autocommit': False
            }
        else:
            raise ValueError("不支持的数据库连接字符串格式")
    
    def get_connection(self):
        """获取数据库连接"""
        try:
            connection = mysql.connector.connect(**self.config)
            return connection
        except Error as e:
            print(f"数据库连接失败: {e}")
            raise
    
    def init_database(self):
        """初始化数据库表"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            
            connection.commit()
            print("数据库表初始化完成")
            
        except Error as e:
            print(f"初始化数据库失败: {e}")
            if connection:
                connection.rollback()
            raise
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    

    def get_user_by_telegram_id(self, telegram_id: int) -> User:
        """获取用户余额"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor(dictionary=True)
            
            cursor.execute(
                f"SELECT * FROM {self.table_prefix}users WHERE telegram_id = %s",
                (telegram_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return User(
                telegram_id=telegram_id,
                username=row['username'],
                display_name=row['display_name'],
                balance=Decimal(str(row['balance'])),
                total_sent=Decimal(str(row['total_sent'])),
                total_received=Decimal(str(row['total_received'])),
                deposit_address=row['deposit_address'],
                created_at=row['created_at'],
                updated_at=row['updated_at']
            )
            
        except Error as e:
            print(f"获取用户失败: {e}")
            return None
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    
    def get_user_balance(self, telegram_id: int) -> Decimal:
        """获取用户余额"""
        user = self.get_user_by_telegram_id(telegram_id)
        if user:
            return user.balance
        return Decimal('0.00000000')
    def create_user(self,user:User):
        """创建用户"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            
            cursor.execute(
                f"INSERT INTO {self.table_prefix}users (telegram_id, username, display_name, balance, deposit_address,total_sent, total_received, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,%s)",
                (
                    user.telegram_id,
                    user.username,
                    user.display_name,
                    float(user.balance),
                    user.deposit_address,
                    float(user.total_sent),
                    float(user.total_received),
                    user.created_at or datetime.now(),
                    user.updated_at or datetime.now()
                )
            )
            connection.commit()
            return True
        except Error as e:
            print(f"创建用户失败: {e}")
            if connection:
                connection.rollback()
            return False
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()

    def update_user_balance(self, telegram_id: int, amount: Decimal) -> bool:
        """更新用户余额"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            
            if not self._update_user_balance(cursor, telegram_id, amount):
                return False
            connection.commit()
            return True
        except Error as e:
            print(f"更新用户余额失败: {e}")
            if connection:
                connection.rollback()
            return False
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    def _update_user_balance(self, cursor:MySQLCursor, telegram_id: int, amount: Decimal) -> bool:
        """更新用户余额"""
        cursor.execute(
            f"UPDATE {self.table_prefix}users SET balance = balance + %s, updated_at = %s WHERE telegram_id = %s",
            (amount, datetime.now(), telegram_id)
        )
        return cursor.rowcount > 0
    def _update_user_balance_send(self, cursor:MySQLCursor, telegram_id: int, amount: Decimal) -> bool:
        """更新用户余额"""
        cursor.execute(
            f"UPDATE {self.table_prefix}users SET balance = balance + %s,total_sent=total_sent+%s,updated_at = %s WHERE telegram_id = %s",
            (amount,-amount, datetime.now(), telegram_id)
        )
        return cursor.rowcount > 0
    # 红包相关操作
    def _create_red_packet(self, cursor: MySQLCursor, red_packet: RedPacket) -> int:
        """创建红包，返回红包ID"""
        amounts_json = json.dumps([float(amount) for amount in red_packet.amounts]) if red_packet.amounts else None
        
        cursor.execute(
            f"""
            INSERT INTO {self.table_prefix}red_packets 
            (sender_id, group_id, group_name, total_amount, packet_count, 
                remaining_count, remaining_amount, message, status, amounts, created_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                red_packet.sender_id, red_packet.group_id, red_packet.group_name,
                float(red_packet.total_amount), red_packet.packet_count,
                red_packet.remaining_count, float(red_packet.remaining_amount),
                red_packet.message, red_packet.status, amounts_json,
                red_packet.created_at, red_packet.expires_at
            )
        )
        return cursor.lastrowid  # 返回自增ID
    
    def get_red_packet(self, red_packet_id: int) -> Optional[RedPacket]:  # str -> int
        """获取红包信息"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor(dictionary=True)
            
            cursor.execute(
                f"SELECT * FROM {self.table_prefix}red_packets WHERE id = %s",
                (red_packet_id,)
            )
            row = cursor.fetchone()
            
            if row:
                amounts = None
                if row['amounts']:
                    amounts = [Decimal(str(amount)) for amount in json.loads(row['amounts'])]
                
                return RedPacket(
                    id=row['id'],
                    sender_id=row['sender_id'],
                    group_id=row['group_id'],
                    group_name=row['group_name'],
                    total_amount=Decimal(str(row['total_amount'])),
                    packet_count=row['packet_count'],
                    remaining_count=row['remaining_count'],
                    remaining_amount=Decimal(str(row['remaining_amount'])),
                    message=row['message'],
                    status=row['status'],
                    amounts=amounts,
                    created_at=row['created_at'],
                    expires_at=row['expires_at']
                )
            return None
        except Error as e:
            print(f"获取红包信息失败: {e}")
            return None
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    
    def claim_red_packet_tran(self, red_packet_id: int, transaction: Transaction, claimer_id: int, amount: Decimal) -> bool:  # str -> int
        """领取红包"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            
            connection.start_transaction()
     
            # 创建领取记录
            cursor.execute(
                f"INSERT INTO {self.table_prefix}claims (red_packet_id, claimer_id, amount) VALUES (%s, %s, %s)",
                (red_packet_id, claimer_id, float(amount))
            )
            
            # 更新红包状态
            cursor.execute(
                f"UPDATE {self.table_prefix}red_packets SET remaining_count = remaining_count - 1, remaining_amount = remaining_amount - %s WHERE id = %s",
                (float(amount), red_packet_id)
            )
            
            # 更新用户余额
            cursor.execute(
                f"UPDATE {self.table_prefix}users SET balance = balance + %s, total_received = total_received + %s, updated_at = %s WHERE telegram_id = %s",
                (float(amount), float(amount), datetime.now(), claimer_id)
            )
            # 创建交易记录
            self._create_transaction(cursor, transaction)
            
            connection.commit()
            return True
        except Error as e:
            print(f"领取红包失败: {e}")
            if connection:
                connection.rollback()
            return False
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    
    def get_red_packet_claims(self, red_packet_id: int) -> List[Claim]:  # str -> int
        """获取红包领取记录"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor(dictionary=True)
            
            cursor.execute(
                f"""
                SELECT c.*, u.username, u.display_name 
                FROM {self.table_prefix}claims c 
                LEFT JOIN {self.table_prefix}users u ON c.claimer_id = u.telegram_id 
                WHERE c.red_packet_id = %s 
                ORDER BY c.claimed_at
                """,
                (red_packet_id,)
            )
            
            claims = []
            for row in cursor.fetchall():
                claim = Claim(
                    id=row['id'],
                    red_packet_id=row['red_packet_id'],
                    claimer_id=row['claimer_id'],
                    amount=Decimal(str(row['amount'])),
                    claimed_at=row['claimed_at']
                )
                # 添加用户信息
                claim.username = row['username']
                claim.display_name = row['display_name']
                claims.append(claim)
            
            return claims
        except Error as e:
            print(f"获取红包领取记录失败: {e}")
            return []
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    
    def has_user_claimed(self, red_packet_id: int, user_id: int) -> bool:  # str -> int
        """检查用户是否已领取红包"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            
            cursor.execute(
                f"SELECT id FROM {self.table_prefix}claims WHERE red_packet_id = %s AND claimer_id = %s",
                (red_packet_id, user_id)
            )
            return cursor.fetchone() is not None
        except Error as e:
            print(f"检查用户领取状态失败: {e}")
            return False
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
 
    
    def get_all_user_addresses(self):
        """获取所有用户的充值地址"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            cursor.execute(f"""
                SELECT u.telegram_id, u.deposit_address,max(t.lt) as lt
                FROM {self.table_prefix}users as u left join {self.table_prefix}transactions as t on u.telegram_id = t.telegram_id group by u.telegram_id
            """)
            
            return cursor.fetchall()
            
        except Error as e:
            print(f"获取用户地址失败: {e}")
            return []
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    

    def create_transaction(self, transaction: Transaction) -> bool:
        """创建交易记录"""
        connection = None
        try:
            connection = self.get_connection()
            connection.autocommit = False  # 确保事务模式
            cursor = connection.cursor()
            self._create_transaction(cursor, transaction)
            connection.commit()
            return True
        except Error as e:
            print(f"创建交易记录失败: {e}")
            if connection:
                connection.rollback()
            return False
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    def _create_transaction(self, cursor:MySQLCursor, transaction: Transaction) -> bool:
        """创建交易记录"""
        cursor.execute(
            f"""
            INSERT INTO {self.table_prefix}transactions 
            (telegram_id, lt, type, amount, red_packet_id,ton_tx_hash, status, created_at, confirmed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s,%s)
            """,
            (
                transaction.telegram_id,
                transaction.lt,
                transaction.type,
                float(transaction.amount),
                transaction.red_packet_id,
                transaction.ton_tx_hash,
                transaction.status,
                transaction.created_at or datetime.now(),
                transaction.confirmed_at
            )
        )
        return cursor.rowcount > 0
    
    def check_transaction(self, telegram_id: int, lt: int) -> bool:
        """获取交易记录"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor(dictionary=True)
            
            cursor.execute(
                f"SELECT * FROM {self.table_prefix}transactions WHERE telegram_id = %s AND lt = %s",
                (telegram_id, lt)
            )
            row = cursor.fetchone()
            if not row:
                return True
            return  False
        except Error as e:
            print(f"获取交易记录失败: {e}")
            return False
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()

    def get_user_transactions(self, telegram_id: int, limit:int) -> List[Transaction]:
        """获取用户交易记录"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor(dictionary=True)
            
            cursor.execute(
                f"SELECT * FROM {self.table_prefix}transactions WHERE telegram_id = %s ORDER BY created_at DESC limit %s",
                (telegram_id, limit)
            )
            transactions = []
            for row in cursor.fetchall():
                transaction = Transaction(
                    telegram_id=row['telegram_id'],
                    lt=row['lt'],
                    type=row['type'],
                    amount=Decimal(str(row['amount'])),
                    ton_tx_hash=row['ton_tx_hash'],
                    status=row['status'],
                    created_at=row['created_at'],
                    confirmed_at=row['confirmed_at']
                )
                transactions.append(transaction)
            return transactions
        except Error as e:
            print(f"获取用户交易记录失败: {e}")
            return []
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    def create_red_packet_tran(self, transaction:Transaction, red_packet: RedPacket, telegram_id:int, amount:Decimal) -> Optional[int]:
        """创建红包交易"""
        connection = None
        try:
            connection = self.get_connection()
            connection.autocommit = False  # 确保事务模式
            cursor = connection.cursor()
            red_packet_id = None
            # 1:扣除用户余额，同时编辑用户总发送金额
            if not self._update_user_balance_send(cursor, telegram_id, amount):
                connection.rollback()
                return None
            # 2:创建红包
            red_packet_id = self._create_red_packet(cursor, red_packet)
            if not red_packet_id:
                connection.rollback()
                return None
            # 3:创建交易记录
            transaction.red_packet_id = red_packet_id  # 设置红包ID
            if not self._create_transaction(cursor, transaction):
                connection.rollback()
                return None
            connection.commit()
            return red_packet_id
        except Error as e:
            print(f"创建红包交易记录失败: {e}")
            if connection:
                connection.rollback()
            return None
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()

    def confirm_withdraw_tran(self, transaction: Transaction, telegram_id:int, amount:Decimal) -> bool:
        """确认提现交易"""
        connection = None
        try:
            connection = self.get_connection()
            connection.autocommit = False 
            cursor = connection.cursor()
            # 确认提款 1:扣除余额 
            if not self._update_user_balance(cursor, telegram_id, amount):
                connection.rollback()
                return False
            # 2:创建交易记录
            if not self._create_transaction(cursor, transaction):
                connection.rollback()
                return False
            connection.commit()
            return True
        except Error as e:
            print(f"确认提现交易失败: {e}")
            if connection:
                connection.rollback()
            return False
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    
    def create_red_packet_confirmation(self, confirmation: RedPacketConfirmation) -> Optional[int]:
        """创建红包确认临时数据，返回自增ID"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            
            cursor.execute(
                f"""
                INSERT INTO {self.table_prefix}red_packet_confirmations 
                (sender_id, amount, count, type, message, group_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    confirmation.sender_id,
                    float(confirmation.amount),
                    confirmation.count,
                    confirmation.type,
                    confirmation.message,
                    confirmation.group_id,
                    confirmation.created_at or datetime.now()
                )
            )
            connection.commit()
            return cursor.lastrowid  # 返回自增ID
        except Error as e:
            print(f"创建红包确认临时数据失败: {e}")
            if connection:
                connection.rollback()
            return None
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    def get_red_packet_confirmation(self, confirmation_id: int) -> Optional[RedPacketConfirmation]:  # str -> int
        """获取红包确认临时数据"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor(dictionary=True)
            
            cursor.execute(
                f"SELECT * FROM {self.table_prefix}red_packet_confirmations WHERE id = %s",
                (confirmation_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            
            return RedPacketConfirmation(
                id=row['id'],
                sender_id=row['sender_id'],
                amount=Decimal(str(row['amount'])),
                count=row['count'],
                type=row['type'],
                message=row['message'],
                group_id=row['group_id'],
                created_at=row['created_at']
            )
        except Error as e:
            print(f"获取红包确认临时数据失败: {e}")
            return None
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    def delete_red_packet_confirmation(self, confirmation_id: int) -> bool:  # str -> int
        """删除红包确认临时数据"""
        connection = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            
            cursor.execute(
                f"DELETE FROM {self.table_prefix}red_packet_confirmations WHERE id = %s",
                (confirmation_id,)
            )
            connection.commit()
            return cursor.rowcount > 0
        except Error as e:
            print(f"删除红包确认临时数据失败: {e}")
            if connection:
                connection.rollback()
            return False
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()