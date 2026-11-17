-- The whole database, in one file. Vrum is a prototype with no customer data, so there are no
-- migrations: this file is applied to an empty database, and a change to it means rebuilding the
-- database from scratch (docker compose down --volumes, then up again).
-- The database itself refuses anything that breaks the hierarchy, the links, the claims, the
-- history, or the walls between projects.

CREATE EXTENSION IF NOT EXISTS vector;

-- Accounts, sessions, projects and agent keys -----------------------------------------------
-- None of this is graph content. The role every project's work runs as (below) has no access to
-- any of these tables, so a bug in a tool can never read a password hash, a session or a key.

CREATE TABLE accounts (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Written in lower case, so that the same address is never two accounts.
    email         text NOT NULL,
    display_name  text NOT NULL,
    -- Salted and slow (scrypt), as 'scrypt$N$r$p$salt$hash'. Never the password itself.
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT account_email_is_unique UNIQUE (email),
    CONSTRAINT account_email_is_lower_case CHECK (email = lower(btrim(email))),
    CONSTRAINT account_email_looks_like_one CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    CONSTRAINT account_name_is_not_blank CHECK (btrim(display_name) <> ''),
    CONSTRAINT account_password_is_hashed CHECK (password_hash LIKE 'scrypt$%')
);

-- A sign-in. Only a hash of the cookie's value is kept, so a copy of the database cannot be used
-- to sign in. A session ends when it expires or when its person signs out.
CREATE TABLE sessions (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    token_hash text NOT NULL UNIQUE,
    -- What a page has to send back with every change, so that another site cannot make one.
    csrf_token text NOT NULL,
    account_id bigint NOT NULL REFERENCES accounts (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    ended_at   timestamptz,
    CONSTRAINT session_expires_after_it_starts CHECK (expires_at > created_at)
);

CREATE INDEX sessions_account_idx ON sessions (account_id);

CREATE TABLE projects (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Exactly one owner. Only the owner and the agents the owner gave a key to may reach it.
    owner_id   bigint NOT NULL REFERENCES accounts (id),
    name       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT project_name_is_not_blank CHECK (btrim(name) <> '')
);

CREATE UNIQUE INDEX project_name_is_unique_for_its_owner ON projects (owner_id, lower(name));

-- What an agent sends to reach one project, as 'Authorization: Bearer <key>'. Only a hash is kept.
CREATE TABLE agent_keys (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id   bigint NOT NULL REFERENCES projects (id),
    label        text NOT NULL,
    key_hash     text NOT NULL UNIQUE,
    -- The first characters of the key, so that a person can tell the keys apart.
    key_hint     text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at   timestamptz,
    CONSTRAINT agent_key_label_is_not_blank CHECK (btrim(label) <> '')
);

CREATE INDEX agent_keys_project_idx ON agent_keys (project_id);

-- The role a project's work runs as. The server switches to it for the length of every
-- transaction, and names the project (see current_project), so that the database itself shows
-- the work of that project and nothing else.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vrum_project') THEN
        CREATE ROLE vrum_project NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$$;

GRANT vrum_project TO CURRENT_USER;

-- The project this transaction works for, or NULL when none was named. With none, every project
-- table shows no rows and takes none.
CREATE FUNCTION current_project() RETURNS bigint LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('vrum.project_id', true), '')::bigint
$$;

-- Each project numbers its own epics, stories, tasks, links, rules, facts, decisions and events
-- from 1, each kind on its own, so a project's export loads into another project with the same
-- numbers. The numbers come from sequences made when the project is made. The function is the one
-- thing that may use them.
CREATE FUNCTION projects_make_sequences() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    kind text;
BEGIN
    FOREACH kind IN ARRAY ARRAY['epic', 'story', 'task', 'links', 'rules', 'facts', 'decisions',
                                'events'] LOOP
        EXECUTE format('CREATE SEQUENCE %I', kind || '_ids_' || NEW.id);
    END LOOP;
    RETURN NEW;
END
$$;

CREATE TRIGGER projects_make_sequences AFTER INSERT ON projects
    FOR EACH ROW EXECUTE FUNCTION projects_make_sequences();

CREATE FUNCTION next_id(kind text) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF current_project() IS NULL THEN
        RAISE EXCEPTION 'No project is named for this work, so nothing can be written.';
    END IF;
    RETURN nextval(format('%I', kind || '_ids_' || current_project())::regclass);
END
$$;

-- After an import, the next number is the one after the highest that was loaded.
CREATE FUNCTION continue_ids_after(kind text, highest bigint) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF current_project() IS NULL THEN
        RAISE EXCEPTION 'No project is named for this work, so nothing can be written.';
    END IF;
    IF highest >= 1 THEN
        PERFORM setval(format('%I', kind || '_ids_' || current_project())::regclass, highest);
    END IF;
END
$$;

-- Epics, stories and tasks ------------------------------------------------------------------

