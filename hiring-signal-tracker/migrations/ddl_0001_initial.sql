-- Canonical initial schema (Section 6). Applied by Alembic migration 0001 and
-- mirrored to the managed Postgres. Keep this file and the migration in sync.

create table if not exists companies (
    id           bigint generated always as identity primary key,
    ticker       text,
    name         text not null,
    ats_type     text not null check (ats_type in ('greenhouse','lever','ashby','workable','custom')),
    ats_token    text not null,
    careers_url  text,
    is_active    boolean not null default true,
    added_at     timestamptz not null default now(),
    notes        text
);

create table if not exists snapshots (
    id            bigint generated always as identity primary key,
    company_id    bigint not null references companies(id),
    fetched_at    timestamptz not null default now(),
    ats_type      text not null,
    status        text not null check (status in ('ok','failed','empty')),
    http_status   int,
    posting_count int,
    payload_hash  text,
    duration_ms   int,
    error_detail  text
);
create index if not exists ix_snapshots_company_fetched on snapshots (company_id, fetched_at desc);

create table if not exists raw_payloads (
    snapshot_id bigint primary key references snapshots(id),
    body        jsonb not null
);

create table if not exists job_postings (
    id               bigint generated always as identity primary key,
    company_id       bigint not null references companies(id),
    source_ats       text not null,
    source_job_id    text not null,
    title            text not null,
    raw_department   text,
    raw_team         text,
    raw_location     text,
    remote_flag      boolean,
    employment_type  text,
    url              text,
    apply_url        text,
    description_hash text,
    comp_summary     text,
    first_seen_at    timestamptz not null,
    last_seen_at     timestamptz not null,
    closed_at        timestamptz,
    is_open          boolean not null default true,
    reposted_from    bigint references job_postings(id),
    norm_function    text,
    norm_seniority   text,
    norm_country     text,
    norm_metro       text,
    normalized_at    timestamptz,
    constraint uq_posting_natural_key unique (company_id, source_ats, source_job_id),
    constraint postings_open_closed_consistency check (not (is_open = true and closed_at is not null))
);
create index if not exists ix_postings_company_open on job_postings (company_id, is_open);
create index if not exists ix_postings_company_first_seen on job_postings (company_id, first_seen_at);
create index if not exists ix_postings_norm_function on job_postings (norm_function);

create table if not exists posting_events (
    id          bigint generated always as identity primary key,
    posting_id  bigint not null references job_postings(id),
    company_id  bigint not null references companies(id),
    event_type  text not null check (event_type in ('opened','closed','reappeared','reposted','edited')),
    event_at    timestamptz not null default now(),
    snapshot_id bigint references snapshots(id)
);
create index if not exists ix_posting_events_company_at on posting_events (company_id, event_at);

create table if not exists title_map (
    raw_title      text primary key,
    norm_function  text not null,
    norm_seniority text not null,
    mapped_at      timestamptz not null default now(),
    mapped_by      text not null default 'claude'
);

create table if not exists metrics_daily (
    company_id      bigint not null references companies(id),
    as_of_date      date not null,
    open_count      int not null,
    opened_7d       int not null,
    closed_7d       int not null,
    net_7d          int not null,
    opened_30d      int not null,
    closed_30d      int not null,
    by_function     jsonb not null default '{}',
    by_seniority    jsonb not null default '{}',
    by_metro        jsonb not null default '{}',
    repost_rate_30d numeric,
    computed_at     timestamptz not null default now(),
    primary key (company_id, as_of_date)
);
