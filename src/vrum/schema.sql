-- The whole database, in one file. Vrum is a prototype with no customer data, so there are no
-- migrations: this file is applied to an empty database, and a change to it means rebuilding the
-- database from scratch (docker compose down --volumes, then up again).
-- The database itself refuses anything that breaks the hierarchy, the links, the claims, the
-- history, or the walls between projects.

CREATE EXTENSION IF NOT EXISTS vector;

-- Accounts, sessions, projects, their people and agent keys ---------------------------------
-- None of this is graph content. The role every project's work runs as (below) has no access to
-- any of these tables, so a bug in a tool can never read a password hash, a session or a key.

CREATE TABLE accounts (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Written in lower case, so that the same address is never two accounts.
    email         text NOT NULL,
    display_name  text NOT NULL,
    -- Salted and slow (scrypt), as 'scrypt$N$r$p$salt$hash'. Never the password itself. NULL for
    -- an account that signing in with a provider made and that has not chosen a password: it
    -- cannot sign in with one.
    password_hash text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    -- When this address was shown to be the person's: by a link that reached its mailbox (a
    -- confirmation, a password reset, or an invitation sent to it), or by a provider that vouched
    -- for it when it made the account. NULL until then. Nothing is withheld from an account that
    -- has not confirmed its address, but a provider is only joined to an account by address when
    -- the address is confirmed.
    email_verified_at timestamptz,
    -- The provider (see account_identities) whose sign-in made this account, if one did.
    signed_up_with text,
    CONSTRAINT account_email_is_unique UNIQUE (email),
    CONSTRAINT account_email_is_lower_case CHECK (email = lower(btrim(email))),
    CONSTRAINT account_email_looks_like_one CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    CONSTRAINT account_name_is_not_blank CHECK (btrim(display_name) <> ''),
    CONSTRAINT account_password_is_hashed CHECK (password_hash IS NULL OR password_hash LIKE 'scrypt$%'),
    CONSTRAINT account_signed_up_with_is_a_provider
        CHECK (signed_up_with IS NULL OR signed_up_with IN ('oidc', 'github', 'ldap')),
    CONSTRAINT account_without_a_password_came_from_a_provider
        CHECK (password_hash IS NOT NULL OR signed_up_with IS NOT NULL)
);