-- An epic, story or task is named by its kind and its number within that kind: `epic:3`,
-- `story:12`, `task:40`. That text is its `ref`, and it is the only identity these three kinds have:
-- every other table that points at one holds its ref, with a foreign key to it. The number comes
-- from next_id(kind) in the INSERT (an import gives its own), so each kind counts from 1 on its own.
CREATE TABLE nodes (
    project_id  bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    kind        text NOT NULL,
    number      bigint NOT NULL,
    ref         text GENERATED ALWAYS AS (kind || ':' || number::text) STORED,
    -- Epics first, then stories, then tasks, so that `ORDER BY kind_rank, number` is the order
    -- everything is listed in, and a page can continue after (kind_rank, number).
    kind_rank   smallint GENERATED ALWAYS AS (
        CASE kind WHEN 'epic' THEN 1 WHEN 'story' THEN 2 WHEN 'task' THEN 3 END
    ) STORED,
    -- The ref of the epic a story belongs to, or of the story a task belongs to. Empty for an epic.
    parent      text,
    -- The kind the parent must have: a story sits under an epic, a task under a story.
    -- Worked out from `kind`, and checked against the parent's ref below.
    parent_kind text GENERATED ALWAYS AS (
        CASE kind WHEN 'story' THEN 'epic' WHEN 'task' THEN 'story' END
    ) STORED,
    title       text NOT NULL,
    body        text NOT NULL DEFAULT '',
    -- `ready` is never stored: it is worked out from the links (see node_state below).
    status      text NOT NULL DEFAULT 'new',
    priority    text NOT NULL DEFAULT 'normal',
    -- Plain dates with no time of day. What the roadmap draws; see node_state for what is worked out.
    start_on    date,
    target_on   date,
    version     integer NOT NULL DEFAULT 1,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    -- What exact-words search reads. Worked out by the database from the title and body, so it is
    -- current the moment a change is saved.
    search      tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', title), 'A') || setweight(to_tsvector('english', body), 'B')
    ) STORED,
    PRIMARY KEY (project_id, kind, number),
    CONSTRAINT node_ref_is_unique UNIQUE (project_id, ref),
    CONSTRAINT node_kind_is_known CHECK (kind IN ('epic', 'story', 'task')),
    CONSTRAINT node_number_starts_at_one CHECK (number >= 1),
    CONSTRAINT node_title_is_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT node_status_is_known
        CHECK (status IN ('new', 'in_progress', 'blocked', 'done', 'dropped')),
    CONSTRAINT node_priority_is_known CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    CONSTRAINT node_start_is_not_after_target
        CHECK (start_on IS NULL OR target_on IS NULL OR start_on <= target_on),
    CONSTRAINT node_version_starts_at_one CHECK (version >= 1),
    CONSTRAINT node_has_a_parent_unless_epic CHECK ((kind = 'epic') = (parent IS NULL)),
    CONSTRAINT node_parent_has_the_right_kind
        CHECK (parent IS NULL OR starts_with(parent, parent_kind || ':')),
    -- The parent is in the same project, because the project is part of the key.
    CONSTRAINT node_parent_exists FOREIGN KEY (project_id, parent)
        REFERENCES nodes (project_id, ref)
);

CREATE INDEX nodes_parent_idx ON nodes (project_id, parent);
CREATE INDEX nodes_order_idx ON nodes (project_id, kind_rank, number);
CREATE INDEX nodes_search_idx ON nodes USING gin (search);

CREATE FUNCTION nodes_guard_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.project_id <> OLD.project_id THEN
        RAISE EXCEPTION 'A node cannot move to another project.';
    END IF;
    IF NEW.kind <> OLD.kind OR NEW.number <> OLD.number THEN
        RAISE EXCEPTION 'A node cannot change its kind or its number (it is % and stays that way).',
            OLD.ref;
    END IF;
    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'Every change to a node must raise its version by exactly one (was %, got %).',
            OLD.version, NEW.version;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END
$$;

CREATE TRIGGER nodes_guard_update BEFORE UPDATE ON nodes
    FOR EACH ROW EXECUTE FUNCTION nodes_guard_update();

-- Links between nodes -----------------------------------------------------------------------

CREATE TABLE links (
    project_id bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    id         bigint NOT NULL DEFAULT next_id('links'),
    source     text NOT NULL,
    target     text NOT NULL,
    link_type  text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, id),
    CONSTRAINT link_type_is_known
        CHECK (link_type IN ('needs_first', 'supersedes', 'duplicates', 'relates_to')),
    CONSTRAINT link_is_not_to_the_same_node CHECK (source <> target),
    CONSTRAINT link_is_not_repeated UNIQUE (project_id, source, target, link_type),
    -- Both ends are in the link's own project.
    CONSTRAINT link_starts_at_a_node FOREIGN KEY (project_id, source) REFERENCES nodes (project_id, ref),
    CONSTRAINT link_ends_at_a_node FOREIGN KEY (project_id, target) REFERENCES nodes (project_id, ref)
);

-- `duplicates` and `relates_to` work both ways, so A to B and B to A are the same link.
CREATE UNIQUE INDEX link_two_way_is_not_repeated
    ON links (project_id, link_type, LEAST(source, target), GREATEST(source, target))
    WHERE link_type IN ('duplicates', 'relates_to');

CREATE INDEX links_target_idx ON links (project_id, target);

