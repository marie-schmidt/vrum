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
    CONSTRAINT node_kind_is_known CHECK (kind IN ('epic', 'story', 'task')),
    CONSTRAINT node_title_is_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT node_status_is_known
        CHECK (status IN ('new', 'in_progress', 'blocked', 'done', 'dropped')),
    CONSTRAINT node_priority_is_known CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    CONSTRAINT node_start_is_not_after_target
        CHECK (start_on IS NULL OR target_on IS NULL OR start_on <= target_on),
    CONSTRAINT node_version_starts_at_one CHECK (version >= 1),
    CONSTRAINT node_has_a_parent_unless_epic CHECK ((kind = 'epic') = (parent_id IS NULL)),
    CONSTRAINT node_id_and_kind_are_unique UNIQUE (id, kind),
    CONSTRAINT node_parent_has_the_right_kind
        FOREIGN KEY (parent_id, parent_kind) REFERENCES nodes (id, kind)
);

CREATE INDEX nodes_parent_idx ON nodes (parent_id);
CREATE INDEX nodes_search_idx ON nodes USING gin (search);

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

-- A node as it is read: its status with `ready` worked out, what it still waits for, who holds a
-- claim on it right now, and what its dates add up to. This is the one place that decides what
-- `ready`, overdue, a span and a schedule conflict mean.
--   waiting_on:         the nodes it needs first that are not done.
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
CREATE VIEW node_state AS
SELECT n.id, n.kind, n.parent_id, n.title, n.body, length(n.body) AS body_chars,
       CASE WHEN n.status = 'new' AND cardinality(w.ids) = 0 THEN 'ready' ELSE n.status END
           AS status,
       n.priority, n.start_on, n.target_on, n.version, n.created_at, n.updated_at,
       w.ids AS waiting_on,
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
       lc.expires_at AS lapsed_claim_at
FROM nodes n
CROSS JOIN LATERAL (
    SELECT coalesce(array_agg(t.id ORDER BY t.id), ARRAY[]::bigint[]) AS ids,
           coalesce(array_agg(t.id ORDER BY t.id)
                    FILTER (WHERE n.start_on IS NOT NULL AND t.target_on > n.start_on),
                    ARRAY[]::bigint[]) AS conflicts
    FROM links l JOIN nodes t ON t.id = l.target_id
    WHERE l.source_id = n.id AND l.link_type = 'needs_first' AND t.status <> 'done'
) w
CROSS JOIN LATERAL (
    SELECT CASE
               WHEN n.start_on IS NOT NULL OR n.target_on IS NOT NULL THEN n.start_on
               WHEN n.kind = 'story' THEN (
                   SELECT min(t.start_on) FROM nodes t
                   WHERE t.parent_id = n.id AND t.status <> 'dropped')
               WHEN n.kind = 'epic' THEN (
                   SELECT min(CASE WHEN s.start_on IS NOT NULL OR s.target_on IS NOT NULL
                                   THEN s.start_on
                                   ELSE (SELECT min(t.start_on) FROM nodes t
                                         WHERE t.parent_id = s.id AND t.status <> 'dropped')
                              END)
                   FROM nodes s WHERE s.parent_id = n.id AND s.status <> 'dropped')
           END AS span_start,
           CASE
               WHEN n.start_on IS NOT NULL OR n.target_on IS NOT NULL THEN n.target_on
               WHEN n.kind = 'story' THEN (
                   SELECT max(t.target_on) FROM nodes t
                   WHERE t.parent_id = n.id AND t.status <> 'dropped')
               WHEN n.kind = 'epic' THEN (
                   SELECT max(CASE WHEN s.start_on IS NOT NULL OR s.target_on IS NOT NULL
                                   THEN s.target_on
                                   ELSE (SELECT max(t.target_on) FROM nodes t
                                         WHERE t.parent_id = s.id AND t.status <> 'dropped')
                              END)
                   FROM nodes s WHERE s.parent_id = n.id AND s.status <> 'dropped')
           END AS span_end
) sp
LEFT JOIN claims c ON c.node_id = n.id AND c.expires_at > clock_timestamp()
LEFT JOIN claims lc ON lc.node_id = n.id AND lc.expires_at <= clock_timestamp();

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
    search     tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', title), 'A') || setweight(to_tsvector('english', body), 'B')
    ) STORED,
    CONSTRAINT rule_title_is_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT rule_status_is_known CHECK (status IN ('held', 'settled', 'dropped')),
    CONSTRAINT rule_version_starts_at_one CHECK (version >= 1),
    CONSTRAINT rule_belongs_to_a_story
        FOREIGN KEY (story_id, story_kind) REFERENCES nodes (id, kind)
);