-- A person's name at a provider (OpenID Connect, GitHub, LDAP): the way they sign in with it. The
-- subject is what the provider says never changes for the person (the OpenID `sub`, GitHub's
-- numeric id, the directory's id attribute), so it is what identifies them, not their address,
-- which people change. A person has at most one per provider, and one provider identity belongs
-- to one account. Not graph content: the project role cannot reach it.
CREATE TABLE account_identities (
    provider     text NOT NULL,
    subject      text NOT NULL,
    account_id   bigint NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    -- What the provider told when this was linked: the address, and a name to show.
    email        text,
    display_name text,
    linked_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, subject),
    CONSTRAINT identity_is_one_per_provider_and_account UNIQUE (account_id, provider),
    CONSTRAINT identity_provider_is_known CHECK (provider IN ('oidc', 'github', 'ldap')),
    CONSTRAINT identity_subject_is_not_empty_or_huge CHECK (char_length(subject) BETWEEN 1 AND 255)
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

-- A link sent by email to confirm an address ('verify') or to choose a new password ('reset').
-- Only a hash of the token is kept, so a copy of the database cannot be used to take over an
-- account. A token works once (used_at) and until it expires, and sent_to is the address it was
-- sent to: it only counts while that is still the account's address.
CREATE TABLE email_tokens (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts (id),
    kind       text NOT NULL,
    token_hash text NOT NULL UNIQUE,
    sent_to    text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,
    CONSTRAINT email_token_kind_is_known CHECK (kind IN ('verify', 'reset')),
    CONSTRAINT email_token_expires_after_it_is_made CHECK (expires_at > created_at)
);

CREATE INDEX email_tokens_account_idx ON email_tokens (account_id, kind);

CREATE TABLE projects (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Exactly one owner, for good. Only the owner, the people the owner invited (project_members)
    -- and the agents the owner gave a key to may reach it.
    owner_id   bigint NOT NULL REFERENCES accounts (id),
    name       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT project_name_is_not_blank CHECK (btrim(name) <> '')
);

CREATE UNIQUE INDEX project_name_is_unique_for_its_owner ON projects (owner_id, lower(name));

-- What an agent sends to reach one project, as 'Authorization: Bearer <key>'. Only a hash is kept.
-- A key is an agent's identity: its name is what everything the agent does is recorded under, so
-- no two keys of a project that are in force may have the same name (upper or lower case).
-- allowed_tools is what the key may call: NULL means every tool, and a list means those tools only
-- (a tool added to the server later is not in it). The server keeps the list sorted and checks the
-- names; the database only refuses an empty list. code_mode is how the key calls Vrum: false, one
-- tool at a time; true, by running code (the key is then shown two tools, explore and run, and
-- what allowed_tools says it may call is what a script can call). verifier says the key belongs to
-- an agent that checks other agents' work: it may tick a check the definition of done marks
-- [verifier] or [github] (see definition_of_done.py), on work it did not do itself. expires_on is
-- the last day (UTC) the key works: from the next day on it is refused in plain words, and the
-- owner can change or clear it. NULL means it never expires.
CREATE TABLE agent_keys (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id    bigint NOT NULL REFERENCES projects (id),
    name          text NOT NULL,
    key_hash      text NOT NULL UNIQUE,
    -- The first characters of the key, so that a person can tell the keys apart.
    key_hint      text NOT NULL,
    allowed_tools text[],
    code_mode     boolean NOT NULL DEFAULT false,
    verifier      boolean NOT NULL DEFAULT false,
    expires_on    date,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz,
    CONSTRAINT agent_key_name_is_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT agent_key_tools_are_not_an_empty_list
        CHECK (allowed_tools IS NULL OR cardinality(allowed_tools) > 0)
);

CREATE INDEX agent_keys_project_idx ON agent_keys (project_id);
CREATE UNIQUE INDEX agent_key_name_is_unique_while_in_force
    ON agent_keys (project_id, lower(name)) WHERE revoked_at IS NULL;

-- Outgoing webhooks and email nudges (M48): a project's owner can have the server send a small
-- signed message to an address for chosen kinds of news (see notify_events.py), and a member can
-- ask to be mailed about some. They are settings of the project and of a person, not graph
-- content, so they are not in an export, and the project role cannot read them. A webhook's
-- secret has to be kept in full, because the server signs with it; it is shown once, never put
-- in the graph, an export or a log, and a backup holds it as it holds the keys' hashes, so a
-- backup is as private as the database.
CREATE TABLE webhooks (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id      bigint NOT NULL REFERENCES projects (id),
    url             text NOT NULL,
    events          text[] NOT NULL,
    secret          text NOT NULL,
    -- While a secret is being replaced the old one signs too, for a day, so that a receiver can
    -- change over without losing a message.
    previous_secret text,
    previous_until  timestamptz,
    enabled         boolean NOT NULL DEFAULT true,
    created_by      bigint NOT NULL REFERENCES accounts (id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- Only news after this position in the project's log is sent (see events.xid and events.id).
    after_xid       xid8 NOT NULL,
    after_id        bigint NOT NULL,
    last_success_at timestamptz,
    last_failure_at timestamptz,
    last_error      text,
    CONSTRAINT webhook_url_is_an_address CHECK (url ~ '^https?://[^/@[:space:]]+' AND length(url) <= 500),
    CONSTRAINT webhook_has_events CHECK (cardinality(events) > 0),
    CONSTRAINT webhook_secret_is_a_signing_secret CHECK (
        secret ~ '^whsec_[A-Za-z0-9+/]{43}=$'
        AND (previous_secret IS NULL OR previous_secret ~ '^whsec_[A-Za-z0-9+/]{43}=$')
    )
);

CREATE INDEX webhooks_project_idx ON webhooks (project_id);

-- A message to send, and how it went. The body is the exact JSON that is sent (no item text), the
-- message id stays the same through every retry, and a kind of news about one thing is sent once
-- to a webhook (`subject` is `event:<id>` for an event and `lapse:<node>:<epoch>` for a claim).
CREATE TABLE webhook_deliveries (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    webhook_id      bigint NOT NULL REFERENCES webhooks (id) ON DELETE CASCADE,
    project_id      bigint NOT NULL REFERENCES projects (id),
    news            text NOT NULL,
    subject         text NOT NULL,
    message_id      text NOT NULL,
    body            text NOT NULL,
    state           text NOT NULL DEFAULT 'pending',
    attempts        integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    delivered_at    timestamptz,
    last_status     integer,
    last_error      text,
    CONSTRAINT webhook_delivery_state_is_known CHECK (state IN ('pending', 'delivered', 'failed')),
    UNIQUE (webhook_id, news, subject)
);

CREATE INDEX webhook_deliveries_due_idx ON webhook_deliveries (next_attempt_at)
    WHERE state = 'pending';
CREATE INDEX webhook_deliveries_webhook_idx ON webhook_deliveries (webhook_id, id);

-- The kinds of news a person asked to be mailed about, in one project.
CREATE TABLE nudges (
    project_id bigint NOT NULL REFERENCES projects (id),
    account_id bigint NOT NULL REFERENCES accounts (id),
    events     text[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, account_id),
    CONSTRAINT nudge_has_events CHECK (cardinality(events) > 0)
);

-- Each email nudge sent, so that the limit of ten an hour for a person holds however many servers
-- share this database. Rows older than an hour are removed.
CREATE TABLE nudge_mails (
    account_id bigint NOT NULL REFERENCES accounts (id),
    sent_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX nudge_mails_idx ON nudge_mails (account_id, sent_at);

-- How far into each project's log the news has been looked at (for the webhooks and nudges that
-- project has). Only a project that has one has a row.
CREATE TABLE notify_cursor (
    project_id      bigint PRIMARY KEY REFERENCES projects (id),
    xid             xid8 NOT NULL,
    id              bigint NOT NULL,
    -- Claims that ran out after this have not been looked at yet (a lapse is a time passing, not
    -- an event).
    lapse_checked_at timestamptz NOT NULL DEFAULT now()
);

-- The people of a project besides its owner (projects.owner_id, who is always its one owner and
-- is never a row here). An editor reads and writes the project's graph; a viewer only reads it.
-- Only the owner makes agent keys, invites people and changes this table.
CREATE TABLE project_members (
    project_id bigint NOT NULL REFERENCES projects (id),
    account_id bigint NOT NULL REFERENCES accounts (id),
    role       text NOT NULL,
    added_at   timestamptz NOT NULL DEFAULT now(),
    -- Who invited this person.
    added_by   bigint NOT NULL REFERENCES accounts (id),
    PRIMARY KEY (project_id, account_id),
    CONSTRAINT member_role_is_known CHECK (role IN ('editor', 'viewer'))
);

CREATE INDEX project_members_account_idx ON project_members (account_id);

CREATE FUNCTION project_members_refuse_the_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM projects WHERE id = NEW.project_id AND owner_id = NEW.account_id) THEN
        RAISE EXCEPTION 'The owner of a project is not one of its members.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER project_members_refuse_the_owner
    BEFORE INSERT OR UPDATE OF project_id, account_id ON project_members
    FOR EACH ROW EXECUTE FUNCTION project_members_refuse_the_owner();

-- A link the owner made for one person to join a project with a role. Only a hash of the link's
-- token is kept, so a copy of the database cannot be used to join. It is spent by one person,
-- ends when it expires, and the owner can withdraw it before that. When the owner gave an email
-- address (sent_to), only an account with that address can accept it, and sent_at is when a mail
-- server last took the message that holds the link (NULL if none did).
CREATE TABLE project_invites (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id  bigint NOT NULL REFERENCES projects (id),
    role        text NOT NULL,
    token_hash  text NOT NULL UNIQUE,
    -- The first characters of the token, so that the owner can tell pending invitations apart.
    token_hint  text NOT NULL,
    created_by  bigint NOT NULL REFERENCES accounts (id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    accepted_by bigint REFERENCES accounts (id),
    accepted_at timestamptz,
    revoked_at  timestamptz,
    sent_to     text,
    sent_at     timestamptz,
    CONSTRAINT invite_role_is_known CHECK (role IN ('editor', 'viewer')),
    CONSTRAINT invite_expires_after_it_is_made CHECK (expires_at > created_at),
    CONSTRAINT invite_is_accepted_by_someone_when_accepted
        CHECK ((accepted_by IS NULL) = (accepted_at IS NULL)),
    CONSTRAINT invite_is_not_both_accepted_and_withdrawn
        CHECK (accepted_at IS NULL OR revoked_at IS NULL),
    CONSTRAINT invite_is_only_sent_to_an_address CHECK (sent_at IS NULL OR sent_to IS NOT NULL),
    CONSTRAINT invite_address_is_lower_case CHECK (sent_to = lower(btrim(sent_to)))
);

CREATE INDEX project_invites_project_idx ON project_invites (project_id);

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

-- Each project numbers its own epics, stories, tasks, links, rules, notes and events from 1, each
-- kind on its own, so a project's export loads into another project with the same numbers. The
-- numbers come from sequences made when the project is made. The function is the one thing that
-- may use them.
CREATE FUNCTION projects_make_sequences() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    kind text;
BEGIN
    FOREACH kind IN ARRAY ARRAY['epic', 'story', 'task', 'links', 'rules', 'notes', 'events'] LOOP
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
    -- How big a story or task is, effort and context together (xs 1 point, s 2, m 3, l 5, xl 8; see
    -- size_points). Empty until someone says. An epic has no size of its own: it adds up.
    size        text,
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
    CONSTRAINT node_size_is_known CHECK (size IN ('xs', 's', 'm', 'l', 'xl')),
    CONSTRAINT node_size_is_not_on_an_epic CHECK (size IS NULL OR kind <> 'epic'),
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
        CHECK (link_type IN
            ('needs_first', 'supersedes', 'duplicates', 'relates_to', 'discovered_from')),
    CONSTRAINT link_is_not_to_the_same_node CHECK (source <> target),
    CONSTRAINT link_is_not_repeated UNIQUE (project_id, source, target, link_type),
    -- Both ends are in the link's own project.
    CONSTRAINT link_starts_at_a_node FOREIGN KEY (project_id, source) REFERENCES nodes (project_id, ref),
    CONSTRAINT link_ends_at_a_node FOREIGN KEY (project_id, target) REFERENCES nodes (project_id, ref)
);

-- `discovered_from` says where a piece of work was found (the source was found while doing the
-- target); it never makes anything wait, so readiness ignores it. `duplicates` and `relates_to`
-- work both ways, so A to B and B to A are the same link.
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
-- until it is taken over or released. Only a story or a task can be claimed. The holder is an
-- identity: a kind (a person or an agent) and a name, and only that identity may renew or release
-- the claim. The last six columns are what the holder says about where the work is (a repository
-- address, a branch, a commit and a pull request address) and about the run (a label and a client
-- name): their own words, which Vrum never checks against a checkout or GitHub, so they are an
-- audit label and never an identity. The patterns are the ones of whereabouts.py.
CREATE TABLE claims (
    project_id   bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    node         text NOT NULL,
    actor        text NOT NULL,
    actor_kind   text NOT NULL,
    claimed_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at   timestamptz NOT NULL,
    repository   text,
    branch       text,
    commit       text,
    pull_request text,
    run_label    text,
    client_name  text,
    PRIMARY KEY (project_id, node),
    CONSTRAINT claim_is_for_a_story_or_task
        CHECK (starts_with(node, 'story:') OR starts_with(node, 'task:')),
    CONSTRAINT claim_actor_is_not_blank CHECK (btrim(actor) <> ''),
    CONSTRAINT claim_actor_kind_is_known CHECK (actor_kind IN ('person', 'agent')),
    CONSTRAINT claim_lease_is_in_the_future CHECK (expires_at > claimed_at),
    CONSTRAINT claim_repository_is_an_address CHECK (
        repository IS NULL OR (
            length(repository) <= 300
            AND repository ~ '^https://[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?(?:/[A-Za-z0-9._~%/-]*)?$'
            AND repository !~ '/\.\.?(?:/|$)'
        )
    ),
    CONSTRAINT claim_branch_is_a_name CHECK (
        branch IS NULL OR (length(branch) <= 200 AND branch ~ '^[A-Za-z0-9][A-Za-z0-9._/+=,#@-]*$')
    ),
    CONSTRAINT claim_commit_is_a_hash CHECK (commit IS NULL OR commit ~ '^[0-9a-f]{7,64}$'),
    CONSTRAINT claim_pull_request_is_an_address CHECK (
        pull_request IS NULL OR (
            pull_request ~
                '^https://[A-Za-z0-9][A-Za-z0-9.-]*/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*$'
            AND pull_request !~ '/\.\.?(?:/|$)'
        )
    ),
    CONSTRAINT claim_run_label_is_a_label CHECK (
        run_label IS NULL OR (
            length(run_label) <= 80 AND run_label ~ '^[A-Za-z0-9][A-Za-z0-9 ._:/+#@()=,-]*$'
        )
    ),
    CONSTRAINT claim_client_name_is_a_label CHECK (
        client_name IS NULL OR (
            length(client_name) <= 60 AND client_name ~ '^[A-Za-z0-9][A-Za-z0-9 ._:/+#@()=,-]*$'
        )
    ),
    CONSTRAINT claim_is_on_a_real_node
        FOREIGN KEY (project_id, node) REFERENCES nodes (project_id, ref)
);

-- Rules (shown as acceptance criteria) ------------------------------------------------------

-- A rule is an acceptance criterion: what must hold for a story or a task to be done. It belongs
-- to at most one story or task, and that is one column, never a join table. The table is here,
-- before node_state, because that view counts a node's rules.
CREATE TABLE rules (
    project_id bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    id         bigint NOT NULL DEFAULT next_id('rules'),
    -- The ref of the story or task the rule belongs to, such as 'story:12' or 'task:40'. Not an
    -- epic. Empty while the rule is not placed.
    node       text,
    title      text NOT NULL,
    body       text NOT NULL DEFAULT '',
    status     text NOT NULL DEFAULT 'open',
    version    integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    search     tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', title), 'A') || setweight(to_tsvector('english', body), 'B')
    ) STORED,
    PRIMARY KEY (project_id, id),
    CONSTRAINT rule_title_is_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT rule_status_is_known CHECK (status IN ('open', 'met', 'dropped')),
    CONSTRAINT rule_version_starts_at_one CHECK (version >= 1),
    CONSTRAINT rule_is_on_a_story_or_task
        CHECK (node IS NULL OR starts_with(node, 'story:') OR starts_with(node, 'task:')),
    CONSTRAINT rule_belongs_to_a_node
        FOREIGN KEY (project_id, node) REFERENCES nodes (project_id, ref)
);

