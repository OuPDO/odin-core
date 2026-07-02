create table if not exists odin_project_registry (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  org text not null default 'unknown',
  path text not null unique,
  git_remote text,
  stack text,
  status text,
  purpose_oneliner text,
  last_commit_at timestamptz,
  last_scanned_at timestamptz not null default now(),
  size_tier text
);
