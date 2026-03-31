CREATE TABLE IF NOT EXISTS players (
    player_id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    total_games INT DEFAULT 0,
    total_wins INT DEFAULT 0,
    total_losses INT DEFAULT 0,
    total_shots INT DEFAULT 0,
    total_hits INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS games (
    game_id SERIAL PRIMARY KEY,
    grid_size INT NOT NULL,
    max_players INT NOT NULL,
    status TEXT DEFAULT 'waiting',
    current_turn_index INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS game_players (
    game_id INT,
    player_id INT,
    turn_order INT,
    PRIMARY KEY (game_id, player_id)
);

CREATE TABLE IF NOT EXISTS ships (
    game_id INT,
    player_id INT,
    row INT,
    col INT,
    hit BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS moves (
    game_id INT,
    player_id INT,
    row INT,
    col INT,
    result TEXT
);
