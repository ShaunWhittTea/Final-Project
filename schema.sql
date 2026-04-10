CREATE TABLE IF NOT EXISTS players (
    player_id SERIAL PRIMARY KEY,
    username VARCHAR(30) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    total_games INT NOT NULL DEFAULT 0,
    total_wins INT NOT NULL DEFAULT 0,
    total_losses INT NOT NULL DEFAULT 0,
    total_moves INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS games (
    game_id SERIAL PRIMARY KEY,
    status VARCHAR(20) NOT NULL DEFAULT 'waiting_setup'
        CHECK (status IN ('waiting_setup', 'playing', 'finished')),
    grid_size INT NOT NULL DEFAULT 8
        CHECK (grid_size BETWEEN 5 AND 15),
    max_players INT NOT NULL DEFAULT 2
        CHECK (max_players BETWEEN 2 AND 10),
    current_turn_index INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS game_players (
    game_id INT NOT NULL,
    player_id INT NOT NULL,
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
        ON DELETE CASCADE,
    CONSTRAINT unique_turn_order_per_game
        UNIQUE (game_id, turn_order)
);

CREATE TABLE IF NOT EXISTS ships (
    ship_id SERIAL PRIMARY KEY,
    game_id INT NOT NULL,
    player_id INT NOT NULL,
    ship_type VARCHAR(50) NOT NULL DEFAULT 'single',
    coordinates JSONB NOT NULL,
    row_index INT NOT NULL,
    col_index INT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ship_game
        FOREIGN KEY (game_id)
        REFERENCES games(game_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ship_player
        FOREIGN KEY (player_id)
        REFERENCES players(player_id)
        ON DELETE CASCADE,
    CONSTRAINT unique_ship_cell_per_player
        UNIQUE (game_id, player_id, row_index, col_index)
);

CREATE TABLE IF NOT EXISTS shots (
    shot_id SERIAL PRIMARY KEY,
    game_id INT NOT NULL,
    attacker_player_id INT NOT NULL,
    target_player_id INT NOT NULL,
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

ALTER TABLE shots
    DROP CONSTRAINT IF EXISTS unique_shot_per_target_cell;

ALTER TABLE shots
    DROP CONSTRAINT IF EXISTS unique_shot_per_game_cell;

ALTER TABLE shots
    ADD CONSTRAINT unique_shot_per_game_cell
        UNIQUE (game_id, row_index, col_index);
