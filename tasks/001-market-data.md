# Task 001 — Market Data Abstraction

## Objective
Create a provider-neutral market-data interface.

## Providers
- Binance
- OKX

## Requirements
- Read-only public market data first
- Symbol discovery
- Ticker
- OHLCV
- Order book
- Funding where available
- Explicit provider error handling
- No order placement

## Acceptance
- Provider interface has tests
- Mock provider tests pass
- No API secret required for public market data
