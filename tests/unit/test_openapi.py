from app.core.openapi import TAGS, VERSION
from app.main import app


def test_openapi_metadata_and_security():
    schema = app.openapi()
    assert schema["info"]["version"] == VERSION == "1.0.0"
    assert schema["info"]["contact"]["name"]
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["bearerFormat"] == "JWT"
    declared = {t["name"] for t in TAGS}
    used = {tag for ops in schema["paths"].values() for op in ops.values() for tag in op.get("tags", [])}
    assert used <= declared  # every route is grouped under a documented tag
    assert all(t["description"] for t in TAGS)
    login = schema["paths"]["/api/v1/auth/login"]["post"]
    assert "security" not in login
    assert schema["paths"]["/api/v1/staff/films"]["get"]["security"] == [{"HTTPBearer": []}]
    assert app.openapi() is schema  # cached