CREATE INDEX rules_node_idx ON rules (project_id, node);
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

-- The definition of done and its checklist --------------------------------------------------

-- One text for the whole project: what every story or task must satisfy, on top of its own
-- acceptance criteria. Plain text or Markdown. A project that has never set one has no row, and
-- reads as an empty text at version 1; the first change makes the row, so a row's version is 2 or
-- more. Only a person changes it (the tool checks that, and so does the constraint on the kind),
-- and every change is an event with the text before and after.
CREATE TABLE definition_of_done (
    project_id      bigint PRIMARY KEY DEFAULT current_project() REFERENCES projects (id),
    body            text NOT NULL,
    version         integer NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    updated_by      text NOT NULL,
    updated_by_kind text NOT NULL,
    CONSTRAINT definition_version_starts_at_one CHECK (version >= 1),
    CONSTRAINT definition_is_set_by_a_named_person
        CHECK (btrim(updated_by) <> '' AND updated_by_kind = 'person')
);

-- The checklist the text holds: its list lines (`- ...`, `* ...` or `- [ ] ...`, at any depth), in
-- the order they are written. An item's key is the first 8 hex digits of a SHA-256 of its words
-- (trimmed, with a leading [ ] or [x] and any [person], [verifier] or [github] tags taken off), so
-- it stays the same while the words do, and two lines with the same words are one item. A line
-- tagged [person] is a check only a person can judge, and an agent may not tick it. One tagged
-- [verifier] or [github] (which means [verifier] too) may be ticked by an agent only if its key is a
-- verifier and it did not do the work on the story or task; a [github] tick by an agent starts its
-- evidence with a pull request address. Rebuilt in the same step as the text (see
-- definition_of_done.py), and by nothing else.
CREATE TABLE definition_items (
    project_id    bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    item_key      text NOT NULL,
    position      integer NOT NULL,
    text          text NOT NULL,
    person_only   boolean NOT NULL DEFAULT false,
    verifier_only boolean NOT NULL DEFAULT false,
    github        boolean NOT NULL DEFAULT false,
    PRIMARY KEY (project_id, item_key),
    CONSTRAINT definition_item_key_is_eight_hex_digits CHECK (item_key ~ '^[0-9a-f]{8}$'),
    CONSTRAINT definition_item_position_starts_at_one CHECK (position >= 1),
    CONSTRAINT definition_item_text_is_not_blank CHECK (btrim(text) <> ''),
    CONSTRAINT definition_item_github_is_for_a_verifier CHECK (NOT github OR verifier_only)
);

CREATE UNIQUE INDEX definition_item_position_is_unique ON definition_items (project_id, position);

-- An item ticked on a story or task, by a person or an agent. Only a tick has a row: unticking
-- deletes it (the history keeps both). A tick whose item is no longer in definition_items stays
-- here, and is neither counted nor shown as a current item. An agent has to say how it checked.
CREATE TABLE definition_checks (
    project_id      bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    node            text NOT NULL,
    item_key        text NOT NULL,
    -- The item's words when it was ticked, so a tick of a line that was later reworded or taken
    -- out still says what it was about.
    item_text       text NOT NULL,
    checked_by      text NOT NULL,
    checked_by_kind text NOT NULL,
    checked_at      timestamptz NOT NULL DEFAULT now(),
    -- How it was checked ("212 tests pass"). Optional for a person.
    evidence        text,
    PRIMARY KEY (project_id, node, item_key),
    CONSTRAINT check_is_on_a_story_or_task
        CHECK (starts_with(node, 'story:') OR starts_with(node, 'task:')),
    CONSTRAINT check_item_key_is_eight_hex_digits CHECK (item_key ~ '^[0-9a-f]{8}$'),
    CONSTRAINT check_item_text_is_not_blank CHECK (btrim(item_text) <> ''),
    CONSTRAINT check_is_ticked_by_a_named_identity
        CHECK (btrim(checked_by) <> '' AND checked_by_kind IN ('person', 'agent')),
    CONSTRAINT check_evidence_is_not_blank CHECK (evidence IS NULL OR btrim(evidence) <> ''),
    CONSTRAINT check_by_an_agent_says_how CHECK (checked_by_kind <> 'agent' OR evidence IS NOT NULL),
    CONSTRAINT check_is_on_a_real_node
        FOREIGN KEY (project_id, node) REFERENCES nodes (project_id, ref)
);

-- Size ------------------------------------------------------------------------------------------

-- What a size is worth, so a run of work can be added up: xs 1, s 2, m 3, l 5, xl 8. Nothing is
-- worth nothing: an item with no size has no points, and is counted as not sized.
CREATE FUNCTION size_points(size text) RETURNS integer LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE size WHEN 'xs' THEN 1 WHEN 's' THEN 2 WHEN 'm' THEN 3 WHEN 'l' THEN 5 WHEN 'xl' THEN 8 END
$$;