CREATE INDEX rules_story_idx ON rules (story_id);
CREATE INDEX rules_search_idx ON rules USING gin (search);

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

-- Facts and beliefs ---------------------------------------------------------------------------

-- One table for both, because they share what matters: where each came from, who recorded it, and
-- that neither is ever edited. A fact is something known, with its source. A belief is a
-- conclusion drawn from evidence (see belief_evidence); it is only ever a suggestion. A change is a
-- newer item that supersedes the old one, or a retraction, never an edit.
CREATE TABLE memories (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type             text NOT NULL,
    -- Facts only. world: true about the project, its tools or its domain. experience: something that
    -- was done or observed.
    kind             text,
    text             text NOT NULL,
    -- Facts only: where it came from, in plain words.
    source           text,
    -- Facts only: the day it was true or happened, when that is known.
    happened_on      date,
    -- stated: a person or agent wrote it. extracted: a language model proposed it from a quoted
    -- passage (a fact). derived: a language model drew it up from evidence (a belief).
    origin           text NOT NULL DEFAULT 'stated',
    recorded_by      text NOT NULL,
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    status           text NOT NULL DEFAULT 'active',
    superseded_by_id bigint,
    -- Why it was superseded or retracted. Present exactly when it is no longer active.
    status_reason    text,
    -- Beliefs only: the person who has looked at it, and when.
    confirmed_by     text,
    confirmed_at     timestamptz,
    -- A fact's source is searchable too, a little less than what it says.
    search           tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', text), 'A')
        || setweight(to_tsvector('english', coalesce(source, '')), 'B')
    ) STORED,
    CONSTRAINT memory_type_is_known CHECK (type IN ('fact', 'belief')),
    CONSTRAINT memory_kind_is_known CHECK (kind IN ('world', 'experience')),
    CONSTRAINT memory_origin_is_known CHECK (origin IN ('stated', 'extracted', 'derived')),
    CONSTRAINT memory_status_is_known CHECK (status IN ('active', 'superseded', 'retracted')),
    CONSTRAINT memory_text_is_not_blank CHECK (btrim(text) <> ''),
    CONSTRAINT memory_recorded_by_is_not_blank CHECK (btrim(recorded_by) <> ''),
    CONSTRAINT fact_has_a_kind_and_a_source
        CHECK (type <> 'fact' OR (kind IS NOT NULL AND source IS NOT NULL AND btrim(source) <> '')),
    CONSTRAINT belief_has_no_fact_fields
        CHECK (type <> 'belief' OR (kind IS NULL AND source IS NULL AND happened_on IS NULL)),
    CONSTRAINT memory_origin_matches_its_type CHECK (
        origin = 'stated'
        OR (type = 'fact' AND origin = 'extracted')
        OR (type = 'belief' AND origin = 'derived')
    ),
    CONSTRAINT only_a_belief_is_confirmed
        CHECK (type = 'belief' OR (confirmed_by IS NULL AND confirmed_at IS NULL)),
    CONSTRAINT confirmation_names_a_person_and_a_time CHECK (
        (confirmed_by IS NULL) = (confirmed_at IS NULL)
        AND (confirmed_by IS NULL OR btrim(confirmed_by) <> '')
    ),
    CONSTRAINT memory_is_superseded_exactly_when_replaced
        CHECK ((status = 'superseded') = (superseded_by_id IS NOT NULL)),
    -- A replacement is always recorded after what it replaces, so replacements cannot form a circle.
    CONSTRAINT memory_is_replaced_by_a_later_one
        CHECK (superseded_by_id IS NULL OR superseded_by_id > id),
    CONSTRAINT memory_has_a_reason_once_it_has_ended CHECK (
        (status = 'active') = (status_reason IS NULL)
        AND (status_reason IS NULL OR btrim(status_reason) <> '')
    ),
    CONSTRAINT memory_id_and_type_are_unique UNIQUE (id, type),
    -- A fact is replaced by a fact and a belief by a belief.
    CONSTRAINT memory_is_replaced_by_its_own_type
        FOREIGN KEY (superseded_by_id, type) REFERENCES memories (id, type)
);