-- A node cannot wait, directly or through others, for itself. Adding source -> target makes a
-- circle exactly when target already waits for source.
CREATE FUNCTION links_refuse_a_circle_of_waiting() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    circle text[];
BEGIN
    -- A link to the same node is refused by its own rule, which says so more plainly.
    IF NEW.link_type <> 'needs_first' OR NEW.source = NEW.target THEN
        RETURN NEW;
    END IF;
    -- One at a time in a project, so two links added together cannot each look fine and together
    -- make a circle.
    PERFORM pg_advisory_xact_lock(1, NEW.project_id::integer);
    WITH RECURSIVE reach (ref, path) AS (
        SELECT NEW.target, ARRAY[NEW.target]
        UNION ALL
        SELECT l.target, r.path || l.target
        FROM links l JOIN reach r ON l.source = r.ref
        WHERE l.project_id = NEW.project_id
          AND l.link_type = 'needs_first' AND NOT l.target = ANY (r.path)
    )
    SELECT path INTO circle FROM reach WHERE ref = NEW.source LIMIT 1;
    IF circle IS NOT NULL THEN
        RAISE EXCEPTION '%',
            'That link would make a circle of waiting: ' || NEW.source
            || ' would need ' || array_to_string(circle, ', which needs ')
            || '. None of them could ever start. Remove one of the needs_first links in the circle '
            || 'first.';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER links_refuse_a_circle_of_waiting BEFORE INSERT OR UPDATE ON links
    FOR EACH ROW EXECUTE FUNCTION links_refuse_a_circle_of_waiting();

-- Claims with leases ------------------------------------------------------------------------

-- At most one holder per node, because the node is the key. An expired claim stays in its row
-- until it is taken over or released. Only a story or a task can be claimed.
CREATE TABLE claims (
    project_id bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    node       text NOT NULL,
    actor      text NOT NULL,
    claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, node),
    CONSTRAINT claim_is_for_a_story_or_task
        CHECK (starts_with(node, 'story:') OR starts_with(node, 'task:')),
    CONSTRAINT claim_actor_is_not_blank CHECK (btrim(actor) <> ''),
    CONSTRAINT claim_lease_is_in_the_future CHECK (expires_at > claimed_at),
    CONSTRAINT claim_is_on_a_real_node
        FOREIGN KEY (project_id, node) REFERENCES nodes (project_id, ref)
);

-- A node as it is read: its status with `ready` worked out, what it still waits for, who holds a
-- claim on it right now, and what its dates add up to. This is the one place that decides what
-- `ready`, overdue, a span and a schedule conflict mean. Every join stays inside the node's own
-- project, and the view is read with the rights of whoever reads it, so a project's role sees only
-- its own rows.
--   waiting_on:         the refs of the nodes it needs first that are not done (epics, then stories,
--                       then tasks, each by number).
--   schedule_conflicts: those of them that are due after this node's own start date. A warning only.
--   overdue:            its own target date has passed and it is not done or dropped.
--   span_start/end:     the dates the roadmap draws. Its own when it has any. Otherwise, for an epic
--                       or story, the span of what is under it (dropped work does not count), and
--                       span_worked_out says so.
--   lapsed_claim_*:     a claim that ran out and was neither renewed, released nor taken over. It
--                       stays in its row until then, so it shows as work someone left with no
--                       handoff note.
--   body_chars:         how long the body is, so a list can leave the body out and still say how
--                       much there is.
--   progress_done/total: for an epic or a story, its tasks that are done out of its tasks that are
--                       not dropped (an epic's are the tasks of its stories that are not dropped).
--                       Empty for a task.
CREATE VIEW node_state WITH (security_invoker = true) AS
SELECT n.project_id, n.ref, n.kind, n.number, n.kind_rank, n.parent, n.title, n.body,
       length(n.body) AS body_chars,
       CASE WHEN n.status = 'new' AND cardinality(w.refs) = 0 THEN 'ready' ELSE n.status END
           AS status,
       n.priority, n.start_on, n.target_on, n.version, n.created_at, n.updated_at,
       w.refs AS waiting_on,
       w.conflicts AS schedule_conflicts,
       coalesce(n.target_on < current_date AND n.status NOT IN ('done', 'dropped'), false)
           AS overdue,
       sp.span_start,
       sp.span_end,
       (n.start_on IS NULL AND n.target_on IS NULL
        AND (sp.span_start IS NOT NULL OR sp.span_end IS NOT NULL)) AS span_worked_out,
       c.actor AS claimed_by,
       c.expires_at AS claim_expires_at,
       lc.actor AS lapsed_claim_by,
       lc.expires_at AS lapsed_claim_at,
       CASE WHEN n.kind <> 'task' THEN pr.done END AS progress_done,
       CASE WHEN n.kind <> 'task' THEN pr.total END AS progress_total
FROM nodes n
CROSS JOIN LATERAL (
    SELECT count(*) FILTER (WHERE t.status = 'done')::integer AS done, count(*)::integer AS total
    FROM nodes t
    WHERE n.kind <> 'task' AND t.project_id = n.project_id
      AND t.kind = 'task' AND t.status <> 'dropped'
      AND CASE n.kind
              WHEN 'story' THEN t.parent = n.ref
              ELSE t.parent IN (SELECT s.ref FROM nodes s
                                WHERE s.project_id = n.project_id
                                  AND s.parent = n.ref AND s.status <> 'dropped')
          END
) pr
CROSS JOIN LATERAL (
    SELECT coalesce(array_agg(t.ref ORDER BY t.kind_rank, t.number), ARRAY[]::text[]) AS refs,
           coalesce(array_agg(t.ref ORDER BY t.kind_rank, t.number)
                    FILTER (WHERE n.start_on IS NOT NULL AND t.target_on > n.start_on),
                    ARRAY[]::text[]) AS conflicts
    FROM links l JOIN nodes t ON t.project_id = l.project_id AND t.ref = l.target
    WHERE l.project_id = n.project_id
      AND l.source = n.ref AND l.link_type = 'needs_first' AND t.status <> 'done'
) w
CROSS JOIN LATERAL (
    SELECT CASE
               WHEN n.start_on IS NOT NULL OR n.target_on IS NOT NULL THEN n.start_on
               WHEN n.kind = 'story' THEN (
                   SELECT min(t.start_on) FROM nodes t
                   WHERE t.project_id = n.project_id AND t.parent = n.ref
                     AND t.status <> 'dropped')
               WHEN n.kind = 'epic' THEN (
                   SELECT min(CASE WHEN s.start_on IS NOT NULL OR s.target_on IS NOT NULL
                                   THEN s.start_on
                                   ELSE (SELECT min(t.start_on) FROM nodes t
                                         WHERE t.project_id = n.project_id AND t.parent = s.ref
                                           AND t.status <> 'dropped')
                              END)
                   FROM nodes s
                   WHERE s.project_id = n.project_id AND s.parent = n.ref
                     AND s.status <> 'dropped')
           END AS span_start,
           CASE
               WHEN n.start_on IS NOT NULL OR n.target_on IS NOT NULL THEN n.target_on
               WHEN n.kind = 'story' THEN (
                   SELECT max(t.target_on) FROM nodes t
                   WHERE t.project_id = n.project_id AND t.parent = n.ref
                     AND t.status <> 'dropped')
               WHEN n.kind = 'epic' THEN (
                   SELECT max(CASE WHEN s.start_on IS NOT NULL OR s.target_on IS NOT NULL
                                   THEN s.target_on
                                   ELSE (SELECT max(t.target_on) FROM nodes t
                                         WHERE t.project_id = n.project_id AND t.parent = s.ref
                                           AND t.status <> 'dropped')
                              END)
                   FROM nodes s
                   WHERE s.project_id = n.project_id AND s.parent = n.ref
                     AND s.status <> 'dropped')
           END AS span_end
) sp
LEFT JOIN claims c ON c.project_id = n.project_id AND c.node = n.ref
    AND c.expires_at > clock_timestamp()
LEFT JOIN claims lc ON lc.project_id = n.project_id AND lc.node = n.ref
    AND lc.expires_at <= clock_timestamp();

-- Rules (requirements) ----------------------------------------------------------------------

-- A rule belongs to at most one story, and that is one column, never a join table.
CREATE TABLE rules (
    project_id bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    id         bigint NOT NULL DEFAULT next_id('rules'),
    -- The ref of the story that owns the rule, such as 'story:12'. Only a story: not an epic and
    -- not a task.
    story      text,
    title      text NOT NULL,
    body       text NOT NULL DEFAULT '',
    status     text NOT NULL DEFAULT 'held',
    version    integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    search     tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', title), 'A') || setweight(to_tsvector('english', body), 'B')
    ) STORED,
    PRIMARY KEY (project_id, id),
    CONSTRAINT rule_title_is_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT rule_status_is_known CHECK (status IN ('held', 'settled', 'dropped')),
    CONSTRAINT rule_version_starts_at_one CHECK (version >= 1),
    CONSTRAINT rule_is_in_a_story CHECK (story IS NULL OR starts_with(story, 'story:')),
    CONSTRAINT rule_belongs_to_a_story
        FOREIGN KEY (project_id, story) REFERENCES nodes (project_id, ref)
);

