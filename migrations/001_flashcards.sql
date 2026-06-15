-- StudyTime — Flashcards migration
-- Run this in your Supabase SQL editor to add flashcard support.

-- Flashcard sets (one set contains many cards)
create table if not exists flashcard_sets (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references auth.users(id) on delete cascade,
    title       text not null,
    subject     text,
    description text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- Individual flashcards (term → definition study pointers)
create table if not exists flashcards (
    id          uuid primary key default gen_random_uuid(),
    set_id      uuid not null references flashcard_sets(id) on delete cascade,
    term        text not null,
    definition  text not null,
    hint        text,
    order_index integer not null default 0,
    created_at  timestamptz not null default now()
);

-- Indexes for common query patterns
create index if not exists flashcard_sets_user_id_idx on flashcard_sets(user_id);
create index if not exists flashcards_set_id_idx      on flashcards(set_id);
create index if not exists flashcards_order_idx       on flashcards(set_id, order_index);

-- Row-level security (users can only see their own sets)
alter table flashcard_sets enable row level security;
alter table flashcards      enable row level security;

create policy "Users manage own flashcard sets"
    on flashcard_sets for all
    using  (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "Users manage own flashcards"
    on flashcards for all
    using  (
        set_id in (
            select id from flashcard_sets where user_id = auth.uid()
        )
    )
    with check (
        set_id in (
            select id from flashcard_sets where user_id = auth.uid()
        )
    );
