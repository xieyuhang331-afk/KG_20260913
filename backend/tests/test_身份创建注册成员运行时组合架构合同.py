import ast
from pathlib import Path

from app import composition


BACKEND_ROOT = Path(__file__).resolve().parents[1]
COMPOSITION_PATH = BACKEND_ROOT / "app" / "composition" / "identity_persistence.py"
APPLICATION_PATH = (
    BACKEND_ROOT
    / "app"
    / "modules"
    / "member"
    / "application"
    / "create_registration_member.py"
)


def _factory_node() -> ast.FunctionDef:
    tree = ast.parse(COMPOSITION_PATH.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "create_registration_member_service"
    )


def test_运行时组合工厂只装配既有依赖且没有副作用():
    node = _factory_node()
    called_names = {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }

    assert called_names == {"CreateRegistrationMemberService"}
    assert {argument.arg for argument in node.args.kwonlyargs} == {
        "identity_persistence",
        "uuid_generator",
    }


def test_运行时组合只从组合包导出且应用层不反向依赖():
    assert callable(composition.create_registration_member_service)
    assert "create_registration_member_service" in composition.__all__
    assert "app.composition" not in APPLICATION_PATH.read_text(encoding="utf-8")


def test_运行时组合没有接入现有注册API或创建公开成员API():
    protected_paths = [
        BACKEND_ROOT / "app" / "modules" / "auth" / "service.py",
        BACKEND_ROOT / "app" / "modules" / "auth" / "api.py",
        BACKEND_ROOT / "app" / "main.py",
    ]
    protected_source = "\n".join(
        path.read_text(encoding="utf-8") for path in protected_paths
    )

    assert "create_registration_member_service" not in protected_source
    assert "CreateRegistrationMemberService" not in protected_source
    assert 'post("/members")' not in protected_source