-- What a story adds up to. With tasks that are not dropped, the sum of theirs (a task with no size
-- adds nothing and is counted in `unsized` unless it is done). With none, the story's own size.
-- A dropped story with no tasks adds nothing. node_state shows it for a story and adds it up for
-- an epic.
CREATE VIEW story_points WITH (security_invoker = true) AS
SELECT s.project_id, s.ref, s.parent, s.status,
       CASE WHEN t.live > 0 THEN t.total
            WHEN s.status = 'dropped' THEN 0
            ELSE coalesce(size_points(s.size), 0) END AS points_total,
       CASE WHEN t.live > 0 THEN t.done
            WHEN s.status = 'done' THEN coalesce(size_points(s.size), 0)
            ELSE 0 END AS points_done,
       CASE WHEN t.live > 0 THEN t.unsized
            WHEN s.status NOT IN ('done', 'dropped') AND s.size IS NULL THEN 1
            ELSE 0 END AS unsized
FROM nodes s
CROSS JOIN LATERAL (
    SELECT count(*)::integer AS live,
           coalesce(sum(size_points(k.size)), 0)::integer AS total,
           coalesce(sum(size_points(k.size)) FILTER (WHERE k.status = 'done'), 0)::integer AS done,
           (count(*) FILTER (WHERE k.status <> 'done' AND k.size IS NULL))::integer AS unsized
    FROM nodes k
    WHERE k.project_id = s.project_id AND k.parent = s.ref AND k.status <> 'dropped'
) t
WHERE s.kind = 'story';

-- node_state, the view every read of a node goes through, is written after the event log and the
-- views derived from it (gates and plans hold a node, and they are events), further down.


-- Notes ---------------------------------------------------------------------------------------

-- A note is what is known or decided, with why, and a record: it is never edited. A change is a
-- newer note that supersedes the old one, or a withdrawal. A note somebody decided names who and
-- when (decided_by and decided_on, together); that is a detail of the note, not another kind. A
-- note has one status: unconfirmed or confirmed while it is in force (confirmed says that a named
-- person has looked at it, and nothing about whether it is right), then superseded or withdrawn
-- once it has ended. One that applies throughout its project is project_wide.
CREATE TABLE notes (
    project_id       bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    id               bigint NOT NULL DEFAULT next_id('notes'),
    -- The statement. It has no title: its first line is what lists show.
    text             text NOT NULL,
    -- Why it is so or why it was chosen: where it came from, who said it, what was observed, or the
    -- reason.
    why              text NOT NULL,
    -- The person who decided it, and the day. Both or neither. Whoever recorded the note is in
    -- recorded_by.
    decided_by       text,
    decided_on       date,
    project_wide     boolean NOT NULL DEFAULT false,
    -- Who recorded it: a name, and whether that was a person or an agent.
    recorded_by      text NOT NULL,
    recorded_by_kind text NOT NULL,
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    status           text NOT NULL DEFAULT 'unconfirmed',
    superseded_by_id bigint,
    -- Why it ended. Present when it was withdrawn, never while it is in force, and optional once
    -- it was superseded (what replaced it says why).
    status_reason    text,
    -- Who vouched for it and when. Both or neither; only a person's name is a confirmation. While
    -- the note is in force they are set exactly when its status is confirmed. Once it has ended
    -- they stay as they were, so the record still shows it had been confirmed.
    confirmed_by     text,
    confirmed_at     timestamptz,
    -- What a note says counts most, and why it is so and who decided it a little less.
    search           tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', text), 'A')
        || setweight(to_tsvector('english', why || ' ' || coalesce(decided_by, '')), 'B')
    ) STORED,
    PRIMARY KEY (project_id, id),
    CONSTRAINT note_status_is_known
        CHECK (status IN ('unconfirmed', 'confirmed', 'superseded', 'withdrawn')),
    CONSTRAINT note_text_is_not_blank CHECK (btrim(text) <> ''),
    CONSTRAINT note_why_is_not_blank CHECK (btrim(why) <> ''),
    CONSTRAINT note_decided_by_is_not_blank CHECK (decided_by IS NULL OR btrim(decided_by) <> ''),
    CONSTRAINT note_is_decided_by_someone_on_a_day
        CHECK ((decided_by IS NULL) = (decided_on IS NULL)),
    CONSTRAINT note_recorded_by_is_not_blank CHECK (btrim(recorded_by) <> ''),
    -- `unknown` is for a note loaded from an export made before identities were verified.
    CONSTRAINT note_recorded_by_kind_is_known
        CHECK (recorded_by_kind IN ('person', 'agent', 'unknown')),
    CONSTRAINT note_confirmation_names_a_person_and_a_time CHECK (
        (confirmed_by IS NULL) = (confirmed_at IS NULL)
        AND (confirmed_by IS NULL OR btrim(confirmed_by) <> '')
    ),
    CONSTRAINT note_is_confirmed_exactly_when_a_person_confirmed CHECK (
        status NOT IN ('unconfirmed', 'confirmed')
        OR (status = 'confirmed') = (confirmed_by IS NOT NULL)
    ),
    CONSTRAINT note_is_superseded_exactly_when_replaced
        CHECK ((status = 'superseded') = (superseded_by_id IS NOT NULL)),
    -- A replacement is always recorded after what it replaces, so replacements cannot form a circle.
    CONSTRAINT note_is_replaced_by_a_later_one
        CHECK (superseded_by_id IS NULL OR superseded_by_id > id),
    CONSTRAINT note_reason_matches_how_it_ended CHECK (
        (status NOT IN ('unconfirmed', 'confirmed') OR status_reason IS NULL)
        AND (status <> 'withdrawn' OR status_reason IS NOT NULL)
        AND (status_reason IS NULL OR btrim(status_reason) <> '')
    ),
    CONSTRAINT note_is_replaced_by_a_note
        FOREIGN KEY (project_id, superseded_by_id) REFERENCES notes (project_id, id)
);

CREATE INDEX notes_superseded_by_idx ON notes (project_id, superseded_by_id);
CREATE INDEX notes_search_idx ON notes USING gin (search);

-- A note that has ended cannot change at all, so what it was confirmed as stays as it was. While it
-- is in force only its confirmation can change (and with it, its status between unconfirmed and
-- confirmed), and ending it never changes the confirmation in the same step.
CREATE FUNCTION notes_are_a_record() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'A note is a record and cannot be deleted. Withdraw it or supersede it instead.';
    END IF;
    IF OLD.status IN ('superseded', 'withdrawn') THEN
        RAISE EXCEPTION 'A superseded or withdrawn note cannot change again.';
    END IF;
    IF (NEW.project_id, NEW.text, NEW.why, NEW.decided_by, NEW.decided_on, NEW.project_wide,
        NEW.recorded_by, NEW.recorded_by_kind, NEW.recorded_at)
       IS DISTINCT FROM
       (OLD.project_id, OLD.text, OLD.why, OLD.decided_by, OLD.decided_on, OLD.project_wide,
        OLD.recorded_by, OLD.recorded_by_kind, OLD.recorded_at)
    THEN
        RAISE EXCEPTION 'A note cannot be edited. Supersede it with a newer one, or withdraw it.';
    END IF;
    IF NEW.status IN ('superseded', 'withdrawn')
       AND (NEW.confirmed_by, NEW.confirmed_at) IS DISTINCT FROM (OLD.confirmed_by, OLD.confirmed_at)
    THEN
        RAISE EXCEPTION 'A note cannot be confirmed or unconfirmed in the same step that ends it.';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER notes_are_a_record BEFORE UPDATE OR DELETE ON notes
    FOR EACH ROW EXECUTE FUNCTION notes_are_a_record();

-- What a note concerns: zero or more nodes, rules and other notes.
CREATE TABLE note_nodes (
    project_id bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    note_id    bigint NOT NULL,
    node       text NOT NULL,
    PRIMARY KEY (project_id, note_id, node),
    FOREIGN KEY (project_id, note_id) REFERENCES notes (project_id, id),
    FOREIGN KEY (project_id, node) REFERENCES nodes (project_id, ref)
);

