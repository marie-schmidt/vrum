-- The whole database, in one file. Vrum is a prototype with no customer data, so there are no
-- migrations: this file is applied to an empty database, and a change to it means rebuilding the
-- database from scratch (docker compose down --volumes, then up again).
-- The database itself refuses anything that breaks the hierarchy, the links, the claims or the
-- history.

CREATE EXTENSION IF NOT EXISTS vector;

-- Epics, stories and tasks ------------------------------------------------------------------

CREATE TABLE nodes (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind        text NOT NULL,
    parent_id   bigint,
    -- The kind the parent must have: a story sits under an epic, a task under a story.
    -- Worked out from `kind`, and checked against the parent by the foreign key below.
    parent_kind text GENERATED ALWAYS AS (
        CASE kind WHEN 'story' THEN 'epic' WHEN 'task' THEN 'story' END
    ) STORED,
    title       text NOT NULL,
    body        text NOT NULL DEFAULT '',
    -- `ready` is never stored: it is worked out from the links (see node_state below).
    status      text NOT NULL DEFAULT 'new',
    priority    text NOT NULL DEFAULT 'normal',
    version     integer NOT NULL DEFAULT 1,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT node_kind_is_known CHECK (kind IN ('epic', 'story', 'task')),
    CONSTRAINT node_title_is_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT node_status_is_known
        CHECK (status IN ('new', 'in_progress', 'blocked', 'done', 'dropped')),
    CONSTRAINT node_priority_is_known CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    CONSTRAINT node_version_starts_at_one CHECK (version >= 1),
    CONSTRAINT node_has_a_parent_unless_epic CHECK ((kind = 'epic') = (parent_id IS NULL)),
    CONSTRAINT node_id_and_kind_are_unique UNIQUE (id, kind),
    CONSTRAINT node_parent_has_the_right_kind
        FOREIGN KEY (parent_id, parent_kind) REFERENCES nodes (id, kind)
);

CREATE INDEX nodes_parent_idx ON nodes (parent_id);

CREATE FUNCTION nodes_guard_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind <> OLD.kind THEN
        RAISE EXCEPTION 'A node cannot change its kind (it is % and stays that way).', OLD.kind;
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
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id  bigint NOT NULL REFERENCES nodes (id),
    target_id  bigint NOT NULL REFERENCES nodes (id),
    link_type  text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT link_type_is_known
        CHECK (link_type IN ('needs_first', 'supersedes', 'duplicates', 'relates_to')),
    CONSTRAINT link_is_not_to_the_same_node CHECK (source_id <> target_id),
    CONSTRAINT link_is_not_repeated UNIQUE (source_id, target_id, link_type)
);

-- `duplicates` and `relates_to` work both ways, so A to B and B to A are the same link.
CREATE UNIQUE INDEX link_two_way_is_not_repeated
    ON links (link_type, LEAST(source_id, target_id), GREATEST(source_id, target_id))
    WHERE link_type IN ('duplicates', 'relates_to');

CREATE INDEX links_target_idx ON links (target_id);

-- A node cannot wait, directly or through others, for itself. Adding source -> target makes a
-- circle exactly when target already waits for source.
CREATE FUNCTION links_refuse_a_circle_of_waiting() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    circle bigint[];
BEGIN
    -- A link to the same node is refused by its own rule, which says so more plainly.
    IF NEW.link_type <> 'needs_first' OR NEW.source_id = NEW.target_id THEN
        RETURN NEW;
    END IF;
    -- One at a time, so two links added together cannot each look fine and together make a circle.
    PERFORM pg_advisory_xact_lock(7431900002);
    WITH RECURSIVE reach (id, path) AS (
        SELECT NEW.target_id, ARRAY[NEW.target_id]
        UNION ALL
        SELECT l.target_id, r.path || l.target_id
        FROM links l JOIN reach r ON l.source_id = r.id
        WHERE l.link_type = 'needs_first' AND NOT l.target_id = ANY (r.path)
    )
    SELECT path INTO circle FROM reach WHERE id = NEW.source_id LIMIT 1;
    IF circle IS NOT NULL THEN
        RAISE EXCEPTION '%',
            'That link would make a circle of waiting: node ' || NEW.source_id
            || ' would need node ' || array_to_string(circle, ', which needs node ')
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
-- until it is taken over or released.
CREATE TABLE claims (
    node_id    bigint PRIMARY KEY,
    node_kind  text NOT NULL,
    actor      text NOT NULL,
    claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    CONSTRAINT claim_is_for_a_story_or_task CHECK (node_kind IN ('story', 'task')),
    CONSTRAINT claim_actor_is_not_blank CHECK (btrim(actor) <> ''),
    CONSTRAINT claim_lease_is_in_the_future CHECK (expires_at > claimed_at),
    CONSTRAINT claim_is_on_a_real_node FOREIGN KEY (node_id, node_kind) REFERENCES nodes (id, kind)
);

