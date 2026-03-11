CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS games (
    game_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(20) NOT NULL DEFAULT 'waiting'
        CHECK (status IN ('waiting', 'active', 'completed')),
    grid_size INT NOT NULL DEFAULT 10,
    max_players INT NOT NULL DEFAULT 2,
    current_turn_index INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE games
    ADD COLUMN IF NOT EXISTS grid_size INT NOT NULL DEFAULT 10;

ALTER TABLE games
    ADD COLUMN IF NOT EXISTS max_players INT NOT NULL DEFAULT 2;

ALTER TABLE games
    ADD COLUMN IF NOT EXISTS current_turn_index INT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS players (
    player_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    total_games INT NOT NULL DEFAULT 0,
    total_wins INT NOT NULL DEFAULT 0,
    total_losses INT NOT NULL DEFAULT 0,
    total_moves INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS game_players (
    game_id UUID NOT NULL,
    player_id UUID NOT NULL,
    turn_order INT NOT NULL DEFAULT 0,
    joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (game_id, player_id),

    CONSTRAINT fk_game_players_game
        FOREIGN KEY (game_id)
        REFERENCES games(game_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_game_players_player
        FOREIGN KEY (player_id)
        REFERENCES players(player_id)
        ON DELETE CASCADE
);

ALTER TABLE game_players
    ADD COLUMN IF NOT EXISTS turn_order INT NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'unique_turn_order_per_game'
    ) THEN
        ALTER TABLE game_players
        ADD CONSTRAINT unique_turn_order_per_game
        UNIQUE (game_id, turn_order);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS ships (
    ship_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID NOT NULL,
    player_id UUID NOT NULL,
    ship_type VARCHAR(50) NOT NULL DEFAULT 'single',
    coordinates JSONB NOT NULL,
    row_index INT,
    col_index INT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_ship_game
        FOREIGN KEY (game_id)
        REFERENCES games(game_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_ship_player
        FOREIGN KEY (player_id)
        REFERENCES players(player_id)
        ON DELETE CASCADE
);

ALTER TABLE ships
    ADD COLUMN IF NOT EXISTS row_index INT;

ALTER TABLE ships
    ADD COLUMN IF NOT EXISTS col_index INT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'unique_ship_cell_per_player'
    ) THEN
        ALTER TABLE ships
        ADD CONSTRAINT unique_ship_cell_per_player
        UNIQUE (game_id, player_id, row_index, col_index);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS shots (
    shot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID NOT NULL,
    attacker_player_id UUID NOT NULL,
    target_player_id UUID NOT NULL,
    row_index INT NOT NULL,
    col_index INT NOT NULL,
    result VARCHAR(20) NOT NULL CHECK (result IN ('hit', 'miss')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_shot_game
        FOREIGN KEY (game_id)
        REFERENCES games(game_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_shot_attacker
        FOREIGN KEY (attacker_player_id)
        REFERENCES players(player_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_shot_target
        FOREIGN KEY (target_player_id)
        REFERENCES players(player_id)
        ON DELETE CASCADE
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'unique_shot_per_cell'
    ) THEN
        ALTER TABLE shots
        ADD CONSTRAINT unique_shot_per_cell
        UNIQUE (game_id, target_player_id, row_index, col_index);
    END IF;
END $$;
