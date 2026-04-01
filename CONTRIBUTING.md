# Contributing to OpenLucid

Thanks for your interest in contributing! This guide will help you get started.

## Prerequisites

- Python 3.11+
- PostgreSQL 16+
- Node.js is **not** required (the frontend is plain HTML/JS)

## Local Development

```bash
# 1. Clone the repo
git clone https://github.com/agidesigner/OpenLucid.git
cd openlucid

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up the database
#    Create a PostgreSQL database named "openlucid" (default connection string is in app/config.py)

# 5. Run the app
uvicorn app.main:app --reload

# 6. Run tests
pytest tests/ -v
```

## Pull Request Process

1. Fork the repository and create a feature branch from `main`.
2. Keep changes focused — one concern per PR.
3. Add or update tests if your change affects behavior.
4. Ensure `pytest tests/ -v` passes before submitting.
5. Open a PR against `main` with a clear description of the change.

## Code Style

- Follow the existing patterns in the codebase.
- No linter is enforced yet — just keep it consistent with surrounding code.

## License & Contributor Agreement

By submitting a pull request, you agree that:

1. Your contributions are licensed under the same [LICENSE](LICENSE) as the project.
2. The project maintainer may use your contributed code for commercial purposes, including but not limited to cloud-hosted services (see LICENSE Section 2.b).
3. The project maintainer may adjust the open-source license terms as needed (see LICENSE Section 2.a).
