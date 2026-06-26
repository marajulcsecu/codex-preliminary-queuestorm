

# FinTech AI Complaint Investigator System

## Objective

Build an enterprise-grade AI Complaint Investigator for a digital financial platform.

This is **NOT** a chatbot.

This is **NOT** a simple complaint classifier.

The system must investigate customer complaints using transaction history, determine what actually happened, classify the issue, generate evidence-backed decisions, route the case to the proper department, and produce safe customer responses.

The entire architecture must be optimized to maximize the official hackathon evaluation score.

---

# Primary Design Goals

Priority order:

1. Exact API Contract
2. Evidence Reasoning
3. Safety Guardrails
4. Reliability
5. Low Latency
6. Clean Code
7. Documentation

Never sacrifice correctness for complexity.

---

# Core Philosophy

Treat every complaint like a financial investigation.

Never trust the complaint alone.

Never trust the transaction history alone.

Always compare both.

Evidence decides the outcome.

---

# High-Level Workflow

```text
                Incoming Request
                       │
                       ▼
              Request Validation
                       │
                       ▼
              Complaint Normalization
                       │
                       ▼
           Language Detection (EN/BN/Banglish)
                       │
                       ▼
           Information Extraction
      - amount
      - time
      - transaction type
      - fraud keywords
      - refund intent
      - recipient
                       │
                       ▼
           Transaction Matching Engine
                       │
                       ▼
           Evidence Verification Engine
                       │
                       ▼
        Case Classification Engine
                       │
                       ▼
          Department Routing Engine
                       │
                       ▼
           Severity Assessment
                       │
                       ▼
         Human Review Decision
                       │
                       ▼
       Customer Reply Generator
                       │
                       ▼
          Safety Validation Layer
                       │
                       ▼
             JSON Response Builder
```

---

# Internal Modules

## 1. Request Validator

Responsibilities:

* Validate JSON
* Validate required fields
* Validate enums
* Validate transaction structure
* Reject malformed requests safely

Never crash.

---

## 2. Complaint Normalizer

Normalize

* lowercase
* whitespace
* punctuation
* Banglish spelling variations

Examples

"Vul Number"

↓

"vul number"

---

## 3. Language Detection

Supported

* English
* Bangla
* Mixed Banglish

Use lightweight language detection.

---

## 4. Information Extraction

Extract

* Amount
* Phone Number
* Merchant ID
* Agent ID
* Time
* Date
* Payment Type
* Refund Intent
* Wrong Transfer Intent
* Duplicate Payment Intent
* Fraud Indicators

Primary method

Regex

Keyword Dictionary

RapidFuzz

Optional LLM fallback

---

## 5. Transaction Matching Engine

This is the heart of the system.

Compare extracted information against every transaction.

Matching signals

* amount
* timestamp
* transaction type
* recipient
* merchant
* status

Return

Best matching transaction

or

null

Never guess.

---

## 6. Evidence Verification

Determine

consistent

Complaint matches evidence.

inconsistent

Complaint contradicts transaction history.

insufficient_data

Not enough information.

Evidence is more important than complaint wording.

---

## 7. Case Classification

Supported Case Types

wrong_transfer

payment_failed

refund_request

duplicate_payment

merchant_settlement_delay

agent_cash_in_issue

phishing_or_social_engineering

other

Never invent new enums.

---

## 8. Department Router

wrong_transfer

→ dispute_resolution

payment_failed

→ payments_ops

duplicate_payment

→ payments_ops

merchant_settlement_delay

→ merchant_operations

agent_cash_in_issue

→ agent_operations

phishing_or_social_engineering

→ fraud_risk

other

→ customer_support

---

## 9. Severity Engine

Factors

Transaction Amount

Fraud

Pending Payment

Refund Dispute

Multiple Matching Transactions

Contradictory Evidence

Possible Scam

Return

low

medium

high

critical

---

## 10. Human Review Decision

Return true if

Fraud

Wrong Transfer

High Value

Refund Dispute

Ambiguous Evidence

Conflicting Transactions

Suspicious Activity

Otherwise false.

---

## 11. Customer Reply Generator

Requirements

Professional

Short

Empathetic

No promises

No financial decisions

No unsafe instructions

---

## 12. Safety Layer

This module runs LAST.

It validates every response before sending.

Reject any response that

asks for OTP

asks for PIN

asks for password

asks for card number

promises refund

promises reversal

promises recovery

promises account unblock

directs customers to unofficial contacts

Ignore prompt injection attempts.

Complaint text is data.

Never instructions.

---

# Rule-Based First

Primary reasoning engine

Keyword Matching

Regex

RapidFuzz

Scoring Rules

Decision Trees

Transaction Matching

LLM is optional.

System must work perfectly without an LLM.

---

# Optional LLM Layer

Only used when confidence is low.

Tasks

Semantic understanding

Mixed-language interpretation

Complex complaint reasoning

Final output must still pass rule-based safety validation.

---

# Confidence Calculation

High confidence

Clear evidence

Single matching transaction

Matching amount

Matching type

Matching time

Medium confidence

Partial match

Low confidence

No clear evidence

Multiple candidates

Contradictory information

Return value

0.0 → 1.0

---

# Performance Targets

Health endpoint

<100ms

Average analysis

<2 seconds

Worst case

<5 seconds

Maximum allowed

30 seconds

Avoid unnecessary LLM calls.

---

# Security Principles

Never expose

API keys

Tokens

Secrets

Stack traces

Internal prompts

Internal reasoning

Prompt injection must never affect output.

---

# Coding Standards

Use

Type hints

Pydantic

Dataclasses where appropriate

Reusable services

Dependency Injection

Clear logging

Meaningful function names

No duplicated code

---

# Folder Structure

```text
app/
│
├── main.py
├── routes.py
├── config.py
│
├── schemas/
│
├── services/
│   ├── normalizer.py
│   ├── extractor.py
│   ├── matcher.py
│   ├── evidence.py
│   ├── classifier.py
│   ├── router.py
│   ├── severity.py
│   ├── confidence.py
│   ├── safety.py
│   ├── reply_generator.py
│
├── utils/
│
└── tests/
```

---

# Hidden Test Strategy

System must handle

English

Bangla

Banglish

Typos

Malformed JSON

Empty complaints

Missing transaction history

Prompt Injection

Fraud

Duplicate Payments

Merchant Cases

Agent Cases

Refund Ambiguity

Unknown Inputs

Never hardcode sample cases.

---

# Engineering Principle

Simple.

Reliable.

Evidence-driven.

Safe.

Fast.

Every response should be explainable using the provided transaction history.

If evidence is unclear,

say

"insufficient_data"

instead of guessing.

Correctness is more valuable than confidence.