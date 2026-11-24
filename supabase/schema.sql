-- Indira Pathshala – Supabase schema
-- Run once: Supabase dashboard → SQL Editor → paste → Run
--   (or: python -m pathshala_bot db-init   with SUPABASE_DB_URL set)
-- Safe to run again: everything is "if not exists".

-- Every pipeline / newspaper run (also the topic history that prevents repeats)
create table if not exists public.runs (
  id            bigint generated always as identity primary key,
  created_at    timestamptz not null default now(),
  exam_id       text not null,             -- exam id, or 'akhbar:<edition>' for newspapers
  subject       text,
  topic         text,                      -- topic covered / lead headline
  content_type  text,                      -- pyq_quiz, cheatsheet, ..., akhbar
  run_dir       text,                      -- local folder with the rendered files
  status        text,                      -- ok | partial | failed
  publish       jsonb not null default '{}'::jsonb,   -- where it was posted (message ids, URLs)
  content       jsonb                      -- the generated package (quiz, notes, ...) – used by /quiz
);
create index if not exists runs_exam_created_idx on public.runs (exam_id, created_at desc);
-- space the run belongs to (added later: also added to existing tables)
alter table public.runs add column if not exists space_id text;
create index if not exists runs_space_idx on public.runs (space_id, id desc);
create index if not exists runs_type_idx on public.runs (content_type);

-- Newspaper headlines already published (no repeats within a week)
create table if not exists public.headlines (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  exam_id     text not null,               -- 'akhbar:<edition>', e.g. akhbar:rajasthan-hi
  day         date not null,
  headline    text not null
);
create index if not exists headlines_exam_day_idx on public.headlines (exam_id, day desc);

-- Admin-panel settings (channels, exams, schedules, editions, keys) – a single row
create table if not exists public.app_config (
  id          int primary key default 1 check (id = 1),
  data        jsonb not null,
  updated_at  timestamptz not null default now()
);

-- Test series: students who signed up for a test (leads) and their attempts
create table if not exists public.test_leads (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  series_id   text not null,
  test_id     text not null,
  name        text,
  phone       text,
  email       text
);
create index if not exists test_leads_series_idx on public.test_leads (series_id, id desc);
create table if not exists public.test_attempts (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  series_id   text not null,
  test_id     text not null,
  lead_id     bigint,
  name        text,
  score       double precision,
  max_score   double precision,
  correct     int,
  wrong       int,
  skipped     int,
  seconds     int,
  answers     jsonb not null default '{}'::jsonb
);
create index if not exists test_attempts_test_idx on public.test_attempts (series_id, test_id);

-- Lock the tables down: Row Level Security on, and no policies, so the public
-- "anon" key can read nothing. The server uses the service_role key, which
-- bypasses RLS. app_config contains API keys – never expose it to a browser.
alter table public.runs       enable row level security;
alter table public.headlines  enable row level security;
alter table public.app_config enable row level security;
alter table public.test_leads    enable row level security;  -- phone numbers: server only
alter table public.test_attempts enable row level security;
