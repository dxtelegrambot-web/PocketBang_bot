import random
from decimal import Decimal, ROUND_DOWN
from typing import List


def generate_random_amounts(total_amount: Decimal, count: int) -> List[Decimal]:
    """
    随机分配红包金额 (二倍均值法)
    
    Args:
        total_amount: 红包总金额
        count: 红包数量
        
    Returns:
        List[Decimal]: 分配后的金额列表
    """
    if count <= 0:
        return []
    
    if count == 1:
        return [total_amount]
    
    # 确保精度为8位小数
    total_amount = total_amount.quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
    
    # 转换为最小单位 (纳诺TON)
    min_unit = Decimal('0.00000001')
    total_units = int(total_amount / min_unit)
    
    # 确保每个红包至少有1个最小单位
    if total_units < count:
        raise ValueError(f"红包总金额不足: {total_amount} TON, 需要至少 {count * min_unit} TON")
    
    # 分配算法 (二倍均值法)
    amounts_units = []
    remaining_units = total_units
    remaining_count = count
    
    for i in range(count - 1):
        # 计算当前红包的随机范围
        # 剩余平均值的2倍作为上限
        max_amount = int(remaining_units / remaining_count * 2)
        # 至少1个最小单位
        max_amount = max(1, max_amount)
        
        # 随机生成当前红包金额
        amount = random.randint(1, max_amount)
        amounts_units.append(amount)
        
        # 更新剩余金额和数量
        remaining_units -= amount
        remaining_count -= 1
    
    # 最后一个红包拿走剩余金额
    amounts_units.append(remaining_units)
    
    # 转回TON单位
    amounts = [Decimal(units) * min_unit for units in amounts_units]
    
    # 随机打乱顺序
    random.shuffle(amounts)
    
    return amounts


def generate_equal_amounts(total_amount: Decimal, count: int) -> List[Decimal]:
    """
    均分红包金额
    
    Args:
        total_amount: 红包总金额
        count: 红包数量
        
    Returns:
        List[Decimal]: 分配后的金额列表
    """
    if count <= 0:
        return []
    
    # 确保精度为8位小数
    total_amount = total_amount.quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
    
    # 计算每个红包的金额
    amount_per_packet = (total_amount / count).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
    
    # 确保每个红包至少有最小单位
    min_unit = Decimal('0.00000001')
    if amount_per_packet < min_unit:
        raise ValueError(f"红包总金额不足: {total_amount} TON, 需要至少 {count * min_unit} TON")
    
    # 创建均等金额列表
    amounts = [amount_per_packet] * count
    
    # 处理因舍入导致的剩余金额
    remaining = total_amount - sum(amounts)
    if remaining > Decimal('0'):
        # 将剩余金额分配到第一个红包
        amounts[0] += remaining
    
    return amounts


def format_ton_amount(amount: Decimal) -> str:
    """
    格式化TON金额显示
    
    Args:
        amount: TON金额
        
    Returns:
        str: 格式化后的金额字符串
    """
    # 去除尾部的0
    amount_str = str(amount.normalize())
    
    # 如果是整数，添加.0
    if '.' not in amount_str:
        amount_str += '.0'
    
    return amount_str + ' TON'