CREATE INDEX memories_superseded_by_idx ON memories (superseded_by_id);
CREATE INDEX memories_search_idx ON memories USING gin (search);

CREATE FUNCTION memories_are_a_record() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'A fact or belief is a record and cannot be deleted. Retract it or supersede it instead.';
    END IF;
    IF OLD.status <> 'active' THEN
        RAISE EXCEPTION 'A superseded or retracted fact or belief cannot change again.';
    END IF;
    IF (NEW.type, NEW.kind, NEW.text, NEW.source, NEW.happened_on, NEW.origin, NEW.recorded_by,
        NEW.recorded_at)
       IS DISTINCT FROM
       (OLD.type, OLD.kind, OLD.text, OLD.source, OLD.happened_on, OLD.origin, OLD.recorded_by,
        OLD.recorded_at)
    THEN
        RAISE EXCEPTION 'A fact or belief cannot be edited. Supersede it with a newer one, or retract it.';
    END IF;
    IF OLD.confirmed_by IS NOT NULL
       AND (NEW.confirmed_by, NEW.confirmed_at) IS DISTINCT FROM (OLD.confirmed_by, OLD.confirmed_at)
    THEN
        RAISE EXCEPTION 'A confirmation cannot be changed.';
    END IF;
    IF NEW.status <> OLD.status
       AND (NEW.confirmed_by, NEW.confirmed_at) IS DISTINCT FROM (OLD.confirmed_by, OLD.confirmed_at)
    THEN
        RAISE EXCEPTION 'A belief cannot be confirmed in the same step that ends it.';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER memories_are_a_record BEFORE UPDATE OR DELETE ON memories
    FOR EACH ROW EXECUTE FUNCTION memories_are_a_record();

-- Decisions and principles --------------------------------------------------------------------

-- A decision is a record: it is never edited. A change of mind is a new decision that supersedes
-- the old one, and the old one stays, marked as superseded and pointing at its replacement.
CREATE TABLE decisions (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind             text NOT NULL DEFAULT 'decision',
    title            text NOT NULL,
    text             text NOT NULL,
    reason           text NOT NULL,
    -- The person who made the decision. Whoever recorded it is in the event log.
    decided_by       text NOT NULL,
    decided_on       date NOT NULL,
    status           text NOT NULL DEFAULT 'active',
    superseded_by_id bigint REFERENCES decisions (id),
    -- The belief a person promoted to this decision, if it began as one. Only a belief can be.
    promoted_from_id bigint,
    promoted_from_type text GENERATED ALWAYS AS (CASE WHEN promoted_from_id IS NOT NULL THEN 'belief' END) STORED,
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    search           tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', title), 'A')
        || setweight(to_tsvector('english', text || ' ' || reason), 'B')
    ) STORED,
    CONSTRAINT decision_kind_is_known CHECK (kind IN ('decision', 'principle')),
    CONSTRAINT decision_status_is_known CHECK (status IN ('active', 'superseded')),
    CONSTRAINT decision_title_is_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT decision_text_is_not_blank CHECK (btrim(text) <> ''),
    CONSTRAINT decision_reason_is_not_blank CHECK (btrim(reason) <> ''),
    CONSTRAINT decision_decided_by_is_not_blank CHECK (btrim(decided_by) <> ''),
    CONSTRAINT decision_is_superseded_exactly_when_replaced
        CHECK ((status = 'superseded') = (superseded_by_id IS NOT NULL)),
    -- A replacement is always recorded after what it replaces, so replacements cannot form a circle.
    CONSTRAINT decision_is_replaced_by_a_later_one
        CHECK (superseded_by_id IS NULL OR superseded_by_id > id),
    CONSTRAINT decision_is_promoted_from_a_belief
        FOREIGN KEY (promoted_from_id, promoted_from_type) REFERENCES memories (id, type)
);

