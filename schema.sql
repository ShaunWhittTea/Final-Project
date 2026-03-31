CREATE TABLE IF NOT EXISTS players (
    player_id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    total_games INT NOT NULL DEFAULT 0,
    total_wins INT NOT NULL DEFAULT 0,
    total_losses INT NOT NULL DEFAULT 0,
    total_shots INT NOT NULL DEFAULT 0,
    total_hits INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS games (
    game_id SERIAL PRIMARY KEY,
    grid_size INT NOT NULL CHECK (grid_size BETWEEN 5 AND 15),
    max_players INT NOT NULL CHECK (max_players >= 1),
    status TEXT NOT NULL DEFAULT 'waiting',
    current_turn_index INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS game_players (
    game_id INT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    player_id INT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    turn_order INT NOT NULL,
    PRIMARY KEY (game_id, player_id),
    UNIQUE (game_id, turn_order)
);

CREATE TABLE IF NOT EXISTS ships (
    game_id INT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    player_id INT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    row INT NOT NULL,
    col INT NOT NULL,
    hit BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (game_id, player_id, row, col)
);

CREATE TABLE IF NOT EXISTS moves (
    move_id SERIAL PRIMARY KEY,
    game_id INT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    player_id INT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    row INT NOT NULL,
    col INT NOT NULL,
    result TEXT NOT NULL
);
