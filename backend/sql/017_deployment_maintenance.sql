-- Owner-managed admission gate. Runtime roles may only read this control row.
CREATE TABLE deployment_maintenance (
    id INTEGER PRIMARY KEY CHECK (id=1),
    enabled BOOLEAN NOT NULL DEFAULT false,
    release_id TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (enabled = (release_id IS NOT NULL))
);
INSERT INTO deployment_maintenance(id) VALUES (1);
