# Multiplayer Battleship System

CPSC 3750 -- Web Application Development\
Clemson University

Domain 1 - https://battleship.shaunwhittportfolio.com/

Domain 2 - https://battleship.jackstiversportfolio.com/

------------------------------------------------------------------------

# Project Overview

The Multiplayer Battleship System is a server-managed web application
that allows multiple players to participate in turn-based Battleship
games. The server handles all game logic, including player
participation, move validation, turn enforcement, and game state
tracking.

Players can create or join games, take turns attacking coordinates on
opponent boards, and continue gameplay until only one player remains.
The system ensures that rules are consistently enforced and that game
progress remains synchronized across all players.

The application also supports persistence, allowing game states and
player statistics to remain stored even if the server restarts.

------------------------------------------------------------------------

# Architecture Summary

The application follows a client--server architecture with persistent
storage.

## Client Layer

The client interacts with the game server using HTTP requests. Clients
can create games, join games, and submit moves through API endpoints.

## Server Layer

The server is responsible for:

-   Managing game sessions
-   Enforcing turn order
-   Validating player actions
-   Processing hits and misses
-   Determining player elimination
-   Declaring the game winner

The server maintains the authoritative game state to ensure consistent
gameplay across all participants.

## Database Layer

A relational database stores persistent data including:

-   Active games
-   Player information
-   Game board states
-   Game results
-   Player statistics

This ensures games are not lost if the server restarts and allows
historical data to be tracked.

------------------------------------------------------------------------

# API Description

The multiplayer system exposes a REST-style API for interacting with the
game server.

## Create Game

Creates a new Battleship game session.

POST /games

Response includes a unique game ID and initial game state.

------------------------------------------------------------------------

## Join Game

Allows a player to join an existing game.

POST /games/{gameId}/join

The server assigns the player a unique player ID.

------------------------------------------------------------------------

## Get Game State

Returns the current state of the game including players, turn order, and
board status.

GET /games/{gameId}

------------------------------------------------------------------------

## Make Move

Allows the current player to attack a coordinate on an opponent's board.

POST /games/{gameId}/move

The server validates:

-   Correct player turn
-   Valid coordinates
-   Legal move

The response indicates hit, miss, or ship destruction.

------------------------------------------------------------------------

## Game Completion

When only one player remains with ships, the system automatically
declares the winner and records the result.

------------------------------------------------------------------------

# Team Members

-   Jack Stivers
-   Shaun Whitt

------------------------------------------------------------------------

# AI Tools Used

GPT-5.2

The AI tool was used to assist with:

-   Documentation formatting
-   Development guidance
-   Debugging and troubleshooting
-   Structuring the project README

All final implementation decisions and code integration were completed
by the human team members.

------------------------------------------------------------------------

# Roles and Contributions

## Jack Stivers

-   Testing server functionality
-   Frontend/UI design
-   Developing the Battleship AI
-   Assisted with API development

## Shaun Whitt

-   Implemented database integration
-   Managed persistence of games and statistics
-   Assisted with testing gameplay functionality
-   Deployment setup

## AI (GPT-5.2)

-   Assisted with documentation structure and formatting
-   Provided coding guidance and debugging support
-   Helped generate explanations for system design and architecture
