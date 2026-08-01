# NOTES — Decision Log

| # | Date | Decision | Rationale |
|---|------|----------|-----------|
| 1 | 2026-08-01 | Installed latest majors: langgraph 1.2.10, langchain 1.3.14, python-telegram-bot 22.8 | Playbook assumed langgraph 0.2 / langchain 0.3 / ptb 20+; latest installed for security + support. API drift (Send(), interrupt(), with_structured_output) verified against current docs at each prompt |
| 2 | 2026-08-01 | LLM provider = NVIDIA NIM (integrate.api.nvidia.com/v1), model deepseek-ai/deepseek-v4-flash | User's key; benchmarked vs step-3.7-flash (empty output) + nemotron-super-49b (4.5s): deepseek-v4-flash fastest WITH correct output (1.3s) and is the playbook's primary. Key stored in gitignored .env |
