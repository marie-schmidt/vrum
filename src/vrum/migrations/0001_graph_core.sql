-- The graph core: epics, stories and tasks, and the append-only event log.
-- The database itself refuses anything that breaks the hierarchy or the history.

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
    -- `ready` is never stored: it is worked out from the links.
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

CREATE TABLE events (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    node_id bigint NOT NULL REFERENCES nodes (id),
    action  text NOT NULL,
    actor   text NOT NULL,
    at      timestamptz NOT NULL DEFAULT now(),
    -- The fields that changed, as they were and as they became. `before` is empty for a create.
    before  jsonb,
    after   jsonb NOT NULL,
    CONSTRAINT event_action_is_known CHECK (action IN ('create', 'update', 'move')),
    CONSTRAINT event_actor_is_not_blank CHECK (btrim(actor) <> '')
);

CREATE INDEX events_node_idx ON events (node_id, id);

CREATE FUNCTION events_refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'The event log is append-only: events cannot be changed or deleted.';
END
$$;

CREATE TRIGGER events_are_append_only BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION events_refuse_change();
