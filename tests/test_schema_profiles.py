from app.schema import build_empty_fields, resolve_schema_name


def field_map(document_type: str) -> dict[str, tuple[str, bool]]:
    return {field.field_name: (field.label, field.required) for field in build_empty_fields(document_type)}


def test_resolve_schema_name_defaults_to_tax_invoice() -> None:
    assert resolve_schema_name("") == "전자세금계산서"
    assert resolve_schema_name("알수없음") == "전자세금계산서"


def test_contract_schema_relaxes_tax_and_biz_number_requirements() -> None:
    fields = field_map("외부용역계약서")
    assert fields["issue_date"] == ("계약일자", True)
    assert fields["supply_amount"] == ("계약금액", True)
    assert fields["tax_amount"] == ("세액", False)
    assert fields["supplier_biz_no"] == ("공급자 사업자번호", False)


def test_quote_schema_uses_quote_labels() -> None:
    fields = field_map("일반견적서")
    assert fields["issue_date"] == ("견적일자", True)
    assert fields["tax_amount"] == ("세액", False)
    assert fields["item_name"] == ("견적 항목", False)
