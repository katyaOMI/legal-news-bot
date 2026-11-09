-- Desk Research: five tables.
-- sources     -> where we collect from
-- materials   -> what the collector brought (one row per link)
-- findings    -> what the worker extracted with the model
-- runs        -> every run of every block, with status
-- subscribers -> who pressed Start in the Telegram bot

CREATE TABLE IF NOT EXISTS sources (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,            -- hn | arxiv | rss
    url         TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS runs (
    id           SERIAL PRIMARY KEY,
    service      TEXT NOT NULL,           -- collector | worker | bot
    status       TEXT NOT NULL,           -- started | running | finished | failed
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    items        INTEGER NOT NULL DEFAULT 0,
    note         TEXT
);

CREATE TABLE IF NOT EXISTS materials (
    id            SERIAL PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES sources(id),
    run_id        INTEGER REFERENCES runs(id),
    url           TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    text          TEXT,
    published_at  TIMESTAMPTZ,
    collected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    status        TEXT NOT NULL DEFAULT 'new'   -- new | done | skipped | failed
);

CREATE TABLE IF NOT EXISTS findings (
    id              SERIAL PRIMARY KEY,
    material_id     INTEGER NOT NULL UNIQUE REFERENCES materials(id),
    entity          TEXT,                 -- company the material is about
    summary         TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- the bot marks a finding when it has been sent to subscribers
ALTER TABLE findings ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS subscribers (
    chat_id     BIGINT PRIMARY KEY,
    name        TEXT,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Legal-news migration: keep existing subscribers and historical data.
ALTER TABLE sources ADD COLUMN IF NOT EXISTS monitoring_started_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS archive_backfill_completed_at TIMESTAMPTZ;
UPDATE sources SET active = FALSE WHERE kind IN ('hn', 'arxiv', 'rss');
INSERT INTO sources(name,kind,url) VALUES
 ('Официальное опубликование РФ','pravo','https://publication.pravo.gov.ru/api/Documents')
ON CONFLICT(name) DO NOTHING;

CREATE TABLE IF NOT EXISTS material_snapshots (
 id SERIAL PRIMARY KEY,
 material_id INTEGER NOT NULL REFERENCES materials(id),
 captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 digest TEXT NOT NULL,
 metadata JSONB NOT NULL,
 UNIQUE(material_id,digest)
);
CREATE TABLE IF NOT EXISTS source_events (
 id SERIAL PRIMARY KEY,
 source_id INTEGER NOT NULL REFERENCES sources(id),
 kind TEXT NOT NULL,
 body TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS source_event_deliveries (
 event_id INTEGER NOT NULL REFERENCES source_events(id),
 chat_id BIGINT NOT NULL REFERENCES subscribers(chat_id) ON DELETE CASCADE,
 sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(event_id,chat_id)
);
CREATE TABLE IF NOT EXISTS finding_deliveries (
 finding_id INTEGER NOT NULL REFERENCES findings(id),
 chat_id BIGINT NOT NULL REFERENCES subscribers(chat_id) ON DELETE CASCADE,
 sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(finding_id,chat_id)
);
CREATE OR REPLACE VIEW legal_findings AS
 SELECT f.* FROM findings f JOIN materials m ON m.id=f.material_id
 JOIN sources s ON s.id=m.source_id WHERE s.kind IN ('pravo','legal_rss','survey_web') AND s.active;

-- Official API is kept as a configured optional source, not polled while unavailable.
-- This one-time switch is keyed so operators can enable it later without being reset.
CREATE TABLE IF NOT EXISTS project_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now());
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM project_migrations WHERE name='free_rss_v1') THEN
  UPDATE sources SET active=FALSE WHERE kind='pravo';
  INSERT INTO project_migrations(name) VALUES ('free_rss_v1');
 END IF;
END $$;
ALTER TABLE materials ADD COLUMN IF NOT EXISTS dedup_key TEXT;
ALTER TABLE materials ADD COLUMN IF NOT EXISTS is_backfill BOOLEAN NOT NULL DEFAULT FALSE;
CREATE UNIQUE INDEX IF NOT EXISTS materials_dedup_key ON materials(dedup_key) WHERE dedup_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS material_mentions (
 material_id INTEGER NOT NULL REFERENCES materials(id),
 source_id INTEGER NOT NULL REFERENCES sources(id),
 url TEXT NOT NULL,
 first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(source_id,url)
);
INSERT INTO sources(name,kind,url) VALUES ('КонсультантПлюс: горячие документы','legal_rss','https://www.consultant.ru/rss/hotdocs.xml') ON CONFLICT(name) DO NOTHING;
INSERT INTO sources(name,kind,url) VALUES ('КонсультантПлюс: федеральное законодательство','legal_rss','https://www.consultant.ru/rss/fd.xml') ON CONFLICT(name) DO NOTHING;
INSERT INTO sources(name,kind,url) VALUES ('КонсультантПлюс: новости для юриста','legal_rss','https://www.consultant.ru/rss/nw.xml') ON CONFLICT(name) DO NOTHING;
INSERT INTO sources(name,kind,url) VALUES ('ГАРАНТ: новости','legal_rss','https://rss.garant.ru/categories/news/') ON CONFLICT(name) DO NOTHING;
INSERT INTO sources(name,kind,url) VALUES ('ГАРАНТ: федеральные документы','legal_rss','https://rss.garant.ru/hotlaw/federal/') ON CONFLICT(name) DO NOTHING;

CREATE TABLE IF NOT EXISTS weekly_digest_runs (
 chat_id BIGINT NOT NULL REFERENCES subscribers(chat_id) ON DELETE CASCADE,
 week_start DATE NOT NULL,
 sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 item_count INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(chat_id,week_start)
);

-- Additive survey-material migration. Existing subscriptions/deliveries stay intact.
ALTER TABLE materials ADD COLUMN IF NOT EXISTS content_kind TEXT NOT NULL DEFAULT 'news';
ALTER TABLE materials ADD COLUMN IF NOT EXISTS usefulness TEXT NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS source_pages (
 source_id INTEGER NOT NULL REFERENCES sources(id),
 url TEXT NOT NULL,
 last_checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 last_error TEXT,
 PRIMARY KEY(source_id,url)
);
CREATE TABLE IF NOT EXISTS source_checks (
 run_id INTEGER NOT NULL REFERENCES runs(id),
 source_id INTEGER NOT NULL REFERENCES sources(id),
 discovered INTEGER NOT NULL DEFAULT 0,
 checked INTEGER NOT NULL DEFAULT 0,
 accepted INTEGER NOT NULL DEFAULT 0,
 errors INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(run_id,source_id)
);
