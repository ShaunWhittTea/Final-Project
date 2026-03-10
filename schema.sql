CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS games (
    game_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(20) NOT NULL CHECK (status IN ('waiting', 'active', 'completed')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
    joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (game_id, player_id),
    CONSTRAINT fk_game
        FOREIGN KEY (game_id)
        REFERENCES games(game_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_player
        FOREIGN KEY (player_id)
        REFERENCES players(player_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS moves (
    move_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID NOT NULL,
    player_id UUID NOT NULL,
    move_number INT NOT NULL,
    move_data JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_move_game
        FOREIGN KEY (game_id)
        REFERENCES games(game_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_move_player
        FOREIGN KEY (player_id)
        REFERENCES players(player_id)
        ON DELETE CASCADE
);