-- Links between nodes, readiness, and claims with leases.

-- The event log now also records links and claims. A link event belongs to both of its nodes.
ALTER TABLE events ADD COLUMN other_node_id bigint REFERENCES nodes (id);
CREATE INDEX events_other_node_idx ON events (other_node_id, id) WHERE other_node_id IS NOT NULL;
ALTER TABLE events DROP CONSTRAINT event_action_is_known;
ALTER TABLE events ADD CONSTRAINT event_action_is_known CHECK (action IN (
    'create', 'update', 'move',
    'link_added', 'link_removed',
    'claimed', 'claim_renewed', 'claim_released'
));

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
