create table public.assets (
    id uuid primary key,
    owner_id uuid not null references auth.users(id) on delete restrict,
    thread_id uuid not null,
    turn_id uuid,
    role text not null check (role in ('input', 'artifact', 'dataset', 'workspace')),
    status text not null check (status in ('uploading', 'ready', 'failed', 'deleted')),
    logical_key text not null,
    bucket_id text not null,
    object_path text not null,
    sandbox_path text,
    filename text not null,
    media_type text not null,
    size_bytes bigint check (size_bytes is null or size_bytes >= 0),
    sha256 text check (sha256 is null or sha256 ~ '^[a-f0-9]{64}$'),
    retention_class text not null default 'standard'
        check (retention_class in ('temporary', 'standard', 'pinned')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (thread_id, logical_key),
    unique (bucket_id, object_path),
    constraint assets_artifact_sandbox_path_check check (
        (
            role = 'artifact'
            and sandbox_path is not null
            and sandbox_path like '/workspace/artifacts/%'
        )
        or role <> 'artifact'
    ),
    constraint assets_ready_integrity_check check (
        status <> 'ready'
        or (size_bytes is not null and sha256 is not null)
    )
);

create index assets_owner_thread_updated_idx
    on public.assets (owner_id, thread_id, updated_at desc)
    where status <> 'deleted';

create index assets_retention_updated_idx
    on public.assets (retention_class, updated_at)
    where status in ('ready', 'failed');

alter table public.assets enable row level security;

revoke all on table public.assets from anon, authenticated;
grant all on table public.assets to service_role;

insert into storage.buckets (
    id,
    name,
    public,
    file_size_limit
)
values (
    'langalpha-assets',
    'langalpha-assets',
    false,
    26214400
)
on conflict (id) do update
set
    public = excluded.public,
    file_size_limit = excluded.file_size_limit;

do $$
begin
    if to_regprocedure('public.rls_auto_enable()') is not null then
        execute
            'revoke execute on function public.rls_auto_enable() '
            'from public, anon, authenticated';
    end if;
end
$$;
