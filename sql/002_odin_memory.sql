create table if not exists odin_memory (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('semantic', 'episodic', 'procedural')),
  content text not null,
  subject text,
  key text,
  org text,
  provenance jsonb,
  confidence real,
  valid_from timestamptz not null default now(),
  valid_to timestamptz,
  recorded_at timestamptz not null default now(),
  superseded_by uuid,
  content_hash text not null
);

-- genau eine gueltige Zeile pro (subject, key): DB-enforced. coalesce(subject,'') schliesst
-- die NULL-subject-Luecke (sonst waeren zwei gueltige (NULL,key)-Zeilen erlaubt); key=NULL
-- Append bleibt distinct, da NULL im Unique-Index distinct ist.
create unique index if not exists odin_memory_subject_key_valid
  on odin_memory (coalesce(subject, ''), key) where valid_to is null;
create index if not exists odin_memory_kind on odin_memory (kind);
create index if not exists odin_memory_org on odin_memory (org);
create index if not exists odin_memory_content_hash on odin_memory (content_hash);

-- Atomarer Supersede: alte Zeile invalidieren, neue einfuegen, verketten -- in EINER
-- Transaktion und in der Reihenfolge invalidate-vor-insert, damit der UNIQUE-Partial-Index
-- nie transient zwei gueltige Zeilen sieht. Gibt die neue Zeile zurueck.
create or replace function odin_memory_supersede(
  p_old_id uuid,
  p_kind text,
  p_content text,
  p_subject text,
  p_key text,
  p_org text,
  p_provenance jsonb,
  p_content_hash text,
  p_valid_from timestamptz
) returns odin_memory
language plpgsql
as $$
declare
  v_new odin_memory;
begin
  update odin_memory set valid_to = now()
    where id = p_old_id and valid_to is null;
  insert into odin_memory (kind, content, subject, key, org, provenance, content_hash, valid_from)
    values (p_kind, p_content, p_subject, p_key, p_org, p_provenance, p_content_hash,
            coalesce(p_valid_from, now()))
    returning * into v_new;
  update odin_memory set superseded_by = v_new.id where id = p_old_id;
  return v_new;
end;
$$;
