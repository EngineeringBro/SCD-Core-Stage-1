# Machine Learning Blueprint

## Purpose

This document defines a future machine learning path for SCD Core.

The goal is to let the system learn from large volumes of historical Jira tickets and improve routing, classification, prioritization, and recommendation quality over time.

## Core Objective

Use historical closed-ticket data to train models that can:

- classify ticket type
- predict the best handling module
- estimate confidence
- detect common resolution patterns
- surface likely next actions

## Candidate Inputs

- ticket summary
- ticket description
- comments
- reporter email and domain
- client name
- support level
- resolution outcome
- historical handling labels

## Candidate Outputs

- predicted module
- predicted intent category
- predicted priority band
- confidence score
- recommended action label

## Pipeline Shape

1. Ingest closed-ticket history.
2. Clean and normalize ticket text.
3. Build labeled training datasets.
4. Train baseline classifiers.
5. Evaluate against held-out ticket sets.
6. Deploy models as decision support, not as the only control path.
7. Monitor drift and retrain on new data.

## Good Early Use Cases

- module pre-routing
- spam probability scoring
- notification-type clustering
- claim-type clustering
- payment-related issue detection
- confidence calibration

## Guardrails

- keep deterministic fallbacks
- never skip validation based only on model output
- log predictions and outcomes for future retraining
- prefer explainable outputs for operational workflows

## Stage Goal

Machine learning should improve speed and accuracy without replacing the system's explicit execution rules.