CREATE INDEX decisions_superseded_by_idx ON decisions (superseded_by_id);
CREATE INDEX decisions_search_idx ON decisions USING gin (search);
CREATE INDEX decisions_promoted_from_idx ON decisions (promoted_from_id) WHERE promoted_from_id IS NOT NULL;

CREATE FUNCTION decisions_are_a_record() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'A decision is a record and cannot be deleted. Supersede it with a new decision instead.';
    END IF;
    IF OLD.status = 'superseded' THEN
        RAISE EXCEPTION 'A superseded decision cannot change again.';
    END IF;
    IF (NEW.kind, NEW.title, NEW.text, NEW.reason, NEW.decided_by, NEW.decided_on,
        NEW.promoted_from_id, NEW.recorded_at)
       IS DISTINCT FROM
       (OLD.kind, OLD.title, OLD.text, OLD.reason, OLD.decided_by, OLD.decided_on,
        OLD.promoted_from_id, OLD.recorded_at)
    THEN
        RAISE EXCEPTION 'A decision cannot be edited. Supersede it with a new decision instead.';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER decisions_are_a_record BEFORE UPDATE OR DELETE ON decisions
    FOR EACH ROW EXECUTE FUNCTION decisions_are_a_record();

-- What a decision affects: zero or more nodes and zero or more rules.
CREATE TABLE decision_nodes (
    decision_id bigint NOT NULL REFERENCES decisions (id),
    node_id     bigint NOT NULL REFERENCES nodes (id),
    PRIMARY KEY (decision_id, node_id)
);

CREATE TABLE decision_rules (
    decision_id bigint NOT NULL REFERENCES decisions (id),
    rule_id     bigint NOT NULL REFERENCES rules (id),
    PRIMARY KEY (decision_id, rule_id)
);

CREATE INDEX decision_nodes_node_idx ON decision_nodes (node_id);
CREATE INDEX decision_rules_rule_idx ON decision_rules (rule_id);

-- A superseded decision keeps the links it had, as a record of what it once applied to.
CREATE FUNCTION decision_links_are_frozen_once_superseded() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    the_decision bigint;
    the_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        the_decision := OLD.decision_id;
    ELSE
        the_decision := NEW.decision_id;
    END IF;
    SELECT status INTO the_status FROM decisions WHERE id = the_decision;
    IF the_status = 'superseded' THEN
        RAISE EXCEPTION 'A superseded decision keeps the links it had. Link or unlink the decision that replaced it instead.';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER decision_nodes_are_frozen_once_superseded BEFORE INSERT OR DELETE ON decision_nodes
    FOR EACH ROW EXECUTE FUNCTION decision_links_are_frozen_once_superseded();
CREATE TRIGGER decision_rules_are_frozen_once_superseded BEFORE INSERT OR DELETE ON decision_rules
    FOR EACH ROW EXECUTE FUNCTION decision_links_are_frozen_once_superseded();

-- A decision as it is read: what it replaced and what it affects.
CREATE VIEW decision_state AS
SELECT d.id, d.kind, d.title, d.text, d.reason, d.decided_by, d.decided_on, d.status,
       d.superseded_by_id, d.promoted_from_id, d.recorded_at,
       coalesce((SELECT array_agg(s.id ORDER BY s.id) FROM decisions s
                 WHERE s.superseded_by_id = d.id), ARRAY[]::bigint[]) AS supersedes_ids,
       coalesce((SELECT array_agg(l.node_id ORDER BY l.node_id) FROM decision_nodes l
                 WHERE l.decision_id = d.id), ARRAY[]::bigint[]) AS node_ids,
       coalesce((SELECT array_agg(l.rule_id ORDER BY l.rule_id) FROM decision_rules l
                 WHERE l.decision_id = d.id), ARRAY[]::bigint[]) AS rule_ids
FROM decisions d;