CREATE TABLE note_rules (
    project_id bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    note_id    bigint NOT NULL,
    rule_id    bigint NOT NULL,
    PRIMARY KEY (project_id, note_id, rule_id),
    FOREIGN KEY (project_id, note_id) REFERENCES notes (project_id, id),
    FOREIGN KEY (project_id, rule_id) REFERENCES rules (project_id, id)
);

CREATE TABLE note_notes (
    project_id bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    note_id    bigint NOT NULL,
    about_id   bigint NOT NULL,
    PRIMARY KEY (project_id, note_id, about_id),
    FOREIGN KEY (project_id, note_id) REFERENCES notes (project_id, id),
    FOREIGN KEY (project_id, about_id) REFERENCES notes (project_id, id),
    CONSTRAINT note_is_not_about_itself CHECK (note_id <> about_id)
);

CREATE INDEX note_nodes_node_idx ON note_nodes (project_id, node);
CREATE INDEX note_rules_rule_idx ON note_rules (project_id, rule_id);
CREATE INDEX note_notes_about_idx ON note_notes (project_id, about_id);

-- A superseded or withdrawn note keeps the links it had, as a record of what it once concerned.
CREATE FUNCTION note_links_are_frozen_once_ended() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    the_project bigint;
    the_note bigint;
    the_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        the_project := OLD.project_id;
        the_note := OLD.note_id;
    ELSE
        the_project := NEW.project_id;
        the_note := NEW.note_id;
    END IF;
    SELECT status INTO the_status FROM notes
    WHERE project_id = the_project AND id = the_note;
    IF the_status IN ('superseded', 'withdrawn') THEN
        RAISE EXCEPTION 'A superseded or withdrawn note keeps the links it had. Link or unlink the note that replaced it instead.';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER note_nodes_are_frozen_once_ended BEFORE INSERT OR DELETE ON note_nodes
    FOR EACH ROW EXECUTE FUNCTION note_links_are_frozen_once_ended();
CREATE TRIGGER note_rules_are_frozen_once_ended BEFORE INSERT OR DELETE ON note_rules
    FOR EACH ROW EXECUTE FUNCTION note_links_are_frozen_once_ended();
CREATE TRIGGER note_notes_are_frozen_once_ended BEFORE INSERT OR DELETE ON note_notes
    FOR EACH ROW EXECUTE FUNCTION note_links_are_frozen_once_ended();

-- A note as it is read: whether somebody decided it, what it replaced and what it concerns.
CREATE VIEW note_state WITH (security_invoker = true) AS
SELECT n.project_id, n.id, n.text, n.why, n.decided_by, n.decided_on,
       (n.decided_by IS NOT NULL) AS decided, n.project_wide,
       n.recorded_by, n.recorded_by_kind, n.recorded_at,
       n.status, n.superseded_by_id, n.status_reason, n.confirmed_by, n.confirmed_at,
       coalesce((SELECT array_agg(s.id ORDER BY s.id) FROM notes s
                 WHERE s.project_id = n.project_id AND s.superseded_by_id = n.id),
                ARRAY[]::bigint[]) AS supersedes_ids,
       coalesce((SELECT array_agg(l.node ORDER BY k.kind_rank, k.number) FROM note_nodes l
                 JOIN nodes k ON k.project_id = l.project_id AND k.ref = l.node
                 WHERE l.project_id = n.project_id AND l.note_id = n.id),
                ARRAY[]::text[]) AS nodes,
       coalesce((SELECT array_agg(l.rule_id ORDER BY l.rule_id) FROM note_rules l
                 WHERE l.project_id = n.project_id AND l.note_id = n.id),
                ARRAY[]::bigint[]) AS rule_ids,
       coalesce((SELECT array_agg(l.about_id ORDER BY l.about_id) FROM note_notes l
                 WHERE l.project_id = n.project_id AND l.note_id = n.id),
                ARRAY[]::bigint[]) AS note_ids
FROM notes n;

-- Exact-words search and the semantic index ---------------------------------------------------

-- Every thing a person can search for: its type, its id and its ref, and its search column. The type
-- is the one a person sees: epic, story, task, rule (shown as an acceptance criterion) or note. For
-- an epic, story or task the id is its number, which is unique within its type, so (item_type, id)
-- names one thing.
CREATE VIEW search_documents WITH (security_invoker = true) AS
SELECT n.project_id, n.kind AS item_type, n.number AS id, n.ref, n.search FROM nodes n
UNION ALL
SELECT r.project_id, 'rule', r.id, 'rule:' || r.id::text, r.search FROM rules r
UNION ALL
SELECT n.project_id, 'note', n.id, 'note:' || n.id::text, n.search FROM notes n;

-- What the semantic index embeds, and a hash of it, so that an item whose text changed is known
-- to be waiting to be indexed. Epics, stories, tasks, rules and notes are embedded. The index tables
-- themselves are not in this file: they are derived, and the reindex job builds them (see
-- vrum/indexing.py).
CREATE VIEW index_source WITH (security_invoker = true) AS
SELECT s.project_id, s.item_type, s.id, s.ref, s.content, md5(s.content) AS content_hash
FROM (
    SELECT n.project_id, n.kind AS item_type, n.number AS id, n.ref,
           btrim(n.title || E'\n' || n.body) AS content
    FROM nodes n
    UNION ALL
    SELECT r.project_id, 'rule', r.id, 'rule:' || r.id::text, btrim(r.title || E'\n' || r.body)
    FROM rules r
    UNION ALL
    SELECT n.project_id, 'note', n.id, 'note:' || n.id::text, btrim(n.text || E'\n' || n.why)
    FROM notes n
) s;

-- The event log ----------------------------------------------------------------------------

