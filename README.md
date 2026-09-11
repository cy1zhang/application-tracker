# Application Tracker

A planned internship-first application dashboard with Google sign-in, daily Gmail sync, recruiting-cycle filters, and optional bring-your-own-key AI classification.

## Project status

Requirements and architecture draft. Application code and cloud infrastructure have not been implemented or deployed.

- [Product requirements](docs/PRD.md)
- [AWS technical design](docs/TECHNICAL_DESIGN.md)

The planned app also supports co-ops and full-time/new-grad roles. Rules handle clear recruiting emails; OpenAI, Anthropic, or Gemini can assist with ambiguous cases; unresolved cases go to manual review.

## Repository privacy

This repository is public. Do not commit API keys, OAuth credentials, AWS credentials, real email fixtures, or personal application data. Public Gmail onboarding has separate Google verification requirements described in the PRD.
