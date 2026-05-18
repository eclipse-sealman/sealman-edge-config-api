# Testing

## Prerequisites

Install development dependencies:
```bash
pip install -r requirements-dev.txt
```

## Run tests

```bash
pytest
```

## Run with coverage report

```bash
pytest --cov=. --cov-report=term-missing
```

The report can then be found in `htmlcov` folder in the root directory.

## Test layout
| File | Covers |
|---|---|
| `test_helper.py` | Helper utilities |
| `test_smart_ems.py` | SmartEMS integration |
| `conftest.py` | Shared fixtures |