-- A node as it is read: its status with `ready` worked out, what it still waits for, and who
-- holds a claim on it right now. This is the one place that decides what `ready` means.
CREATE VIEW node_state AS
SELECT n.id, n.kind, n.parent_id, n.title, n.body,
       CASE WHEN n.status = 'new' AND cardinality(w.ids) = 0 THEN 'ready' ELSE n.status END
           AS status,
       n.priority, n.version, n.created_at, n.updated_at,
       w.ids AS waiting_on,
       c.actor AS claimed_by,
       c.expires_at AS claim_expires_at
FROM nodes n
CROSS JOIN LATERAL (
    SELECT coalesce(array_agg(t.id ORDER BY t.id), ARRAY[]::bigint[]) AS ids
    FROM links l JOIN nodes t ON t.id = l.target_id
    WHERE l.source_id = n.id AND l.link_type = 'needs_first' AND t.status <> 'done'
) w
LEFT JOIN claims c ON c.node_id = n.id AND c.expires_at > clock_timestamp();

-- Rules (requirements) ----------------------------------------------------------------------

-- A rule belongs to at most one story, and that is one column, never a join table.
CREATE TABLE rules (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    story_id   bigint,
    -- Present only when the rule is placed, so that the foreign key below accepts nothing but a
    -- story: not an epic, not a task.
    story_kind text GENERATED ALWAYS AS (CASE WHEN story_id IS NOT NULL THEN 'story' END) STORED,
    title      text NOT NULL,
    body       text NOT NULL DEFAULT '',
    status     text NOT NULL DEFAULT 'held',
    version    integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT rule_title_is_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT rule_status_is_known CHECK (status IN ('held', 'settled', 'dropped')),
    CONSTRAINT rule_version_starts_at_one CHECK (version >= 1),
    CONSTRAINT rule_belongs_to_a_story
        FOREIGN KEY (story_id, story_kind) REFERENCES nodes (id, kind)
);

CREATE INDEX rules_story_idx ON rules (story_id);

CREATE FUNCTION rules_guard_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
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

-- The event log ----------------------------------------------------------------------------

-- An event shows in the history of every node it names. For a link, node_id is where it starts
-- and other_node_id where it ends. For a rule, node_id is the story the rule is in after the
-- event and other_node_id the story it was in before, when that was a different one.
CREATE TABLE events (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    node_id       bigint REFERENCES nodes (id),
    other_node_id bigint REFERENCES nodes (id),
    rule_id       bigint REFERENCES rules (id),
    action        text NOT NULL,
    actor         text NOT NULL,
    at            timestamptz NOT NULL DEFAULT now(),
    -- The fields that changed, as they were and as they became. `before` is empty for a create.
    before        jsonb,
    after         jsonb NOT NULL,
    CONSTRAINT event_action_is_known CHECK (action IN (
        'create', 'update', 'move',
        'link_added', 'link_removed',
        'claimed', 'claim_renewed', 'claim_released',
        'rule_created', 'rule_updated', 'rule_placed', 'rule_moved'
    )),
    CONSTRAINT event_actor_is_not_blank CHECK (btrim(actor) <> ''),
    CONSTRAINT event_is_about_a_node_or_a_rule CHECK (
        starts_with(action, 'rule_') = (rule_id IS NOT NULL)
        AND (rule_id IS NOT NULL OR node_id IS NOT NULL)
    )
);

CREATE INDEX events_node_idx ON events (node_id, id);
CREATE INDEX events_other_node_idx ON events (other_node_id, id) WHERE other_node_id IS NOT NULL;
CREATE INDEX events_rule_idx ON events (rule_id, id) WHERE rule_id IS NOT NULL;

CREATE FUNCTION events_refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'The event log is append-only: events cannot be changed or deleted.';
END
$$;

CREATE TRIGGER events_are_append_only BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION events_refuse_change();