CREATE INDEX rules_story_idx ON rules (project_id, story);
CREATE INDEX rules_search_idx ON rules USING gin (search);

CREATE FUNCTION rules_guard_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.project_id <> OLD.project_id THEN
        RAISE EXCEPTION 'A rule cannot move to another project.';
    END IF;
    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'Every change to a rule must raise its version by exactly one (was %, got %).',
            OLD.version, NEW.version;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END
$$;

CREATE TRIGGER rules_guard_update BEFORE UPDATE ON rules
    FOR EACH ROW EXECUTE FUNCTION rules_guard_update();

-- Facts ---------------------------------------------------------------------------------------

-- A fact is a statement with its source, and a record: it is never edited. A change is a newer fact
-- that supersedes the old one, or a withdrawal. Confirmation is a field, not a record of its own: it
-- says that a named person has looked at the fact, and nothing about whether it is true.
CREATE TABLE facts (
    project_id       bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    id               bigint NOT NULL DEFAULT next_id('facts'),
    text             text NOT NULL,
    -- Where it came from, in plain words.
    source           text NOT NULL,
    recorded_by      text NOT NULL,
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    status           text NOT NULL DEFAULT 'active',
    superseded_by_id bigint,
    -- Why it was superseded or withdrawn. Present exactly when it is no longer active.
    status_reason    text,
    -- Unconfirmed while both are empty. Only a person's name is a confirmation.
    confirmed_by     text,
    confirmed_at     timestamptz,
    -- A fact's source is searchable too, a little less than what it says.
    search           tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', text), 'A')
        || setweight(to_tsvector('english', source), 'B')
    ) STORED,
    PRIMARY KEY (project_id, id),
    CONSTRAINT fact_status_is_known CHECK (status IN ('active', 'superseded', 'withdrawn')),
    CONSTRAINT fact_text_is_not_blank CHECK (btrim(text) <> ''),
    CONSTRAINT fact_source_is_not_blank CHECK (btrim(source) <> ''),
    CONSTRAINT fact_recorded_by_is_not_blank CHECK (btrim(recorded_by) <> ''),
    CONSTRAINT confirmation_names_a_person_and_a_time CHECK (
        (confirmed_by IS NULL) = (confirmed_at IS NULL)
        AND (confirmed_by IS NULL OR btrim(confirmed_by) <> '')
    ),
    CONSTRAINT fact_is_superseded_exactly_when_replaced
        CHECK ((status = 'superseded') = (superseded_by_id IS NOT NULL)),
    -- A replacement is always recorded after what it replaces, so replacements cannot form a circle.
    CONSTRAINT fact_is_replaced_by_a_later_one
        CHECK (superseded_by_id IS NULL OR superseded_by_id > id),
    CONSTRAINT fact_has_a_reason_once_it_has_ended CHECK (
        (status = 'active') = (status_reason IS NULL)
        AND (status_reason IS NULL OR btrim(status_reason) <> '')
    ),
    CONSTRAINT fact_is_replaced_by_a_fact
        FOREIGN KEY (project_id, superseded_by_id) REFERENCES facts (project_id, id)
);

