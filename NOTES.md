# NOTES — Decision Log

| # | Date | Decision | Rationale |
|---|------|----------|-----------|
| 1 | 2026-08-01 | Installed latest majors: langgraph 1.2.10, langchain 1.3.14, python-telegram-bot 22.8 | Playbook assumed langgraph 0.2 / langchain 0.3 / ptb 20+; latest installed for security + support. API drift (Send(), interrupt(), with_structured_output) verified against current docs at each prompt |
| 2 | 2026-08-01 | LLM provider = NVIDIA NIM (integrate.api.nvidia.com/v1), model deepseek-ai/deepseek-v4-flash | User's key; benchmarked vs step-3.7-flash (empty output) + nemotron-super-49b (4.5s): deepseek-v4-flash fastest WITH correct output (1.3s) and is the playbook's primary. Key stored in gitignored .env |
| 3 | 2026-08-01 | Challenger threshold lowered 0.9 → 0.8; #402 confidence = 85% pre-challenge → **91%** final (not the PRD's 94%) | TDD revealed TRD's arithmetic is unreachable: with 4 hypotheses + 2 dispatched investigators #402 scores 0.5+0.3×(2/4)+0.2×(4/4)=0.85; 94% requires 3/5 eliminated — impossible. 0.9 bar would skip the Challenger beat. Team approved 0.8. Formula itself unchanged (auditable math). Demo must show 91%, update slide/deck language |
