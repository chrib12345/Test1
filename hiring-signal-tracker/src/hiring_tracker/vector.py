"""pgvector description dedup + semantic role search (Phase 4, optional).

This is guarded and optional. It only works if the `vector` extension and the
`posting_embeddings` table exist (see migration 0002).

Embeddings source: the core build must not require a paid data source, so the
default embedder here is a deterministic, offline, hashed bag-of-words vector
(useful for near-duplicate detection and rough semantic grouping). Swap in a
real embedding provider by passing a different ``embed_fn`` — the dimension is
configured by EMBED_DIM and must match the column definition.
"""

from __future__ import annotations

import math
import re
from typing import Callable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from .logging_util import get_logger

log = get_logger("vector")

EMBED_DIM = 1536
_TOKEN_RE = re.compile(r"[a-z0-9]+")

Embedder = Callable[[str], list[float]]


def hashed_embedding(textval: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic L2-normalized hashed bag-of-words embedding (placeholder)."""
    vec = [0.0] * dim
    for tok in _TOKEN_RE.findall((textval or "").lower()):
        h = hash(tok) % dim
        vec[h] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def extension_available(session: Session) -> bool:
    row = session.execute(
        text("select 1 from pg_extension where extname = 'vector'")
    ).first()
    return row is not None


def embed_open_postings(
    session: Session, embed_fn: Optional[Embedder] = None
) -> int:
    """Embed open postings that have no embedding yet. Returns rows embedded."""
    if not extension_available(session):
        log.warning("vector extension not installed; skipping embeddings")
        return 0
    embed_fn = embed_fn or hashed_embedding
    rows = session.execute(
        text(
            """
            select jp.id, jp.title, coalesce(jp.raw_department,''), coalesce(jp.raw_location,'')
            from job_postings jp
            left join posting_embeddings pe on pe.posting_id = jp.id
            where jp.is_open = true and pe.posting_id is null
            """
        )
    ).all()
    n = 0
    for pid, title, dept, loc in rows:
        vec = embed_fn(f"{title} {dept} {loc}")
        session.execute(
            text(
                "insert into posting_embeddings (posting_id, embedding) "
                "values (:pid, cast(:emb as vector)) "
                "on conflict (posting_id) do update set embedding = excluded.embedding, "
                "embedded_at = now()"
            ),
            {"pid": pid, "emb": _vec_literal(vec)},
        )
        n += 1
    log.info("embedded %d postings", n)
    return n


def find_near_duplicates(
    session: Session, company_id: int, threshold: float = 0.05, limit: int = 50
) -> list[dict]:
    """Return pairs of open postings within cosine distance `threshold`."""
    if not extension_available(session):
        return []
    rows = session.execute(
        text(
            """
            select a.posting_id as a_id, b.posting_id as b_id,
                   (a.embedding <=> b.embedding) as dist,
                   ja.title as a_title, jb.title as b_title
            from posting_embeddings a
            join posting_embeddings b on a.posting_id < b.posting_id
            join job_postings ja on ja.id = a.posting_id
            join job_postings jb on jb.id = b.posting_id
            where ja.company_id = :cid and jb.company_id = :cid
              and ja.is_open and jb.is_open
              and (a.embedding <=> b.embedding) < :thr
            order by dist asc
            limit :lim
            """
        ),
        {"cid": company_id, "thr": threshold, "lim": limit},
    ).all()
    return [
        {"a_id": r.a_id, "b_id": r.b_id, "distance": float(r.dist),
         "a_title": r.a_title, "b_title": r.b_title}
        for r in rows
    ]


def semantic_role_search(
    session: Session, query: str, embed_fn: Optional[Embedder] = None, limit: int = 20
) -> list[dict]:
    """Rank open postings by semantic similarity to a free-text role query."""
    if not extension_available(session):
        return []
    embed_fn = embed_fn or hashed_embedding
    qvec = _vec_literal(embed_fn(query))
    rows = session.execute(
        text(
            """
            select jp.id, jp.title, jp.company_id,
                   (pe.embedding <=> cast(:q as vector)) as dist
            from posting_embeddings pe
            join job_postings jp on jp.id = pe.posting_id
            where jp.is_open = true
            order by dist asc
            limit :lim
            """
        ),
        {"q": qvec, "lim": limit},
    ).all()
    return [
        {"posting_id": r.id, "title": r.title, "company_id": r.company_id,
         "distance": float(r.dist)}
        for r in rows
    ]