-- What a fact or belief concerns ---------------------------------------------------------------

-- Zero or more nodes and zero or more rules, for a fact or a belief. Only a fact can also concern
-- decisions: a belief's decisions are its evidence, below.
CREATE TABLE memory_nodes (
    memory_id bigint NOT NULL REFERENCES memories (id),
    node_id   bigint NOT NULL REFERENCES nodes (id),
    PRIMARY KEY (memory_id, node_id)
);

CREATE TABLE memory_rules (
    memory_id bigint NOT NULL REFERENCES memories (id),
    rule_id   bigint NOT NULL REFERENCES rules (id),
    PRIMARY KEY (memory_id, rule_id)
);

CREATE TABLE memory_decisions (
    memory_id   bigint NOT NULL,
    memory_type text GENERATED ALWAYS AS ('fact') STORED,
    decision_id bigint NOT NULL REFERENCES decisions (id),
    PRIMARY KEY (memory_id, decision_id),
    CONSTRAINT only_a_fact_concerns_a_decision
        FOREIGN KEY (memory_id, memory_type) REFERENCES memories (id, type)
);

CREATE INDEX memory_nodes_node_idx ON memory_nodes (node_id);
CREATE INDEX memory_rules_rule_idx ON memory_rules (rule_id);
CREATE INDEX memory_decisions_decision_idx ON memory_decisions (decision_id);

-- A fact or belief that has been superseded or retracted keeps the links it had.
CREATE FUNCTION memory_links_are_frozen_once_ended() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    the_memory bigint;
    the_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        the_memory := OLD.memory_id;
    ELSE
        the_memory := NEW.memory_id;
    END IF;
    SELECT status INTO the_status FROM memories WHERE id = the_memory;
    IF the_status <> 'active' THEN
        RAISE EXCEPTION 'A superseded or retracted fact or belief keeps the links it had.';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER memory_nodes_are_frozen_once_ended BEFORE INSERT OR DELETE ON memory_nodes
    FOR EACH ROW EXECUTE FUNCTION memory_links_are_frozen_once_ended();
CREATE TRIGGER memory_rules_are_frozen_once_ended BEFORE INSERT OR DELETE ON memory_rules
    FOR EACH ROW EXECUTE FUNCTION memory_links_are_frozen_once_ended();
CREATE TRIGGER memory_decisions_are_frozen_once_ended BEFORE INSERT OR DELETE ON memory_decisions
    FOR EACH ROW EXECUTE FUNCTION memory_links_are_frozen_once_ended();

-- What a belief rests on ------------------------------------------------------------------------

-- Each row is one piece of evidence: a fact or a decision, never another belief, so a conclusion is
-- always one step from what it rests on. The generated types make the foreign keys accept nothing
-- but a belief on one side and a fact on the other.
CREATE TABLE belief_evidence (
    belief_id   bigint NOT NULL,
    belief_type text GENERATED ALWAYS AS ('belief') STORED,
    fact_id     bigint,
    fact_type   text GENERATED ALWAYS AS (CASE WHEN fact_id IS NOT NULL THEN 'fact' END) STORED,
    decision_id bigint REFERENCES decisions (id),
    CONSTRAINT evidence_is_a_fact_or_a_decision CHECK ((fact_id IS NULL) <> (decision_id IS NULL)),
    CONSTRAINT evidence_belongs_to_a_belief
        FOREIGN KEY (belief_id, belief_type) REFERENCES memories (id, type),
    CONSTRAINT evidence_is_never_a_belief
        FOREIGN KEY (fact_id, fact_type) REFERENCES memories (id, type)
);

CREATE UNIQUE INDEX belief_evidence_fact_idx ON belief_evidence (belief_id, fact_id)
    WHERE fact_id IS NOT NULL;
CREATE UNIQUE INDEX belief_evidence_decision_idx ON belief_evidence (belief_id, decision_id)
    WHERE decision_id IS NOT NULL;
CREATE INDEX belief_evidence_by_fact_idx ON belief_evidence (fact_id) WHERE fact_id IS NOT NULL;
CREATE INDEX belief_evidence_by_decision_idx
    ON belief_evidence (decision_id) WHERE decision_id IS NOT NULL;

