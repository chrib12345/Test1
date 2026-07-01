-- Optional pgvector layer (Phase 4). Enable only when semantic dedup / role
-- search is turned on. Dimension must match hiring_tracker.vector.EMBED_DIM.

create extension if not exists vector;

create table if not exists posting_embeddings (
    posting_id  bigint primary key references job_postings(id),
    embedding   vector(1536),
    embedded_at timestamptz not null default now()
);

-- Approximate-NN index for cosine distance (built once data exists).
create index if not exists ix_posting_embeddings_cos
    on posting_embeddings using hnsw (embedding vector_cosine_ops);
