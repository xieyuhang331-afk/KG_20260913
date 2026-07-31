$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "backend"
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m unittest backend.tests.test_sqlalchemy_mappings backend.tests.test_core_orm_models backend.tests.test_database_alembic backend.tests.test_config_engineering backend.tests.test_app_contract -v