CREATE INDEX facts_superseded_by_idx ON facts (project_id, superseded_by_id);
CREATE INDEX facts_search_idx ON facts USING gin (search);

-- A fact that has ended cannot change at all, so what it was confirmed as stays as it was. While it
-- is active only its confirmation can change, and ending it never changes it in the same step.
CREATE FUNCTION facts_are_a_record() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'A fact is a record and cannot be deleted. Withdraw it or supersede it instead.';
    END IF;
    IF OLD.status <> 'active' THEN
        RAISE EXCEPTION 'A superseded or withdrawn fact cannot change again.';
    END IF;
    IF (NEW.project_id, NEW.text, NEW.source, NEW.recorded_by, NEW.recorded_at)
       IS DISTINCT FROM
       (OLD.project_id, OLD.text, OLD.source, OLD.recorded_by, OLD.recorded_at)
    THEN
        RAISE EXCEPTION 'A fact cannot be edited. Supersede it with a newer one, or withdraw it.';
    END IF;
    IF NEW.status <> OLD.status
       AND (NEW.confirmed_by, NEW.confirmed_at) IS DISTINCT FROM (OLD.confirmed_by, OLD.confirmed_at)
    THEN
        RAISE EXCEPTION 'A fact cannot be confirmed or unconfirmed in the same step that ends it.';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER facts_are_a_record BEFORE UPDATE OR DELETE ON facts
    FOR EACH ROW EXECUTE FUNCTION facts_are_a_record();

-- Decisions -----------------------------------------------------------------------------------

-- A decision is a record: it is never edited. A change of mind is a new decision that supersedes
-- the old one, and the old one stays, marked as superseded and pointing at its replacement. A
-- decision can also be withdrawn. One that applies throughout its project is project_wide: it is
-- the same kind of record, not a different one. Confirmation works as it does for a fact.
CREATE TABLE decisions (
    project_id       bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    id               bigint NOT NULL DEFAULT next_id('decisions'),
    title            text NOT NULL,
    text             text NOT NULL,
    reason           text NOT NULL,
    -- The person who made the decision. Whoever recorded it is in the event log.
    decided_by       text NOT NULL,
    decided_on       date NOT NULL,
    project_wide     boolean NOT NULL DEFAULT false,
    status           text NOT NULL DEFAULT 'active',
    superseded_by_id bigint,
    -- Why it was withdrawn. Present exactly when it was.
    status_reason    text,
    confirmed_by     text,
    confirmed_at     timestamptz,
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    search           tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', title), 'A')
        || setweight(to_tsvector('english', text || ' ' || reason), 'B')
    ) STORED,
    PRIMARY KEY (project_id, id),
    CONSTRAINT decision_status_is_known CHECK (status IN ('active', 'superseded', 'withdrawn')),
    CONSTRAINT decision_title_is_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT decision_text_is_not_blank CHECK (btrim(text) <> ''),
    CONSTRAINT decision_reason_is_not_blank CHECK (btrim(reason) <> ''),
    CONSTRAINT decision_decided_by_is_not_blank CHECK (btrim(decided_by) <> ''),
    CONSTRAINT decision_is_superseded_exactly_when_replaced
        CHECK ((status = 'superseded') = (superseded_by_id IS NOT NULL)),
    -- A replacement is always recorded after what it replaces, so replacements cannot form a circle.
    CONSTRAINT decision_is_replaced_by_a_later_one
        CHECK (superseded_by_id IS NULL OR superseded_by_id > id),
    CONSTRAINT decision_is_withdrawn_with_a_reason CHECK (
        (status = 'withdrawn') = (status_reason IS NOT NULL)
        AND (status_reason IS NULL OR btrim(status_reason) <> '')
    ),
    CONSTRAINT decision_confirmation_names_a_person_and_a_time CHECK (
        (confirmed_by IS NULL) = (confirmed_at IS NULL)
        AND (confirmed_by IS NULL OR btrim(confirmed_by) <> '')
    ),
    CONSTRAINT decision_is_replaced_by_a_decision
        FOREIGN KEY (project_id, superseded_by_id) REFERENCES decisions (project_id, id)
);

CREATE INDEX decisions_superseded_by_idx ON decisions (project_id, superseded_by_id);
CREATE INDEX decisions_search_idx ON decisions USING gin (search);

