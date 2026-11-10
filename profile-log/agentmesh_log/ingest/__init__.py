"""Transcript ingest and the broker summary (moved from memory-sync/ in 2026-11).

Reads the owner's Claude, Codex and OMP JSONL transcripts, summarizes them in bounded windows with a Claude worker and
writes memories through `agentmesh-log summary related|write`. Personal-only: see personal.py. Standard library only,
so the hourly jobs start fast and never import the broker's crypto stack unless they call the CLI.
"""
