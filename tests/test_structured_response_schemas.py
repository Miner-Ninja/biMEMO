"""Tests for structured-response schema constants.

Covers the shape and completeness of SKILL_EXTRACTION_SCHEMA and its
OpenAI-format wrapper introduced alongside the existing default
structured-response schema.
"""

from backend.llm.structured_response import (
    OPENAI_SKILL_EXTRACTION_FORMAT,
    SKILL_EXTRACTION_SCHEMA,
    SKILL_EXTRACTION_SCHEMA_NAME,
)


class TestSkillExtractionSchema:
    """Tests for the raw JSON schema describing a skill extraction result."""

    def test_schema_is_object_type(self):
        """Verify the top-level schema declares type: object."""
        assert SKILL_EXTRACTION_SCHEMA["type"] == "object"

    def test_schema_declares_additional_properties_false(self):
        """Verify additional fields are rejected (strict mode compatible)."""
        assert SKILL_EXTRACTION_SCHEMA["additionalProperties"] is False

    def test_schema_required_fields_cover_all_skill_fields(self):
        """Verify every skill field is marked required."""
        required = SKILL_EXTRACTION_SCHEMA["required"]
        assert set(required) == {
            "title",
            "description",
            "trigger",
            "procedure",
            "errors",
            "verification",
        }

    def test_schema_exposes_string_title(self):
        """Verify title is a string property."""
        properties = SKILL_EXTRACTION_SCHEMA["properties"]
        assert properties["title"]["type"] == "string"

    def test_schema_exposes_string_description(self):
        """Verify description is a string property."""
        properties = SKILL_EXTRACTION_SCHEMA["properties"]
        assert properties["description"]["type"] == "string"

    def test_schema_exposes_string_trigger(self):
        """Verify trigger is a string property."""
        properties = SKILL_EXTRACTION_SCHEMA["properties"]
        assert properties["trigger"]["type"] == "string"

    def test_schema_exposes_string_verification(self):
        """Verify verification is a string property."""
        properties = SKILL_EXTRACTION_SCHEMA["properties"]
        assert properties["verification"]["type"] == "string"

    def test_schema_procedure_is_array_of_objects(self):
        """Verify procedure is an array of step objects."""
        procedure = SKILL_EXTRACTION_SCHEMA["properties"]["procedure"]
        assert procedure["type"] == "array"
        assert procedure["items"]["type"] == "object"

    def test_schema_procedure_item_requires_tool_and_query(self):
        """Verify each procedure step has tool/query/note required fields."""
        item = SKILL_EXTRACTION_SCHEMA["properties"]["procedure"]["items"]
        assert set(item["required"]) == {"tool", "query", "note"}
        assert item["additionalProperties"] is False

    def test_schema_errors_is_array_of_objects(self):
        """Verify errors is an array of error objects."""
        errors = SKILL_EXTRACTION_SCHEMA["properties"]["errors"]
        assert errors["type"] == "array"
        assert errors["items"]["type"] == "object"

    def test_schema_errors_item_requires_name_and_fix(self):
        """Verify each error item has name/fix required fields."""
        item = SKILL_EXTRACTION_SCHEMA["properties"]["errors"]["items"]
        assert set(item["required"]) == {"name", "fix"}
        assert item["additionalProperties"] is False


class TestOpenAISkillExtractionFormat:
    """Tests for the OpenAI response_format wrapper around the skill schema."""

    def test_wrapper_declares_json_schema_type(self):
        """Verify the wrapper uses type: json_schema."""
        assert OPENAI_SKILL_EXTRACTION_FORMAT["type"] == "json_schema"

    def test_wrapper_carries_schema_name(self):
        """Verify the wrapper exposes the schema name."""
        wrapper = OPENAI_SKILL_EXTRACTION_FORMAT["json_schema"]
        assert wrapper["name"] == SKILL_EXTRACTION_SCHEMA_NAME

    def test_wrapper_requests_strict_mode(self):
        """Verify the wrapper requests OpenAI strict validation."""
        wrapper = OPENAI_SKILL_EXTRACTION_FORMAT["json_schema"]
        assert wrapper["strict"] is True

    def test_wrapper_embeds_the_skill_schema(self):
        """Verify the wrapper schema field is exactly SKILL_EXTRACTION_SCHEMA."""
        wrapper = OPENAI_SKILL_EXTRACTION_FORMAT["json_schema"]
        assert wrapper["schema"] is SKILL_EXTRACTION_SCHEMA
