from scripts.render_submission_cover import render_cover


def test_render_cover_applies_profile_values() -> None:
    template = "<title>{document_title}</title><h1>{product_name}</h1><p>{lead}</p>"
    profile = {
        "document_title": "제안서",
        "product_name": "문서 자동입력 도우미",
        "lead": "반복 문서 입력을 자동화합니다.",
    }

    rendered = render_cover(template, profile)

    assert "<title>제안서</title>" in rendered
    assert "<h1>문서 자동입력 도우미</h1>" in rendered
    assert "반복 문서 입력을 자동화합니다." in rendered
