-- Add endless hunting, owned cosmetic skins, an auditable inventory ledger and fixed shop.
ALTER TABLE game_virtual_users
    ALTER COLUMN experience_total TYPE BIGINT,
    ALTER COLUMN highest_stage_order TYPE BIGINT,
    ALTER COLUMN current_hunt_stage_order TYPE BIGINT,
    ALTER COLUMN weapon_upgrade_level TYPE BIGINT,
    ALTER COLUMN armor_upgrade_level TYPE BIGINT,
    ADD COLUMN IF NOT EXISTS highest_dungeon_floor BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS current_dungeon_floor BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS dungeon_hunt_mode TEXT NOT NULL DEFAULT 'push',
    ADD COLUMN IF NOT EXISTS dungeon_success_count BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS equipped_skin_id TEXT;

ALTER TABLE game_inventory_balances
    ALTER COLUMN quantity TYPE BIGINT;
ALTER TABLE game_reward_records
    ALTER COLUMN quantity TYPE BIGINT;
ALTER TABLE game_growth_records
    ALTER COLUMN quantity_spent TYPE BIGINT,
    ALTER COLUMN upgrade_level_before TYPE BIGINT,
    ALTER COLUMN upgrade_level_after TYPE BIGINT;

CREATE TABLE IF NOT EXISTS game_skin_ownerships (
    user_id TEXT NOT NULL REFERENCES game_virtual_users(user_id) ON DELETE RESTRICT,
    skin_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, skin_id),
    CONSTRAINT uq_game_skin_acquisition UNIQUE (source_type, source_id, skin_id)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_game_user_equipped_owned_skin'
    ) THEN
        ALTER TABLE game_virtual_users
            ADD CONSTRAINT fk_game_user_equipped_owned_skin
            FOREIGN KEY (user_id, equipped_skin_id)
            REFERENCES game_skin_ownerships(user_id, skin_id)
            ON DELETE RESTRICT;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS game_inventory_ledger (
    entry_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES game_virtual_users(user_id) ON DELETE RESTRICT,
    item_id TEXT NOT NULL,
    delta BIGINT NOT NULL CHECK (delta <> 0),
    balance_before BIGINT NOT NULL CHECK (balance_before >= 0),
    balance_after BIGINT NOT NULL CHECK (balance_after >= 0),
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_game_inventory_source UNIQUE (source_type, source_id, item_id),
    CONSTRAINT ck_game_inventory_delta CHECK (balance_after = balance_before + delta)
);
CREATE INDEX IF NOT EXISTS ix_game_inventory_ledger_user_time
    ON game_inventory_ledger(user_id, occurred_at DESC, entry_id DESC);

CREATE TABLE IF NOT EXISTS game_shop_purchases (
    purchase_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES game_virtual_users(user_id) ON DELETE RESTRICT,
    request_id TEXT NOT NULL,
    offer_id TEXT NOT NULL,
    skin_id TEXT NOT NULL,
    price_item_id TEXT NOT NULL,
    price BIGINT NOT NULL CHECK (price > 0),
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_game_shop_user_request UNIQUE (user_id, request_id),
    CONSTRAINT fk_game_shop_owned_skin FOREIGN KEY (user_id, skin_id)
        REFERENCES game_skin_ownerships(user_id, skin_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_game_shop_user_time
    ON game_shop_purchases(user_id, created_at DESC);

-- Reconstruct pre-ledger grant and growth history for the inventory screen.
WITH raw_changes AS (
    SELECT user_id, item_id, quantity::BIGINT AS delta, grant_source_type AS source_type,
           source_id, created_at AS occurred_at
    FROM game_reward_records
    UNION ALL
    SELECT user_id, item_id, -quantity_spent::BIGINT AS delta, 'growth' AS source_type,
           request_id AS source_id, created_at AS occurred_at
    FROM game_growth_records
), net_changes AS (
    SELECT user_id, item_id, SUM(delta) AS net_delta, MIN(occurred_at) AS first_change
    FROM raw_changes
    GROUP BY user_id, item_id
), changes AS (
    SELECT * FROM raw_changes
    UNION ALL
    SELECT b.user_id, b.item_id, b.quantity - COALESCE(n.net_delta, 0),
           'opening_balance', 'opening:' || b.user_id || ':' || b.item_id,
           COALESCE(n.first_change - INTERVAL '1 microsecond', u.created_at)
    FROM game_inventory_balances b
    JOIN game_virtual_users u ON u.user_id = b.user_id
    LEFT JOIN net_changes n ON n.user_id = b.user_id AND n.item_id = b.item_id
    WHERE b.quantity - COALESCE(n.net_delta, 0) > 0
), running AS (
    SELECT *, SUM(delta) OVER (
        PARTITION BY user_id, item_id ORDER BY occurred_at, source_type, source_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS balance_after
    FROM changes
)
INSERT INTO game_inventory_ledger (
    entry_id, user_id, item_id, delta, balance_before, balance_after,
    source_type, source_id, occurred_at
)
SELECT 'legacy-' || md5(source_type || ':' || source_id || ':' || item_id),
       user_id, item_id, delta, balance_after - delta, balance_after,
       source_type, source_id, occurred_at
FROM running
ON CONFLICT (source_type, source_id, item_id) DO NOTHING;