CREATE FUNCTION decisions_are_a_record() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'A decision is a record and cannot be deleted. Withdraw it or supersede it with a new decision instead.';
    END IF;
    IF OLD.status <> 'active' THEN
        RAISE EXCEPTION 'A superseded or withdrawn decision cannot change again.';
    END IF;
    IF (NEW.project_id, NEW.title, NEW.text, NEW.reason, NEW.decided_by, NEW.decided_on,
        NEW.project_wide, NEW.recorded_at)
       IS DISTINCT FROM
       (OLD.project_id, OLD.title, OLD.text, OLD.reason, OLD.decided_by, OLD.decided_on,
        OLD.project_wide, OLD.recorded_at)
    THEN
        RAISE EXCEPTION 'A decision cannot be edited. Supersede it with a new decision instead.';
    END IF;
    IF NEW.status <> OLD.status
       AND (NEW.confirmed_by, NEW.confirmed_at) IS DISTINCT FROM (OLD.confirmed_by, OLD.confirmed_at)
    THEN
        RAISE EXCEPTION 'A decision cannot be confirmed or unconfirmed in the same step that ends it.';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER decisions_are_a_record BEFORE UPDATE OR DELETE ON decisions
    FOR EACH ROW EXECUTE FUNCTION decisions_are_a_record();

-- What a decision affects: zero or more nodes and zero or more rules.
CREATE TABLE decision_nodes (
    project_id  bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    decision_id bigint NOT NULL,
    node        text NOT NULL,
    PRIMARY KEY (project_id, decision_id, node),
    FOREIGN KEY (project_id, decision_id) REFERENCES decisions (project_id, id),
    FOREIGN KEY (project_id, node) REFERENCES nodes (project_id, ref)
);

CREATE TABLE decision_rules (
    project_id  bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    decision_id bigint NOT NULL,
    rule_id     bigint NOT NULL,
    PRIMARY KEY (project_id, decision_id, rule_id),
    FOREIGN KEY (project_id, decision_id) REFERENCES decisions (project_id, id),
    FOREIGN KEY (project_id, rule_id) REFERENCES rules (project_id, id)
);

CREATE INDEX decision_nodes_node_idx ON decision_nodes (project_id, node);
CREATE INDEX decision_rules_rule_idx ON decision_rules (project_id, rule_id);

-- A superseded or withdrawn decision keeps the links it had, as a record of what it once applied to.
CREATE FUNCTION decision_links_are_frozen_once_ended() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    the_project bigint;
    the_decision bigint;
    the_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        the_project := OLD.project_id;
        the_decision := OLD.decision_id;
    ELSE
        the_project := NEW.project_id;
        the_decision := NEW.decision_id;
    END IF;
    SELECT status INTO the_status FROM decisions
    WHERE project_id = the_project AND id = the_decision;
    IF the_status <> 'active' THEN
        RAISE EXCEPTION 'A superseded or withdrawn decision keeps the links it had. Link or unlink the decision that replaced it instead.';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER decision_nodes_are_frozen_once_ended BEFORE INSERT OR DELETE ON decision_nodes
    FOR EACH ROW EXECUTE FUNCTION decision_links_are_frozen_once_ended();
CREATE TRIGGER decision_rules_are_frozen_once_ended BEFORE INSERT OR DELETE ON decision_rules
    FOR EACH ROW EXECUTE FUNCTION decision_links_are_frozen_once_ended();

-- A decision as it is read: what it replaced and what it affects.
CREATE VIEW decision_state WITH (security_invoker = true) AS
SELECT d.project_id, d.id, d.title, d.text, d.reason, d.decided_by, d.decided_on, d.project_wide,
       d.status, d.superseded_by_id, d.status_reason, d.confirmed_by, d.confirmed_at,
       CASE WHEN d.confirmed_by IS NULL THEN 'unconfirmed' ELSE 'confirmed' END AS confirmation,
       d.recorded_at,
       coalesce((SELECT array_agg(s.id ORDER BY s.id) FROM decisions s
                 WHERE s.project_id = d.project_id AND s.superseded_by_id = d.id),
                ARRAY[]::bigint[]) AS supersedes_ids,
       coalesce((SELECT array_agg(l.node ORDER BY n.kind_rank, n.number)
                 FROM decision_nodes l
                 JOIN nodes n ON n.project_id = l.project_id AND n.ref = l.node
                 WHERE l.project_id = d.project_id AND l.decision_id = d.id),
                ARRAY[]::text[]) AS nodes,
       coalesce((SELECT array_agg(l.rule_id ORDER BY l.rule_id) FROM decision_rules l
                 WHERE l.project_id = d.project_id AND l.decision_id = d.id),
                ARRAY[]::bigint[]) AS rule_ids
FROM decisions d;

-- What a fact concerns -------------------------------------------------------------------------

-- Zero or more nodes, zero or more rules and zero or more decisions.
CREATE TABLE fact_nodes (
    project_id bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    fact_id    bigint NOT NULL,
    node       text NOT NULL,
    PRIMARY KEY (project_id, fact_id, node),
    FOREIGN KEY (project_id, fact_id) REFERENCES facts (project_id, id),
    FOREIGN KEY (project_id, node) REFERENCES nodes (project_id, ref)
);

CREATE TABLE fact_rules (
    project_id bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    fact_id    bigint NOT NULL,
    rule_id    bigint NOT NULL,
    PRIMARY KEY (project_id, fact_id, rule_id),
    FOREIGN KEY (project_id, fact_id) REFERENCES facts (project_id, id),
    FOREIGN KEY (project_id, rule_id) REFERENCES rules (project_id, id)
);

CREATE TABLE fact_decisions (
    project_id  bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    fact_id     bigint NOT NULL,
    decision_id bigint NOT NULL,
    PRIMARY KEY (project_id, fact_id, decision_id),
    FOREIGN KEY (project_id, fact_id) REFERENCES facts (project_id, id),
    FOREIGN KEY (project_id, decision_id) REFERENCES decisions (project_id, id)
);

