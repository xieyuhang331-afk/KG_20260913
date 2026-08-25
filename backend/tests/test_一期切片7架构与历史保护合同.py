from pathlib import Path


ROOT = Path(__file__).parents[1]
MODULE = ROOT / "app/modules/service_fulfillment"


def test_Slice7只通过新模块和受限端口消费既有真相() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in MODULE.glob("*.py"))
    assert "app.modules.member_enrollment" not in source
    assert "app.modules.health_plan" not in source
    assert "app.modules.health_assessment" not in source
    assert "app.modules.private_file" not in source
    assert "SELECT *" not in source.upper()
    assert "DEVICE" not in source
    private_file_api = (
        ROOT / "app/modules/private_file/api.py"
    ).read_text(encoding="utf-8")
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "app.modules.service_fulfillment" not in private_file_api
    assert "slice7_export_download_consumer" in private_file_api
    assert "slice7_export_download_consumer" in main


def test_生产时钟禁止由请求覆盖且导出目录不含内部存储定位() -> None:
    domain = (MODULE / "domain.py").read_text(encoding="utf-8")
    schemas = (MODULE / "schemas.py").read_text(encoding="utf-8")
    assert "SYNTHETIC_CLOCK_FORBIDDEN" in domain
    assert "bucket" not in schemas.lower()
    assert "storage_key" not in schemas.lower()
    assert "ciphertext" not in schemas.lower()
