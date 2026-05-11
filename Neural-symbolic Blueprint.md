# Neural-symbolic Blueprint

## Purpose

This document defines a future neural-symbolic path for SCD Core.

The goal is to combine learned pattern recognition with strict symbolic rules so the system stays flexible without losing operational control.

## Core Idea

Neural components are good at:

- extracting intent from messy text
- clustering similar tickets
- ranking possible modules
- estimating uncertainty

Symbolic components are good at:

- enforcing hard business rules
- validating required fields
- blocking unsafe paths
- guaranteeing deterministic outputs

## Target Architecture

1. Neural layer reads ticket text and proposes structured signals.
2. Symbolic layer validates those signals against explicit rules.
3. Orchestrator accepts only outputs that pass rule checks.
4. Final action is recorded with both learned and rule-based evidence.

## Neural Layer Examples

- module ranking
- intent extraction
- anomaly detection
- semantic similarity search
- confidence scoring

## Symbolic Layer Examples

- assignee requirements
- allowed module lists
- ticket state requirements
- gatekeeper safety checks
- execution ordering rules

## Good Future Use Cases

- learned routing with hard routing constraints
- learned confidence with fixed approval thresholds
- learned clustering with rule-based execution gating
- learned summaries with strict output templates

## Design Rule

If the neural output conflicts with a required symbolic rule, the symbolic rule wins.

## Stage Goal

Neural-symbolic design should let SCD Core get smarter while staying inspectable, auditable, and safe to operate.