-- An event shows in the history of every node and rule it names. For a link, node is where it
-- starts and other_node where it ends. For a rule, node is the story or task the rule is on after
-- the event and other_node the one it was on before, when that was a different one. A note event
-- names the note, and also the node or rule when the event is about a link to it, and the other
-- note (other_note_id) when it is about a link to a note, so it shows in the history of each of
-- them. A change of the definition of done names nothing: it is about the whole project. Ticking or
-- unticking one of its checks names the story or task it was done on.
CREATE TABLE events (
    project_id    bigint NOT NULL DEFAULT current_project() REFERENCES projects (id),
    id            bigint NOT NULL DEFAULT next_id('events'),
    node          text,
    other_node    text,
    rule_id       bigint,
    note_id       bigint,
    other_note_id bigint,
    action        text NOT NULL,
    -- Who did it: a name, and whether that was a person or an agent (`unknown` only for events
    -- loaded from an export made before identities were verified).
    actor         text NOT NULL,
    actor_kind    text NOT NULL,
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
    FOREIGN KEY (project_id, note_id) REFERENCES notes (project_id, id),
    FOREIGN KEY (project_id, other_note_id) REFERENCES notes (project_id, id),
    CONSTRAINT event_action_is_known CHECK (action IN (
        'create', 'update', 'move',
        'link_added', 'link_removed',
        'claimed', 'claim_renewed', 'claim_released', 'handoff',
        'rule_created', 'rule_updated', 'rule_placed', 'rule_moved',
        'definition_of_done_changed', 'check_ticked', 'check_unticked',
        'note_recorded', 'note_superseded', 'note_withdrawn', 'note_linked', 'note_unlinked',
        'note_confirmed', 'note_unconfirmed',
        'ask_raised', 'ask_answered',
        'gate_added', 'gate_removed',
        'plan_proposed', 'plan_revised', 'plan_changes_requested', 'plan_approved',
        'plan_rejected', 'plan_template_saved', 'plan_template_deleted',
        'placement_proposed', 'placement_accepted', 'placement_rejected'
    )),
    CONSTRAINT event_actor_is_not_blank CHECK (btrim(actor) <> ''),
    CONSTRAINT event_actor_kind_is_known CHECK (actor_kind IN ('person', 'agent', 'unknown')),
    -- A handoff says how the holder left the work and what the next person must know. A blank
    -- note is refused here too, whatever the tools check.
    CONSTRAINT handoff_has_an_outcome_and_a_note CHECK (
        action <> 'handoff' OR (
            coalesce(after->>'outcome', '') IN ('done', 'blocked', 'dropped', 'released')
            AND btrim(coalesce(after->>'note', '')) <> ''
        )
    ),
    -- An ask (M45) is a question an agent put to a person about a story or task, and its answer is
    -- another event; neither is ever edited (the log is append-only), and see ask_state. An ask
    -- says what is asked, whether it is a question or an approval, and whether the work was
    -- blocked until it is answered, released and left free (parked), or is the approval a gate
    -- (M47) waits for (gates). An answer names the ask
    -- (the id of the event that raised it) and is a person's: an agent never answers.
    CONSTRAINT ask_raised_has_its_words CHECK (
        action <> 'ask_raised' OR (
            btrim(coalesce(after->>'question', '')) <> ''
            AND coalesce(after->>'kind', '') IN ('question', 'approval')
            AND coalesce(after->>'effect', '') IN ('blocks', 'parks', 'gates')
        )
    ),
    CONSTRAINT ask_answered_has_its_words CHECK (
        action <> 'ask_answered' OR (
            coalesce(after->>'ask', '') ~ '^[0-9]+$'
            AND btrim(coalesce(after->>'answer', '')) <> ''
            AND actor_kind = 'person'
        )
    ),
    -- A gate (M47) holds a story or task until something outside the graph happens: a time has
    -- come (timer) or a person says yes to an approval (an ask of effect gates). It is an event,
    -- and so is lifting it. A plan (M47) is proposed as one event naming the items it made, and a
    -- person approves or rejects it as another. A plan template is saved, and deleted, as events.
    CONSTRAINT gate_added_has_its_words CHECK (
        action <> 'gate_added' OR (
            CASE after->>'kind'
                WHEN 'timer' THEN coalesce(after->>'not_before', '')
                    ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
                WHEN 'approval' THEN coalesce(after->>'ask', '') ~ '^[0-9]+$'
                ELSE false
            END
        )
    ),
    CONSTRAINT gate_removed_names_its_gate CHECK (
        action <> 'gate_removed' OR coalesce(after->>'gate', '') ~ '^[0-9]+$'
    ),
    -- A plan is reviewed as revisions (M51). The proposal is its first; a revision is another event
    -- by whoever revised it; a person's request for changes is another, about one revision. Each
    -- proposal and revision keeps what it held whole (`snapshot`, one entry for each item with its
    -- details, links and criteria), so it can be read as it was; the items it names are the plan's
    -- items from then on (`items`), and `removed` the ones the revision took out.
    CONSTRAINT plan_proposed_has_its_words CHECK (
        action <> 'plan_proposed' OR (
            btrim(coalesce(after->>'title', '')) <> ''
            AND coalesce(jsonb_typeof(after->'items'), '') = 'array'
            AND coalesce(jsonb_typeof(after->'snapshot'), '') = 'array'
        )
    ),
    CONSTRAINT plan_revised_has_its_words CHECK (
        action <> 'plan_revised' OR (
            coalesce(after->>'plan', '') ~ '^[0-9]+$'
            AND CASE WHEN coalesce(after->>'revision', '') ~ '^[0-9]+$'
                     THEN (after->>'revision')::integer >= 2 ELSE false END
            AND coalesce(jsonb_typeof(after->'items'), '') = 'array'
            AND coalesce(jsonb_typeof(after->'snapshot'), '') = 'array'
        )
    ),
    CONSTRAINT plan_changes_requested_has_its_words CHECK (
        action <> 'plan_changes_requested' OR (
            coalesce(after->>'plan', '') ~ '^[0-9]+$'
            AND coalesce(after->>'revision', '') ~ '^[0-9]+$'
            AND btrim(coalesce(after->>'summary', '')) <> ''
            AND coalesce(jsonb_typeof(after->'comments'), '') = 'array'
            AND actor_kind = 'person'
        )
    ),
    CONSTRAINT plan_decided_names_its_plan CHECK (
        action NOT IN ('plan_approved', 'plan_rejected')
        OR (coalesce(after->>'plan', '') ~ '^[0-9]+$' AND coalesce(after->>'revision', '') ~ '^[0-9]+$'
            AND actor_kind = 'person')
    ),
    CONSTRAINT plan_template_has_its_words CHECK (
        action NOT IN ('plan_template_saved', 'plan_template_deleted')
        OR coalesce(after->>'name', '') ~ '^[a-z0-9][a-z0-9_-]{0,59}$'
    ),
    CONSTRAINT plan_template_saved_has_a_template CHECK (
        action <> 'plan_template_saved' OR jsonb_typeof(after->'template') = 'object'
    ),
    -- A placement (M43) is a proposal to move stories or tasks under another parent, or to group
    -- stories under a new epic, made by whoever noticed work in the wrong place (a steward agent
    -- or a person) and decided by a person as another event. It names the items with where each
    -- is now (so a move that has become stale is seen), the new parent or the new epic, and why.
    CONSTRAINT placement_proposed_has_its_words CHECK (
        action <> 'placement_proposed' OR (
            jsonb_typeof(after->'items') = 'array'
            AND jsonb_array_length(after->'items') BETWEEN 1 AND 20
            AND btrim(coalesce(after->>'reason', '')) <> ''
            AND ((after->>'parent') IS NOT NULL)
                <> coalesce(jsonb_typeof(after->'new_epic') = 'object', false)
        )
    ),
    CONSTRAINT placement_decided_names_its_placement CHECK (
        action NOT IN ('placement_accepted', 'placement_rejected')
        OR (coalesce(after->>'placement', '') ~ '^[0-9]+$' AND actor_kind = 'person')
    ),
    CONSTRAINT event_names_what_it_is_about CHECK (
        CASE
            WHEN starts_with(action, 'rule_')
                THEN rule_id IS NOT NULL AND note_id IS NULL AND other_note_id IS NULL
            WHEN starts_with(action, 'note_') THEN note_id IS NOT NULL
            WHEN action IN ('definition_of_done_changed', 'plan_proposed', 'plan_revised',
                            'plan_changes_requested', 'plan_approved',
                            'plan_rejected', 'plan_template_saved', 'plan_template_deleted',
                            'placement_proposed', 'placement_accepted', 'placement_rejected')
                THEN node IS NULL AND other_node IS NULL AND rule_id IS NULL AND note_id IS NULL
                    AND other_note_id IS NULL
            ELSE node IS NOT NULL AND rule_id IS NULL AND note_id IS NULL
                AND other_note_id IS NULL
        END
    )
);

CREATE INDEX events_node_idx ON events (project_id, node, id);
CREATE INDEX events_other_node_idx ON events (project_id, other_node, id)
    WHERE other_node IS NOT NULL;
CREATE INDEX events_rule_idx ON events (project_id, rule_id, id) WHERE rule_id IS NOT NULL;
CREATE INDEX events_note_idx ON events (project_id, note_id, id) WHERE note_id IS NOT NULL;
CREATE INDEX events_other_note_idx ON events (project_id, other_note_id, id)
    WHERE other_note_id IS NOT NULL;
CREATE INDEX events_feed_idx ON events (project_id, xid, id);
-- An ask is answered once, and the answer is final.
CREATE UNIQUE INDEX ask_is_answered_once ON events (project_id, ((after->>'ask')::bigint))
    WHERE action = 'ask_answered';
-- A gate is lifted once, and a plan is decided once.
CREATE UNIQUE INDEX gate_is_removed_once ON events (project_id, ((after->>'gate')::bigint))
    WHERE action = 'gate_removed';
CREATE UNIQUE INDEX plan_is_decided_once ON events (project_id, ((after->>'plan')::bigint))
    WHERE action IN ('plan_approved', 'plan_rejected');
