from fastapi import HTTPException

from app.api.routes import conversations


def test_accepts_openai_maximum_of_sixteen_reference_images() -> None:
    validator = getattr(conversations, "validate_reference_count", None)

    assert callable(validator)
    validator(16)


def test_rejects_more_than_sixteen_reference_images() -> None:
    validator = getattr(conversations, "validate_reference_count", None)

    assert callable(validator)
    try:
        validator(17)
    except HTTPException as exc:
        assert exc.status_code == 422
        assert exc.detail == "参考图最多支持 16 张"
    else:
        raise AssertionError("17 reference images must be rejected")
