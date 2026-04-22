# Scripts

This directory contains local development and diagnostic helpers.

## Public Setup

- `generate_dev_tls.sh` creates the self-signed local HTTPS certificate used by
  `docker/docker-compose.yml`.

## API Diagnostics

- `chat_bridge_api_test.py` runs an end-to-end chat bridge check against a live
  biMEMO instance.
- `file_context_api_test.py` uploads files and checks the file-context API path.
- `memory_health_test.py` captures memory health before and after a chat run.

## Memory Evaluation

- `memory_loop_test.py` runs the main memory evaluation scenario.
- `memory_stress_test.py` sends a larger memory workload and prints a compact
  pass/fail report.
- `seed_and_test_memory.py` seeds example conversations and checks memory
  retrieval behavior.
- `memory_loop_*.py`, `memory_stress_*.py`, and `seed_memory_*.py` are helper
  modules for those runners.

## Maintenance

- `prepare_db_reset.py` creates a backup before deleting a local development
  database.
- `migrate_clear_num_ctx_4000.py` clears old `num_ctx = 4000` overrides from a
  live encrypted database.

These scripts are intended for development and diagnostics. Most of them expect
a running biMEMO instance and may create, modify, or delete test data.
