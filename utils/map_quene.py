import asyncio
from typing import Any, Dict,Set

class QueueMap:
    def __init__(self, maxsize: int = 0):
        self._maxsize = maxsize
        self._map: Dict[Any, asyncio.Queue] = {}

    def put_queue(self, id: Any,data:Any) -> None:
        """
        将数据放入对应 id 的 asyncio.Queue 中。如果不存在则新建一个。
        """
        if id not in self._map:
            self._map[id] = asyncio.Queue(maxsize=self._maxsize)
        queue = self._map[id]
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            pass
    def remove_queue(self, id: Any) -> None:
        """
        删除对应 id 的 queue 引用，便于回收。
        """
        self._map.pop(id, None)

    def has_queue(self, id: Any) -> bool:
        """
        判断是否已存在对应 id 的 queue。
        """
        return id in self._map

    def clear(self) -> None:
        """
        清空所有 queue 映射。
        """
        self._map.clear()
