"""Incident memory: recall prior incidents on the same asset.

Two implementations behind one `IncidentStore` interface:

- `LocalIncidentStore` keeps incidents in-process and recalls by keyword overlap
  and recency. It needs nothing installed, so the offline demo and the test
  suite use it.
- `MnemosIncidentStore` wraps the Mnemos memory engine. The mapping is the whole
  trick: an asset id becomes a Mnemos ``user_id``, so "what has gone wrong on
  turbine_1 before" is literally ``memory.recall(user_id="turbine_1", query=...)``
  and each resolved incident is a persisted fact under that asset. Mnemos is an
  optional dependency (it pulls a database + embedding stack), imported lazily so
  it is only required when actually selected.

Both return the same lightweight `PriorIncident`, so the agent's recall tool does
not know or care which is wired in.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from grid_copilot.types import IncidentReport

_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class PriorIncident:
    incident_id: str
    asset: str
    summary: str
    occurred_at: datetime


class IncidentStore(Protocol):
    def remember(self, report: IncidentReport) -> str:
        """Persist a resolved incident; return its id (a citation handle)."""
        ...

    def recall(self, asset: str, query: str, k: int = 3) -> list[PriorIncident]:
        ...


class LocalIncidentStore:
    """In-process store: keyword overlap + recency, no dependencies."""

    def __init__(self) -> None:
        self._by_asset: dict[str, list[PriorIncident]] = defaultdict(list)
        self._seq = 0

    def remember(self, report: IncidentReport) -> str:
        self._seq += 1
        iid = f"INC-{self._seq:04d}"
        self._by_asset[report.asset].append(
            PriorIncident(
                incident_id=iid,
                asset=report.asset,
                summary=report.summary_line(),
                occurred_at=report.ts,
            )
        )
        return iid

    def recall(self, asset: str, query: str, k: int = 3) -> list[PriorIncident]:
        q = set(_WORD.findall(query.lower()))
        candidates = self._by_asset.get(asset, [])
        scored = []
        for inc in candidates:
            overlap = len(q & set(_WORD.findall(inc.summary.lower())))
            scored.append((overlap, inc.occurred_at, inc))
        # Rank by keyword overlap first, then recency.
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [inc for _, _, inc in scored[:k]]


class MnemosIncidentStore:
    """Adapter over the Mnemos memory engine, keyed by asset id.

    Mnemos is async; this bridges it to the synchronous pipeline with a private
    event loop. Constructing it imports Mnemos, so it is only built when the
    caller selects the ``mnemos`` memory backend.
    """

    def __init__(self, settings: object | None = None, qdrant_path: str = "./data/qdrant_local") -> None:
        import asyncio

        from mnemos import Memory, Settings  # lazy: optional heavy dependency

        # Default to the zero-server path (embedded qdrant + local embeddings), so
        # this runs without a Postgres/Neo4j server. An explicit `settings` wins.
        if settings is None:
            settings = Settings(
                storage_backend="qdrant",
                embedding_provider="local",
                qdrant_local_path=qdrant_path,
            )
        self._loop = asyncio.new_event_loop()
        self._memory = Memory(settings)
        self._seq = 0

    def remember(self, report: IncidentReport) -> str:
        import uuid
        from datetime import timezone

        self._seq += 1
        iid = f"INC-{self._seq:04d}"
        # Mnemos scores recency against a tz-aware clock, so timestamps must be
        # tz-aware; incident timestamps are treated as UTC.
        occurred = report.ts if report.ts.tzinfo else report.ts.replace(tzinfo=timezone.utc)
        # asset id -> user_id; the incident summary becomes a durable episode.
        self._loop.run_until_complete(
            self._memory.remember_episode(
                user_id=report.asset,
                session_id=uuid.uuid4(),
                role="assistant",
                content=f"[{iid}] {report.summary_line()}",
                occurred_at=occurred,
                metadata={"incident_id": iid, "trigger": report.trigger_signal},
            )
        )
        return iid

    def recall(self, asset: str, query: str, k: int = 3) -> list[PriorIncident]:
        result = self._loop.run_until_complete(
            self._memory.recall(user_id=asset, query=query)
        )
        out: list[PriorIncident] = []
        for i, scored in enumerate(result.episodes[:k]):
            ep = scored.episode
            # The incident id was stored as a "[INC-NNNN] ..." prefix on content,
            # since EpisodeRead does not round-trip metadata.
            m = re.match(r"\[([^\]]+)\]\s*", ep.content)
            out.append(
                PriorIncident(
                    incident_id=m.group(1) if m else f"MEM-{i}",
                    asset=asset,
                    summary=ep.content[m.end():] if m else ep.content,
                    occurred_at=ep.occurred_at,
                )
            )
        return out

    def close(self) -> None:
        self._loop.run_until_complete(self._memory.aclose())
        self._loop.close()