CREATE INDEX fact_nodes_node_idx ON fact_nodes (project_id, node);
CREATE INDEX fact_rules_rule_idx ON fact_rules (project_id, rule_id);
CREATE INDEX fact_decisions_decision_idx ON fact_decisions (project_id, decision_id);

-- A fact that has been superseded or withdrawn keeps the links it had.
CREATE FUNCTION fact_links_are_frozen_once_ended() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    the_project bigint;
    the_fact bigint;
    the_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        the_project := OLD.project_id;
        the_fact := OLD.fact_id;
    ELSE
        the_project := NEW.project_id;
        the_fact := NEW.fact_id;
    END IF;
    SELECT status INTO the_status FROM facts
    WHERE project_id = the_project AND id = the_fact;
    IF the_status <> 'active' THEN
        RAISE EXCEPTION 'A superseded or withdrawn fact keeps the links it had.';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER fact_nodes_are_frozen_once_ended BEFORE INSERT OR DELETE ON fact_nodes
    FOR EACH ROW EXECUTE FUNCTION fact_links_are_frozen_once_ended();
CREATE TRIGGER fact_rules_are_frozen_once_ended BEFORE INSERT OR DELETE ON fact_rules
    FOR EACH ROW EXECUTE FUNCTION fact_links_are_frozen_once_ended();
CREATE TRIGGER fact_decisions_are_frozen_once_ended BEFORE INSERT OR DELETE ON fact_decisions
    FOR EACH ROW EXECUTE FUNCTION fact_links_are_frozen_once_ended();

-- A fact as it is read: what it replaced, what it concerns, and whether a person confirmed it.
CREATE VIEW fact_state WITH (security_invoker = true) AS
SELECT f.project_id, f.id, f.text, f.source, f.recorded_by, f.recorded_at, f.status,
       f.superseded_by_id, f.status_reason, f.confirmed_by, f.confirmed_at,
       CASE WHEN f.confirmed_by IS NULL THEN 'unconfirmed' ELSE 'confirmed' END AS confirmation,
       coalesce((SELECT array_agg(s.id ORDER BY s.id) FROM facts s
                 WHERE s.project_id = f.project_id AND s.superseded_by_id = f.id),
                ARRAY[]::bigint[]) AS supersedes_ids,
       coalesce((SELECT array_agg(l.node ORDER BY n.kind_rank, n.number) FROM fact_nodes l
                 JOIN nodes n ON n.project_id = l.project_id AND n.ref = l.node
                 WHERE l.project_id = f.project_id AND l.fact_id = f.id),
                ARRAY[]::text[]) AS nodes,
       coalesce((SELECT array_agg(l.rule_id ORDER BY l.rule_id) FROM fact_rules l
                 WHERE l.project_id = f.project_id AND l.fact_id = f.id),
                ARRAY[]::bigint[]) AS rule_ids,
       coalesce((SELECT array_agg(l.decision_id ORDER BY l.decision_id) FROM fact_decisions l
                 WHERE l.project_id = f.project_id AND l.fact_id = f.id),
                ARRAY[]::bigint[]) AS decision_ids
FROM facts f;

-- Exact-words search and the semantic index ---------------------------------------------------

-- Every thing a person can search for: its type, its id and its ref, and its search column. The type
-- is the one a person sees: epic, story, task, rule (shown as a requirement), decision or fact. For
-- an epic, story or task the id is its number, which is unique within its type, so (item_type, id)
-- names one thing.
CREATE VIEW search_documents WITH (security_invoker = true) AS
SELECT n.project_id, n.kind AS item_type, n.number AS id, n.ref, n.search FROM nodes n
UNION ALL
SELECT r.project_id, 'rule', r.id, 'rule:' || r.id::text, r.search FROM rules r
UNION ALL
SELECT d.project_id, 'decision', d.id, 'decision:' || d.id::text, d.search FROM decisions d
UNION ALL
SELECT f.project_id, 'fact', f.id, 'fact:' || f.id::text, f.search FROM facts f;

-- What the semantic index embeds, and a hash of it, so that an item whose text changed is known
-- to be waiting to be indexed. Epics, stories, rules, decisions and facts are embedded. Tasks are
-- found by exact words only: there are many of them and they are short. The index tables
-- themselves are not in this file: they are derived, and the reindex job builds them (see
-- vrum/indexing.py).
CREATE VIEW index_source WITH (security_invoker = true) AS
SELECT s.project_id, s.item_type, s.id, s.ref, s.content, md5(s.content) AS content_hash
FROM (
    SELECT n.project_id, n.kind AS item_type, n.number AS id, n.ref,
           btrim(n.title || E'\n' || n.body) AS content
    FROM nodes n WHERE n.kind <> 'task'
    UNION ALL
    SELECT r.project_id, 'rule', r.id, 'rule:' || r.id::text, btrim(r.title || E'\n' || r.body)
    FROM rules r
    UNION ALL
    SELECT d.project_id, 'decision', d.id, 'decision:' || d.id::text,
           btrim(d.title || E'\n' || d.text || E'\n' || d.reason)
    FROM decisions d
    UNION ALL
    SELECT f.project_id, 'fact', f.id, 'fact:' || f.id::text, btrim(f.text) FROM facts f
) s;

-- The event log ----------------------------------------------------------------------------

