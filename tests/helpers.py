"""测试辅助：构造不依赖 AstrBot 的最小组件栈。

关键设计：``FakeEmbedding`` 默认不可用，因此测试天然覆盖「向量路降级」这一主路径；
需要覆盖向量路时传入 ``available=True`` 即可。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from super_astrbot.journal import JournalConfig, JournalService
from super_astrbot.memory import (
    HybridRetriever,
    KeywordRetriever,
    MemoryConfig,
    MemoryLifecycle,
    MemoryService,
    VectorRetriever,
)
from super_astrbot.storage import (
    Database,
    JournalRepository,
    MemoryRepository,
    VectorRepository,
)

VECTOR = [0.1, 0.2, 0.3, 0.4]


class FakeEmbedding:
    """可控的 Embedding 替身。"""

    def __init__(self, *, available: bool = False, vector: Sequence[float] | None = None) -> None:
        self._available = available
        self._vector = list(vector or VECTOR)
        self.calls = 0

    @property
    def available(self) -> bool:
        return self._available

    def fingerprint(self) -> str:
        return "fake-fp"

    def dimension(self) -> int:
        return len(self._vector)

    async def embed(self, text: str) -> list[float] | None:
        self.calls += 1
        if not self._available:
            return None
        return list(self._vector)


class FakeHost:
    """最小 Host 替身（日志丢弃）。"""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    def data_dir(self) -> Path:
        return self._data_dir

    def log(self) -> Any:
        class _NullLogger:
            def debug(self, *a: Any, **k: Any) -> None: ...
            def info(self, *a: Any, **k: Any) -> None: ...
            def warning(self, *a: Any, **k: Any) -> None: ...
            def error(self, *a: Any, **k: Any) -> None: ...

        return _NullLogger()


class FakeInjector:
    """记录注入调用的替身，用于验证「注入了什么」。"""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def inject(self, target: Any, blocks: Sequence[str], *, prefer: str = "auto") -> Any:
        from super_astrbot.harness.protocols import InjectResult

        self.calls.append((len(blocks), prefer))
        return InjectResult(
            applied=True, method="fake", parts=len(blocks), chars=sum(len(b) for b in blocks)
        )

    def clear(self, target: Any) -> int:
        return 0


class Stack:
    """一组已装配好的组件，便于测试直接取用。"""

    def __init__(
        self,
        *,
        db: Database,
        memories: MemoryRepository,
        journals_repo: JournalRepository,
        vectors: VectorRepository,
        config: MemoryConfig,
        embedding: FakeEmbedding,
        injector: FakeInjector,
        memory: MemoryService,
        journal: JournalService,
    ) -> None:
        self.db = db
        self.memories = memories
        self.journals_repo = journals_repo
        self.vectors = vectors
        self.config = config
        self.embedding = embedding
        self.injector = injector
        self.memory = memory
        self.journal = journal

    async def close(self) -> None:
        await self.db.close()


async def build_stack(
    tmp_path: Path,
    *,
    vector_available: bool = False,
    config: MemoryConfig | None = None,
) -> Stack:
    """装配一个最小可用组件栈（SQLite + 关键词检索 + 可选向量）。"""
    db = Database(tmp_path / "test.db")
    await db.connect()

    memories = MemoryRepository(db)
    journals_repo = JournalRepository(db)
    vectors = VectorRepository(db)
    cfg = config or MemoryConfig()
    embedding = FakeEmbedding(available=vector_available)
    injector = FakeInjector()

    routes: list[Any] = [KeywordRetriever(memories)]
    if vector_available:
        routes.append(VectorRetriever(embedding, vectors, max_scan=100))

    retriever = HybridRetriever(routes=routes, memories=memories, config=cfg.retrieval_config())
    lifecycle = MemoryLifecycle(
        db=db, memories=memories, vectors=vectors, embedding=embedding, config=cfg
    )
    memory = MemoryService(
        config=cfg,
        lifecycle=lifecycle,
        retriever=retriever,
        memories=memories,
        journals=journals_repo,
        injector=injector,
        embedding=embedding,
    )
    journal = JournalService(config=JournalConfig(), journals=journals_repo, memory_service=memory)
    return Stack(
        db=db,
        memories=memories,
        journals_repo=journals_repo,
        vectors=vectors,
        config=cfg,
        embedding=embedding,
        injector=injector,
        memory=memory,
        journal=journal,
    )