CREATE FUNCTION belief_evidence_is_a_record() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    the_status text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'The evidence of a belief cannot be changed or removed. Supersede the belief with a revised one instead.';
    END IF;
    IF NEW.fact_id IS NOT NULL THEN
        SELECT status INTO the_status FROM memories WHERE id = NEW.fact_id;
    ELSE
        SELECT status INTO the_status FROM decisions WHERE id = NEW.decision_id;
    END IF;
    IF the_status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION 'Evidence for a new belief must be an active fact or decision.';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER belief_evidence_is_a_record BEFORE INSERT OR UPDATE OR DELETE ON belief_evidence
    FOR EACH ROW EXECUTE FUNCTION belief_evidence_is_a_record();

-- Checked when the transaction commits, because the evidence is added right after the belief.
CREATE FUNCTION a_belief_needs_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.type = 'belief'
       AND NOT EXISTS (SELECT 1 FROM belief_evidence WHERE belief_id = NEW.id)
    THEN
        RAISE EXCEPTION 'A belief needs evidence: at least one fact or decision it rests on.';
    END IF;
    RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER a_belief_needs_evidence AFTER INSERT ON memories
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION a_belief_needs_evidence();

-- A fact or belief as it is read: what it replaced, what it concerns, what a belief rests on,
-- whether that belief is stale, and the decisions made from it.
CREATE VIEW memory_state AS
SELECT m.id, m.type, m.kind, m.text, m.source, m.happened_on, m.origin, m.recorded_by,
       m.recorded_at, m.status, m.superseded_by_id, m.status_reason, m.confirmed_by,
       m.confirmed_at,
       coalesce((SELECT array_agg(s.id ORDER BY s.id) FROM memories s
                 WHERE s.superseded_by_id = m.id), ARRAY[]::bigint[]) AS supersedes_ids,
       coalesce((SELECT array_agg(l.node_id ORDER BY l.node_id) FROM memory_nodes l
                 WHERE l.memory_id = m.id), ARRAY[]::bigint[]) AS node_ids,
       coalesce((SELECT array_agg(l.rule_id ORDER BY l.rule_id) FROM memory_rules l
                 WHERE l.memory_id = m.id), ARRAY[]::bigint[]) AS rule_ids,
       coalesce((SELECT array_agg(l.decision_id ORDER BY l.decision_id) FROM memory_decisions l
                 WHERE l.memory_id = m.id), ARRAY[]::bigint[]) AS decision_ids,
       coalesce((SELECT jsonb_agg(e.item ORDER BY e.sort_group, e.sort_id)
                 FROM (SELECT jsonb_build_object('type', 'fact', 'id', f.id, 'status', f.status,
                              'replaced_by_id', f.superseded_by_id,
                              'reason', f.status_reason) AS item, 0 AS sort_group, f.id AS sort_id
                       FROM belief_evidence be JOIN memories f ON f.id = be.fact_id
                       WHERE be.belief_id = m.id
                       UNION ALL
                       SELECT jsonb_build_object('type', 'decision', 'id', d.id,
                              'status', d.status, 'replaced_by_id', d.superseded_by_id,
                              'reason', NULL), 1, d.id
                       FROM belief_evidence be JOIN decisions d ON d.id = be.decision_id
                       WHERE be.belief_id = m.id) e), '[]'::jsonb) AS evidence,
       (SELECT count(*) FROM belief_evidence be WHERE be.belief_id = m.id)::integer
           AS evidence_count,
       (m.type = 'belief' AND m.status = 'active' AND EXISTS (
           SELECT 1 FROM belief_evidence be
           LEFT JOIN memories f ON f.id = be.fact_id
           LEFT JOIN decisions d ON d.id = be.decision_id
           WHERE be.belief_id = m.id AND coalesce(f.status, d.status) <> 'active'
       )) AS stale,
       coalesce((SELECT array_agg(d.id ORDER BY d.id) FROM decisions d
                 WHERE d.promoted_from_id = m.id), ARRAY[]::bigint[]) AS promoted_decision_ids
FROM memories m;