-- An event shows in the history of every node and rule it names. For a link, node is where it
-- starts and other_node where it ends. For a rule, node is the story the rule is in after the
-- event and other_node the story it was in before, when that was a different one. A decision
-- event names the decision, and also the node or rule when the event is about a link to it. A fact
-- event names the fact, and also the node, rule or decision it concerns when the event is about a
-- link to it, so it shows in the history of each of them.
CREATE TABLE events (
    project_id    bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    id            bigint NOT NULL DEFAULT next_id('events'),
    node          text,
    other_node    text,
    rule_id       bigint,
    decision_id   bigint,
    fact_id       bigint,
    action        text NOT NULL,
    actor         text NOT NULL,
    at            timestamptz NOT NULL DEFAULT now(),
    -- The fields that changed, as they were and as they became. `before` is empty for a create.
    before        jsonb,
    after         jsonb NOT NULL,
    -- The transaction that wrote it. A feed of changes reads only events from transactions that
    -- have all finished, so an event that commits late (with a lower id than one that committed
    -- earlier) is never skipped by someone following the feed.
    xid           xid8 NOT NULL DEFAULT pg_current_xact_id(),
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id, node) REFERENCES nodes (project_id, ref),
    FOREIGN KEY (project_id, other_node) REFERENCES nodes (project_id, ref),
    FOREIGN KEY (project_id, rule_id) REFERENCES rules (project_id, id),
    FOREIGN KEY (project_id, decision_id) REFERENCES decisions (project_id, id),
    FOREIGN KEY (project_id, fact_id) REFERENCES facts (project_id, id),
    CONSTRAINT event_action_is_known CHECK (action IN (
        'create', 'update', 'move',
        'link_added', 'link_removed',
        'claimed', 'claim_renewed', 'claim_released', 'handoff',
        'rule_created', 'rule_updated', 'rule_placed', 'rule_moved',
        'decision_recorded', 'decision_superseded', 'decision_withdrawn', 'decision_linked',
        'decision_unlinked', 'decision_confirmed', 'decision_unconfirmed',
        'fact_recorded', 'fact_superseded', 'fact_withdrawn', 'fact_linked', 'fact_unlinked',
        'fact_confirmed', 'fact_unconfirmed'
    )),
    CONSTRAINT event_actor_is_not_blank CHECK (btrim(actor) <> ''),
    -- A handoff says how the holder left the work and what the next person must know. A blank
    -- note is refused here too, whatever the tools check.
    CONSTRAINT handoff_has_an_outcome_and_a_note CHECK (
        action <> 'handoff' OR (
            coalesce(after->>'outcome', '') IN ('done', 'blocked', 'dropped', 'released')
            AND btrim(coalesce(after->>'note', '')) <> ''
        )
    ),
    CONSTRAINT event_names_what_it_is_about CHECK (
        CASE
            WHEN starts_with(action, 'rule_')
                THEN rule_id IS NOT NULL AND decision_id IS NULL AND fact_id IS NULL
            WHEN starts_with(action, 'decision_') THEN decision_id IS NOT NULL AND fact_id IS NULL
            WHEN starts_with(action, 'fact_') THEN fact_id IS NOT NULL
            ELSE node IS NOT NULL AND rule_id IS NULL AND decision_id IS NULL
                AND fact_id IS NULL
        END
    )
);

CREATE INDEX events_node_idx ON events (project_id, node, id);
CREATE INDEX events_other_node_idx ON events (project_id, other_node, id)
    WHERE other_node IS NOT NULL;
CREATE INDEX events_rule_idx ON events (project_id, rule_id, id) WHERE rule_id IS NOT NULL;
CREATE INDEX events_decision_idx ON events (project_id, decision_id, id)
    WHERE decision_id IS NOT NULL;
CREATE INDEX events_fact_idx ON events (project_id, fact_id, id) WHERE fact_id IS NOT NULL;
CREATE INDEX events_feed_idx ON events (project_id, xid, id);

CREATE FUNCTION events_refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'The event log is append-only: events cannot be changed or deleted.';
END
$$;

CREATE TRIGGER events_are_append_only BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION events_refuse_change();

-- The wall between projects ---------------------------------------------------------------------

-- Every table that holds a project's work shows, and takes, only the rows of the project the
-- transaction was started for (current_project), and none when no project was named. The project
-- is also part of every key above, so a row cannot refer to a row of another project even when it
-- is written by someone who is not bound by this (the database's own owner, say).
DO $$
DECLARE
    work_table text;
BEGIN
    FOREACH work_table IN ARRAY ARRAY[
        'nodes', 'links', 'claims', 'rules', 'facts', 'decisions', 'decision_nodes',
        'decision_rules', 'fact_nodes', 'fact_rules', 'fact_decisions', 'events'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', work_table);
        EXECUTE format(
            'CREATE POLICY project_only ON %I USING (project_id = current_project()) '
            'WITH CHECK (project_id = current_project())', work_table);
    END LOOP;
END
$$;

-- What a project's role may do, and nothing more. It cannot reach accounts, sessions, projects or
-- keys at all.
GRANT SELECT, INSERT, UPDATE ON nodes, rules, facts, decisions TO vrum_project;
GRANT SELECT, INSERT, DELETE ON links, decision_nodes, decision_rules, fact_nodes, fact_rules,
    fact_decisions TO vrum_project;
GRANT SELECT, INSERT, UPDATE, DELETE ON claims TO vrum_project;
GRANT SELECT, INSERT ON events TO vrum_project;
GRANT SELECT ON node_state, decision_state, fact_state, search_documents, index_source
    TO vrum_project;
