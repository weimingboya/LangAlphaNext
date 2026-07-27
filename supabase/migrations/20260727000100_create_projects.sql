create table public.projects (
    id uuid primary key,
    owner_id uuid not null references auth.users(id) on delete restrict,
    name text not null check (char_length(name) between 1 and 120),
    sandbox_id text unique,
    status text not null default 'active'
        check (status in ('active', 'deleting', 'deleted')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index projects_owner_updated_idx
    on public.projects (owner_id, updated_at desc)
    where status <> 'deleted';

alter table public.projects enable row level security;
revoke all on table public.projects from anon, authenticated;
grant all on table public.projects to service_role;

create temporary table asset_project_migration (
    owner_id uuid not null,
    thread_id uuid not null,
    project_id uuid not null default gen_random_uuid(),
    primary key (owner_id, thread_id)
) on commit drop;

insert into asset_project_migration (owner_id, thread_id)
select distinct owner_id, thread_id
from public.assets;

insert into public.projects (id, owner_id, name, created_at, updated_at)
select
    project_id,
    owner_id,
    'Imported project',
    now(),
    now()
from asset_project_migration;

alter table public.assets
    add column project_id uuid;

update public.assets as assets
set project_id = migration.project_id
from asset_project_migration as migration
where assets.owner_id = migration.owner_id
  and assets.thread_id = migration.thread_id;

alter table public.assets
    alter column project_id set not null,
    add constraint assets_project_id_fkey
        foreign key (project_id) references public.projects(id) on delete restrict;

drop index public.assets_owner_thread_updated_idx;
drop index public.assets_retention_updated_idx;

alter table public.assets
    drop constraint assets_thread_id_logical_key_key,
    drop constraint assets_bucket_id_object_path_key,
    drop constraint assets_role_check,
    drop constraint assets_retention_class_check;

update public.assets
set role = 'input'
where role in ('dataset', 'workspace');

alter table public.assets
    add constraint assets_role_check check (role in ('input', 'artifact')),
    add constraint assets_project_id_logical_key_key unique (project_id, logical_key),
    add constraint assets_object_path_key unique (object_path),
    drop column thread_id,
    drop column turn_id,
    drop column bucket_id,
    drop column retention_class;

create index assets_owner_project_updated_idx
    on public.assets (owner_id, project_id, updated_at desc)
    where status <> 'deleted';