-- Exact-words search and the semantic index ---------------------------------------------------

-- Every thing a person can search for: the id and its search column. The type is the one a person
-- sees: a decision's own kind (decision or principle) and a memory's own type (fact or belief).
CREATE VIEW search_documents AS
SELECT n.kind AS item_type, n.id, n.search FROM nodes n
UNION ALL
SELECT 'rule', r.id, r.search FROM rules r
UNION ALL
SELECT d.kind, d.id, d.search FROM decisions d
UNION ALL
SELECT m.type, m.id, m.search FROM memories m;

-- What the semantic index embeds, and a hash of it, so that an item whose text changed is known
-- to be waiting to be indexed. Epics, stories, rules, decisions, principles, facts and beliefs are
-- embedded. Tasks are found by exact words only: there are many of them and they are short. The
-- index tables themselves are not in this file: they are derived, and the reindex job builds them
-- (see vrum/indexing.py).
CREATE VIEW index_source AS
SELECT s.item_type, s.id, s.content, md5(s.content) AS content_hash
FROM (
    SELECT n.kind AS item_type, n.id, btrim(n.title || E'\n' || n.body) AS content
    FROM nodes n WHERE n.kind <> 'task'
    UNION ALL
    SELECT 'rule', r.id, btrim(r.title || E'\n' || r.body) FROM rules r
    UNION ALL
    SELECT d.kind, d.id, btrim(d.title || E'\n' || d.text || E'\n' || d.reason) FROM decisions d
    UNION ALL
    SELECT m.type, m.id, btrim(m.text) FROM memories m
) s;

-- The event log ----------------------------------------------------------------------------

-- An event shows in the history of every node and rule it names. For a link, node_id is where it
-- starts and other_node_id where it ends. For a rule, node_id is the story the rule is in after the
-- event and other_node_id the story it was in before, when that was a different one. A decision
-- event names the decision, and also the node or rule when the event is about a link to it. A fact
-- or belief event names the memory, and also the node, rule or decision it concerns when the event is
-- about a link to it, so it shows in the history of each of them.
CREATE TABLE events (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    node_id       bigint REFERENCES nodes (id),
    other_node_id bigint REFERENCES nodes (id),
    rule_id       bigint REFERENCES rules (id),
    decision_id   bigint REFERENCES decisions (id),
    memory_id     bigint REFERENCES memories (id),
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
    CONSTRAINT event_action_is_known CHECK (action IN (
        'create', 'update', 'move',
        'link_added', 'link_removed',
        'claimed', 'claim_renewed', 'claim_released', 'handoff',
        'rule_created', 'rule_updated', 'rule_placed', 'rule_moved',
        'decision_recorded', 'decision_superseded', 'decision_linked', 'decision_unlinked',
        'memory_recorded', 'memory_superseded', 'memory_retracted', 'memory_linked',
        'memory_unlinked', 'memory_confirmed', 'memory_promoted'
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
                THEN rule_id IS NOT NULL AND decision_id IS NULL AND memory_id IS NULL
            WHEN starts_with(action, 'decision_') THEN decision_id IS NOT NULL AND memory_id IS NULL
            WHEN starts_with(action, 'memory_') THEN memory_id IS NOT NULL
            ELSE node_id IS NOT NULL AND rule_id IS NULL AND decision_id IS NULL
                AND memory_id IS NULL
        END
    )
);

CREATE INDEX events_node_idx ON events (node_id, id);
CREATE INDEX events_other_node_idx ON events (other_node_id, id) WHERE other_node_id IS NOT NULL;
CREATE INDEX events_rule_idx ON events (rule_id, id) WHERE rule_id IS NOT NULL;
CREATE INDEX events_decision_idx ON events (decision_id, id) WHERE decision_id IS NOT NULL;
CREATE INDEX events_memory_idx ON events (memory_id, id) WHERE memory_id IS NOT NULL;
CREATE INDEX events_feed_idx ON events (xid, id);

CREATE FUNCTION events_refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'The event log is append-only: events cannot be changed or deleted.';
END
$$;

CREATE TRIGGER events_are_append_only BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION events_refuse_change();
