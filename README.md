# Multiplayer Battleship System - CPSC 3750 – Web Application Development

## Team Members
- Shaun Whitt
- Jack Striver

## Project Overview
This project implements a server-managed multiplayer Battleship system that coordinates players, enforces game rules, and maintains persistent game state. The system allows multiple players to participate in Battleship games where a central server manages gameplay, validates moves, enforces turn order, and tracks game outcomes. The goal of the project is to demonstrate proper API design, persistent storage, multiplayer game logic, and engineering discipline throughout development.

## Architecture Summary
The application follows a client → server → database architecture.

### Client
A client application communicates with the server using HTTP requests.
The client sends requests to perform actions such as joining games or making moves.

### Server
The server contains the core game logic and is responsible for:
- Managing players
- Creating and managing game sessions
- Enforcing turn order
- Validating moves
- Tracking game progress
- Recording game results

### Database
A relational database stores persistent data including:
- Player information
- Game sessions
- Player participation in games
- Game state and move history
- Player statistics

### API Description
The API supports functionality such as:
- Creating players
- Creating game sessions
- Joining games
- Performing game actions
- Retrieving game state
- Tracking player statistics

## AI Tools Used
### The following AI tools were used during development:
- ChatGPT
### AI was used primarily for:
- Assisting with brainstorming architecture ideas
- Explaining technical concepts
- Helping generate example code structures
- Reviewing and improving documentation

## Human + AI Roles
### Human Responsibilities
- Team members were responsible for:
- Defining the product requirements
- Designing the system architecture
- Implementing the application logic
- Writing and debugging code
- Testing functionality
- Ensuring regression discipline
- Managing version control and commits

## AI Responsibilities
### AI tools were used as an assistive resource to:
- Provide explanations of programming concepts
- Suggest potential approaches to problems
- Help refine documentation and structure