CREATE INDEX events_gates_idx ON events (project_id, node, id) WHERE action = 'gate_added';
CREATE INDEX events_plans_idx ON events (project_id, id) WHERE action = 'plan_proposed';
-- Each revision of a plan is made once, and each revision is asked for changes at most once.
CREATE UNIQUE INDEX plan_revision_is_made_once
    ON events (project_id, ((after->>'plan')::bigint), ((after->>'revision')::integer))
    WHERE action = 'plan_revised';
CREATE UNIQUE INDEX plan_revision_is_asked_once
    ON events (project_id, ((after->>'plan')::bigint), ((after->>'revision')::integer))
    WHERE action = 'plan_changes_requested';
-- A placement is decided once.
CREATE UNIQUE INDEX placement_is_decided_once ON events (project_id, ((after->>'placement')::bigint))
    WHERE action IN ('placement_accepted', 'placement_rejected');
CREATE INDEX events_placements_idx ON events (project_id, id) WHERE action = 'placement_proposed';

CREATE FUNCTION events_refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'The event log is append-only: events cannot be changed or deleted.';
END
$$;

CREATE TRIGGER events_are_append_only BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION events_refuse_change();

-- Every ask with its answer, when it has one: the ask is the event that raised it (its number is
-- that event's id) and the answer the event that names it. Derived from the log, never stored
-- twice, so an ask and its answer cannot be edited or lost.
CREATE VIEW ask_state WITH (security_invoker = true) AS
SELECT r.project_id, r.id, r.node, r.actor AS asked_by, r.actor_kind AS asked_by_kind,
       r.at AS asked_at, r.after->>'question' AS question, r.after->>'kind' AS kind,
       r.after->>'effect' AS effect,
       CASE WHEN jsonb_typeof(r.after->'choices') = 'array'
            THEN ARRAY(SELECT jsonb_array_elements_text(r.after->'choices')) END AS choices,
       a.id AS answer_event_id, a.actor AS answered_by, a.at AS answered_at,
       a.after->>'answer' AS answer, a.after->>'comment' AS comment
FROM events r
LEFT JOIN events a ON a.project_id = r.project_id AND a.action = 'ask_answered'
    AND (a.after->>'ask')::bigint = r.id
WHERE r.action = 'ask_raised';

-- Every gate with where it stands: removed (someone lifted it), open (its time has come, or its
-- approval was answered yes), refused (its approval was answered anything else: the work stays
-- held until a person lifts the gate or drops the work) or waiting. Derived from the log.
CREATE VIEW gate_state WITH (security_invoker = true) AS
SELECT g.project_id, g.id, g.node, g.actor AS added_by, g.actor_kind AS added_by_kind,
       g.at AS added_at, g.after->>'kind' AS kind,
       (g.after->>'not_before')::timestamptz AS not_before,
       (g.after->>'ask')::bigint AS ask,
       rm.id AS removed_event_id, rm.actor AS removed_by, rm.at AS removed_at,
       rm.after->>'reason' AS removed_reason,
       a.question AS question, a.answer AS answer, a.answered_by AS answered_by,
       CASE WHEN rm.id IS NOT NULL THEN 'removed'
            WHEN g.after->>'kind' = 'timer' THEN
                CASE WHEN (g.after->>'not_before')::timestamptz <= clock_timestamp()
                     THEN 'open' ELSE 'waiting' END
            WHEN lower(a.answer) = 'yes' THEN 'open'
            WHEN a.answer IS NOT NULL THEN 'refused'
            ELSE 'waiting' END AS state
FROM events g
LEFT JOIN events rm ON rm.project_id = g.project_id AND rm.action = 'gate_removed'
    AND (rm.after->>'gate')::bigint = g.id
LEFT JOIN ask_state a ON a.project_id = g.project_id AND g.after->>'kind' = 'approval'
    AND a.id = (g.after->>'ask')::bigint
WHERE g.action = 'gate_added';

-- Every plan with where it stands: proposed (its current revision waits for a person), changes_requested
-- (a person asked for changes to the current revision and the agent has not revised it yet),
-- approved or rejected. `revision` is the current one (1 is the proposal), `items` are the
-- current revision's items, and `revised_by` and `revised_at` say who made it and when.
CREATE VIEW plan_state WITH (security_invoker = true) AS
SELECT p.project_id, p.id, p.actor AS proposed_by, p.actor_kind AS proposed_by_kind,
       p.at AS proposed_at, p.after->>'title' AS title,
       coalesce(r.after->'items', p.after->'items') AS items,
       coalesce((r.after->>'revision')::integer, 1) AS revision,
       coalesce(r.actor, p.actor) AS revised_by, coalesce(r.actor_kind, p.actor_kind) AS revised_by_kind,
       coalesce(r.at, p.at) AS revised_at,
       d.id AS decision_event_id, d.actor AS decided_by, d.at AS decided_at,
       d.after->>'reason' AS reason,
       CASE WHEN d.action = 'plan_approved' THEN 'approved'
            WHEN d.action = 'plan_rejected' THEN 'rejected'
            WHEN c.id IS NOT NULL THEN 'changes_requested'
            ELSE 'proposed' END AS state
FROM events p
LEFT JOIN LATERAL (
    SELECT e.* FROM events e
    WHERE e.project_id = p.project_id AND e.action = 'plan_revised'
      AND (e.after->>'plan')::bigint = p.id
    ORDER BY (e.after->>'revision')::integer DESC LIMIT 1
) r ON true
LEFT JOIN events d ON d.project_id = p.project_id
    AND d.action IN ('plan_approved', 'plan_rejected') AND (d.after->>'plan')::bigint = p.id
LEFT JOIN events c ON c.project_id = p.project_id AND c.action = 'plan_changes_requested'
    AND (c.after->>'plan')::bigint = p.id
    AND (c.after->>'revision')::integer = coalesce((r.after->>'revision')::integer, 1)
WHERE p.action = 'plan_proposed';

-- Every placement (M43) with where it stands: proposed (a person has not decided), accepted or
-- rejected. `applied`, `skipped` and `epic` come from the decision: what was moved, what had
-- already moved or finished and so was left, and the epic that was made for a group.
CREATE VIEW placement_state WITH (security_invoker = true) AS
SELECT p.project_id, p.id, p.actor AS proposed_by, p.actor_kind AS proposed_by_kind,
       p.at AS proposed_at, p.after->'items' AS items, p.after->>'parent' AS parent,
       p.after->'new_epic' AS new_epic, p.after->>'reason' AS reason,
       d.id AS decision_event_id, d.actor AS decided_by, d.at AS decided_at,
       d.after->>'reason' AS decision_reason, d.after->'applied' AS applied,
       d.after->'skipped' AS skipped, d.after->>'epic' AS epic,
       CASE d.action WHEN 'placement_accepted' THEN 'accepted'
                     WHEN 'placement_rejected' THEN 'rejected' ELSE 'proposed' END AS state
FROM events p
LEFT JOIN events d ON d.project_id = p.project_id
    AND d.action IN ('placement_accepted', 'placement_rejected')
    AND (d.after->>'placement')::bigint = p.id
WHERE p.action = 'placement_proposed';

-- The newest event about each plan template: last_action says whether it is saved or was deleted
-- (a deleted one is not listed). Templates are data: a saved one is never edited, a newer save
-- replaces it, and every version stays in the log.
CREATE VIEW plan_template_state WITH (security_invoker = true) AS
SELECT DISTINCT ON (e.project_id, e.after->>'name')
       e.project_id, e.after->>'name' AS name, e.action AS last_action, e.id AS version_event_id,
       e.actor AS saved_by, e.actor_kind AS saved_by_kind, e.at AS saved_at,
       e.after->'template' AS template
FROM events e
WHERE e.action IN ('plan_template_saved', 'plan_template_deleted')
ORDER BY e.project_id, e.after->>'name', e.id DESC;

-- A node as it is read: its status with `ready` worked out, what it still waits for, who holds a
-- claim on it right now, and what its dates add up to. This is the one place that decides what
-- `ready`, overdue, a span and a schedule conflict mean. Every join stays inside the node's own
-- project, and the view is read with the rights of whoever reads it, so a project's role sees only
-- its own rows.
--   waiting_on:         the refs of the nodes it needs first that are not done (epics, then stories,
--                       then tasks, each by number).
--   held_by:            what holds it back that is not another node: `gate:5` (a gate that is waiting,
--                       or whose approval was refused) and `plan:2` (a plan a person has not yet
--                       approved, or has asked changes to: its current revision's items). A held node is never ready and cannot be claimed.
--   schedule_conflicts: those of them that are due after this node's own start date. A warning only.
--   overdue:            its own target date has passed and it is not done or dropped.
--   span_start/end:     the dates the roadmap draws. Its own when it has any. Otherwise, for an epic
--                       or story, the span of what is under it (dropped work does not count), and
--                       span_worked_out says so.
--   claimed_by(_kind):  who holds it, as a name and the kind of that holder (person or agent).
--   lapsed_claim_*:     a claim that ran out and was neither renewed, released nor taken over. It
--                       stays in its row until then, so it shows as work someone left with no
--                       handoff note.
--   body_chars:         how long the body is, so a list can leave the body out and still say how
--                       much there is.
--   progress_done/total: for an epic or a story, its tasks that are done out of its tasks that are
--                       not dropped (an epic's are the tasks of its stories that are not dropped).
--                       Empty for a task.
--   criteria_total/met: the acceptance criteria (rules) placed directly on this story or task that
--                       are not dropped, and how many of them are met. Always 0 for an epic, which
--                       has none.
--   size, points:       its own size, and what that is worth (see size_points). Empty when it has
--                       none, and always empty for an epic.
--   points_total/done:  what it adds up to, and how much of that is done. A task: its own points
--                       (0 when it is dropped or has no size). A story: the sum over its tasks that
--                       are not dropped, or its own points when it has none. An epic: the sum over
--                       its stories that are not dropped (see story_points).
--   unsized:            how many items are not sized: the item itself, or what it adds up, that
--                       are neither done nor dropped.
--   checks_total/ticked: the items of the definition of done's checklist, and how many of them are
--                       ticked on this story or task. A tick of an item that is no longer on the
--                       list is not counted. Always 0 for an epic.
--   verifier_checks_open: how many checks that only a verifier (or a person) may tick are not yet
--                       ticked on this story or task; always 0 for an epic, and for work that is
--                       done or dropped. `is:unverified` uses it.
CREATE VIEW node_state WITH (security_invoker = true) AS
SELECT n.project_id, n.ref, n.kind, n.number, n.kind_rank, n.parent, n.title, n.body,
       length(n.body) AS body_chars,
       CASE WHEN n.status = 'new' AND cardinality(w.refs) = 0 AND cardinality(h.reasons) = 0
            THEN 'ready' ELSE n.status END AS status,
       n.priority, n.size, size_points(n.size) AS points,
       n.start_on, n.target_on, n.version, n.created_at, n.updated_at,
       w.refs AS waiting_on,
       h.reasons AS held_by,
       w.conflicts AS schedule_conflicts,
       coalesce(n.target_on < current_date AND n.status NOT IN ('done', 'dropped'), false)
           AS overdue,
       sp.span_start,
       sp.span_end,
       (n.start_on IS NULL AND n.target_on IS NULL
        AND (sp.span_start IS NOT NULL OR sp.span_end IS NOT NULL)) AS span_worked_out,
       c.actor AS claimed_by,
       c.actor_kind AS claimed_by_kind,
       c.expires_at AS claim_expires_at,
       lc.actor AS lapsed_claim_by,
       lc.actor_kind AS lapsed_claim_by_kind,
       lc.expires_at AS lapsed_claim_at,
       CASE WHEN n.kind <> 'task' THEN pr.done END AS progress_done,
       CASE WHEN n.kind <> 'task' THEN pr.total END AS progress_total,
       cr.total AS criteria_total,
       cr.met AS criteria_met,
       pt.total AS points_total,
       pt.done AS points_done,
       pt.unsized AS unsized,
       ck.total AS checks_total,
       ck.ticked AS checks_ticked,
       ck.verifier_open AS verifier_checks_open
FROM nodes n
CROSS JOIN LATERAL (
    SELECT count(*)::integer AS total, (count(*) FILTER (WHERE r.status = 'met'))::integer AS met
    FROM rules r
    WHERE r.project_id = n.project_id AND r.node = n.ref AND r.status <> 'dropped'
) cr
CROSS JOIN LATERAL (
    SELECT coalesce(sum(x.total), 0)::integer AS total,
           coalesce(sum(x.done), 0)::integer AS done,
           coalesce(sum(x.unsized), 0)::integer AS unsized
    FROM (
        SELECT CASE WHEN n.status = 'dropped' THEN 0 ELSE coalesce(size_points(n.size), 0) END
                   AS total,
               CASE WHEN n.status = 'done' THEN coalesce(size_points(n.size), 0) ELSE 0 END
                   AS done,
               CASE WHEN n.status NOT IN ('done', 'dropped') AND n.size IS NULL THEN 1 ELSE 0 END
                   AS unsized
        WHERE n.kind = 'task'
        UNION ALL
        SELECT sp.points_total, sp.points_done, sp.unsized
        FROM story_points sp
        WHERE n.kind = 'story' AND sp.project_id = n.project_id AND sp.ref = n.ref
        UNION ALL
        SELECT sp.points_total, sp.points_done, sp.unsized
        FROM story_points sp
        WHERE n.kind = 'epic' AND sp.project_id = n.project_id AND sp.parent = n.ref
          AND sp.status <> 'dropped'
    ) x
) pt
CROSS JOIN LATERAL (
    SELECT CASE WHEN n.kind = 'epic' THEN 0 ELSE
               (SELECT count(*) FROM definition_items i WHERE i.project_id = n.project_id)::integer
           END AS total,
           CASE WHEN n.kind = 'epic' THEN 0 ELSE
               (SELECT count(*) FROM definition_checks d
                JOIN definition_items i ON i.project_id = d.project_id AND i.item_key = d.item_key
                WHERE d.project_id = n.project_id AND d.node = n.ref)::integer
           END AS ticked,
           CASE WHEN n.kind = 'epic' OR n.status IN ('done', 'dropped') THEN 0 ELSE
               (SELECT count(*) FROM definition_items i
                WHERE i.project_id = n.project_id AND i.verifier_only AND NOT i.person_only
                  AND NOT EXISTS (SELECT 1 FROM definition_checks d
                                  WHERE d.project_id = n.project_id AND d.node = n.ref
                                    AND d.item_key = i.item_key))::integer
           END AS verifier_open
) ck
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
    SELECT coalesce(array_agg(r.reason ORDER BY r.reason), ARRAY[]::text[]) AS reasons
    FROM (
        SELECT 'gate:' || g.id::text AS reason FROM gate_state g
        WHERE g.node = n.ref AND g.state IN ('waiting', 'refused')
        UNION ALL
        SELECT 'plan:' || p.id::text FROM plan_state p
        WHERE p.state IN ('proposed', 'changes_requested') AND p.items ? n.ref
    ) r
) h
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
        'nodes', 'links', 'claims', 'rules', 'notes', 'note_nodes', 'note_rules', 'note_notes',
        'events', 'definition_of_done', 'definition_items', 'definition_checks'
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
GRANT SELECT, INSERT, UPDATE ON nodes, rules, notes, definition_of_done TO vrum_project;
GRANT SELECT, INSERT, DELETE ON links, note_nodes, note_rules, note_notes, definition_items
    TO vrum_project;
GRANT SELECT, INSERT, UPDATE, DELETE ON claims, definition_checks TO vrum_project;
GRANT SELECT, INSERT ON events TO vrum_project;
GRANT SELECT ON node_state, story_points, note_state, ask_state, gate_state, plan_state,
    plan_template_state, placement_state, search_documents, index_source
    TO vrum_project;
